"""
Public, no-login payment link for buyer invoices.

Buyers don't have accounts of their own in this app (only admin staff and
customers do), so there's nowhere for them to "log in" to pay an
invoice. Instead, the admin's Buyer Invoices screen (see
buyer_invoices/detail.html's "Copy Payment Link" button, wired up via
make_invoice_pay_token below) hands out a signed link the admin can send
by email/WhatsApp - opening it needs no account, but the invoice_id
inside it is cryptographically signed (itsdangerous, using the app's own
SECRET_KEY) so a buyer can't guess or edit the URL to view or pay
someone else's invoice, and the link stops working after LINK_MAX_AGE.

The amount charged is always read fresh from the invoice record on the
server at both "create order" and "verify" time - never trusted from the
browser - see payment_gateway_service.py's module docstring for why.
"""
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, current_app, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.services import buyer_invoice_service, payment_gateway_service, payment_service

pay_bp = Blueprint("pay", __name__, url_prefix="/pay")

# How long a shared payment link stays valid. Generating a fresh one from
# the invoice detail page (any time) is free, so this doesn't need to be
# long-lived.
LINK_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days

_TOKEN_SALT = "buyer-invoice-pay-link"


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_TOKEN_SALT)


def make_invoice_pay_token(invoice_id):
    """Used by app/routes/buyer_invoices.py to build the shareable link."""
    return _serializer().dumps({"invoice_id": invoice_id})


def _invoice_id_from_token(token):
    try:
        data = _serializer().loads(token, max_age=LINK_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("invoice_id")


@pay_bp.route("/invoice/<token>")
def invoice_pay_page(token):
    invoice_id = _invoice_id_from_token(token)
    invoice = current_app.db.buyer_invoices.find_one({"invoice_id": invoice_id}) if invoice_id else None
    if not invoice:
        return render_template("pay/invalid_link.html"), 404

    buyer = current_app.db.buyers.find_one({"buyer_id": invoice["buyer_id"]})

    return render_template(
        "pay/buyer_invoice.html",
        invoice=invoice,
        buyer=buyer,
        token=token,
        dairy_name=current_app.config.get("DAIRY_NAME"),
        razorpay_key_id=current_app.config.get("RAZORPAY_KEY_ID"),
        payments_enabled=payment_gateway_service.gateway_configured(),
    )


@pay_bp.route("/invoice/<token>/create-order", methods=["POST"])
def create_invoice_order(token):
    invoice_id = _invoice_id_from_token(token)
    if not invoice_id:
        return jsonify(error="This payment link is invalid or has expired."), 404

    invoice = current_app.db.buyer_invoices.find_one({"invoice_id": invoice_id})
    if not invoice:
        return jsonify(error="Invoice not found."), 404
    if invoice["remaining_amount"] <= 0:
        return jsonify(error="This invoice is already fully paid."), 400

    order, error = payment_gateway_service.create_order(
        invoice["remaining_amount"],
        receipt=f"inv-{invoice_id}",
        notes={"invoice_id": invoice_id, "buyer_id": invoice["buyer_id"]},
    )
    if error:
        return jsonify(error=error), 400

    return jsonify(
        order_id=order["id"],
        amount=order["amount"],
        key_id=current_app.config["RAZORPAY_KEY_ID"],
        buyer_name=invoice["buyer_name"],
        dairy_name=current_app.config.get("DAIRY_NAME"),
    )


@pay_bp.route("/invoice/<token>/verify", methods=["POST"])
def verify_invoice_order(token):
    invoice_id = _invoice_id_from_token(token)
    if not invoice_id:
        return jsonify(error="This payment link is invalid or has expired."), 404

    data = request.get_json(silent=True) or {}
    verified = payment_gateway_service.verify_payment_signature(
        data.get("razorpay_order_id"),
        data.get("razorpay_payment_id"),
        data.get("razorpay_signature"),
    )
    if not verified:
        return jsonify(error="Payment could not be verified."), 400

    # Idempotency: the webhook (see /pay/webhook, below) can also deliver
    # this same payment, before or after this browser call. Whichever
    # claims the razorpay_payment_id first actually credits the invoice;
    # the other is a harmless no-op success.
    if not payment_gateway_service.claim_payment_event(current_app.db, data.get("razorpay_payment_id")):
        return jsonify(success=True)

    invoice = current_app.db.buyer_invoices.find_one({"invoice_id": invoice_id})
    if not invoice:
        return jsonify(error="Invoice not found."), 404

    amount = invoice["remaining_amount"]
    if amount <= 0:
        # Already settled (e.g. the buyer double-clicked, or staff
        # recorded a manual payment in between) - nothing left to do,
        # but don't show the browser an error after a real charge.
        return jsonify(success=True)

    ok, result = buyer_invoice_service.record_buyer_payment(
        current_app.db,
        invoice_id,
        amount,
        "Online (Razorpay)",
        data.get("razorpay_payment_id"),
        datetime.now(timezone.utc),
    )
    if not ok:
        return jsonify(error=result), 400
    return jsonify(success=True)


# ---------------------------------------------------------------------------
# Webhook: Razorpay -> our server, for either payment flow
# ---------------------------------------------------------------------------
#
# See payment_gateway_service.py's "Webhook" section for why this exists
# alongside the browser-side /verify routes above rather than instead of
# them: this is a backstop for a browser that never made it back to
# /verify (closed tab, lost connection, JS error), not a replacement.
#
# Configure the webhook URL in the Razorpay Dashboard as:
#   https://<your-app-domain>/pay/webhook
# subscribed to at least the "payment.captured" event, and put the secret
# it gives you in RAZORPAY_WEBHOOK_SECRET (.env / Render env vars) - a
# different value from RAZORPAY_KEY_SECRET.


@pay_bp.route("/webhook", methods=["POST"])
def webhook():
    if not payment_gateway_service.webhook_configured():
        # No RAZORPAY_WEBHOOK_SECRET set - refuse rather than accept an
        # unverifiable, unauthenticated request claiming to be Razorpay.
        return jsonify(error="Webhook not configured."), 503

    raw_body = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not payment_gateway_service.verify_webhook_signature(raw_body, signature):
        return jsonify(error="Invalid webhook signature."), 400

    event = request.get_json(silent=True) or {}
    if event.get("event") != "payment.captured":
        # We only act on captured payments; every other subscribed event
        # (failed, refunded, etc.) is acknowledged so Razorpay stops
        # retrying it, but doesn't change anything here.
        return jsonify(status="ignored"), 200

    try:
        payment_entity = event["payload"]["payment"]["entity"]
    except (KeyError, TypeError):
        return jsonify(error="Malformed payload."), 400

    razorpay_payment_id = payment_entity.get("id")
    notes = payment_entity.get("notes") or {}

    if not razorpay_payment_id:
        return jsonify(error="Missing payment id."), 400

    # Same idempotency guard as the browser-side /verify routes - whichever
    # of the webhook or the browser call reaches this payment_id first
    # actually credits it; the other becomes a harmless no-op.
    if not payment_gateway_service.claim_payment_event(current_app.db, razorpay_payment_id):
        return jsonify(status="already processed"), 200

    now = datetime.now(timezone.utc)

    if "invoice_id" in notes:
        invoice = current_app.db.buyer_invoices.find_one({"invoice_id": notes["invoice_id"]})
        if not invoice:
            return jsonify(error="Invoice not found."), 404
        amount = invoice["remaining_amount"]
        if amount > 0:
            buyer_invoice_service.record_buyer_payment(
                current_app.db, notes["invoice_id"], amount, "Online (Razorpay)", razorpay_payment_id, now
            )
    elif "payment_id" in notes:
        payment = current_app.db.payments.find_one({"payment_id": notes["payment_id"]})
        if not payment:
            return jsonify(error="Payment record not found."), 404
        amount = payment["remaining_amount"]
        if amount > 0:
            payment_service.record_payment(
                current_app.db, notes["payment_id"], amount, "Online (Razorpay)", razorpay_payment_id, now
            )
    else:
        # An order created outside these two flows (shouldn't normally
        # happen, since every order this app creates sets one of these
        # notes) - acknowledge it so Razorpay doesn't keep retrying.
        return jsonify(status="unrecognized order, ignored"), 200

    return jsonify(status="ok"), 200
