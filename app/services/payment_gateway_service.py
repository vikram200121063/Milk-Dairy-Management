"""
Thin wrapper around Razorpay's Orders API (test/sandbox mode by default -
see RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET in .env.example). Two flows in
this app use it:
  - Customer portal (app/routes/customer_portal.py): a customer pays one
    of their own pending payment cycles online.
  - Buyer invoice pay links (app/routes/pay.py): a buyer opens a public,
    signed link and pays an outstanding invoice online, without needing
    any login of their own.

Both flows follow Razorpay's standard "Checkout" pattern:
  1. Server creates an Order for the exact amount THE SERVER computed
     from the payment/invoice record - the browser can never submit its
     own amount, which is what stops someone paying, say, 1 rupee
     against a 5,000 rupee due by tampering with the page.
  2. The browser opens Razorpay's Checkout popup with that order_id.
  3. On success, Razorpay hands the browser a
     payment_id/order_id/signature triple.
  4. The browser POSTs those three values back to our server.
  5. The server verifies the signature (cryptographic proof that
     Razorpay, not a forged browser request, produced this result)
     before recording anything as paid.

Gracefully degrades exactly like ai_service.py: gateway_configured()
returns False when no keys are set, and every route that uses this
checks it first - the existing manual "record a payment" forms keep
working completely untouched either way, with or without Razorpay
configured.
"""
import hmac
import hashlib
from datetime import datetime, timezone

# The razorpay package (and, transitively, its `import pkg_resources`
# dependency on setuptools) is only needed for the two functions below
# that actually call Razorpay's SDK (create_order, verify_payment_signature).
# The webhook path (further down this file) is pure stdlib hmac/hashlib and
# never touches this import at all. Wrapping the import means a missing or
# broken razorpay/setuptools install degrades this ONE optional feature -
# gateway_configured() below returns False and every "Pay Now" button
# disappears - instead of crashing the entire app at startup, which is the
# whole point of "gracefully degrades" described above.
try:
    import razorpay
except ImportError:
    razorpay = None

from flask import current_app
from pymongo.errors import DuplicateKeyError


def gateway_configured():
    cfg = current_app.config
    return bool(razorpay and cfg.get("RAZORPAY_KEY_ID") and cfg.get("RAZORPAY_KEY_SECRET"))


def _client():
    cfg = current_app.config
    return razorpay.Client(auth=(cfg["RAZORPAY_KEY_ID"], cfg["RAZORPAY_KEY_SECRET"]))


def create_order(amount_rupees, receipt, notes=None):
    """
    amount_rupees: a rupee amount (e.g. 1234.50), as already stored on
    the payment/invoice document - always computed server-side, never
    taken from the browser.

    Razorpay's API wants the amount in paise (an integer, the smallest
    currency unit) - handling that conversion once, here, avoids it
    being a silent x100 bug at every call site.

    Returns (order_dict, error_message). On success, error_message is
    None and order_dict has at least "id" and "amount".
    """
    if not gateway_configured():
        return None, "Online payments aren't set up for this dairy yet."

    try:
        amount_paise = int(round(float(amount_rupees) * 100))
    except (TypeError, ValueError):
        return None, "Invalid amount."
    if amount_paise <= 0:
        return None, "There's nothing due to pay."

    try:
        order = _client().order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": notes or {},
            }
        )
        return order, None
    except Exception as exc:  # razorpay.errors.BadRequestError / ServerError / network issues
        return None, f"Could not start the payment: {exc}"


def verify_payment_signature(order_id, payment_id, signature):
    """
    Cryptographically confirms Razorpay (not a tampered browser request)
    actually produced this order_id/payment_id/signature triple, using
    RAZORPAY_KEY_SECRET. Callers must treat a "payment succeeded"
    message from the browser as untrusted until this returns True.
    """
    if not gateway_configured():
        return False
    if not (order_id and payment_id and signature):
        return False
    try:
        _client().utility.verify_payment_signature(
            {
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            }
        )
        return True
    except razorpay.errors.SignatureVerificationError:
        return False
    except Exception:
        # Any other unexpected error (network, malformed input, etc.) -
        # never treat an unverifiable payment as verified.
        return False


# ---------------------------------------------------------------------------
# Webhook (server-to-server) support
# ---------------------------------------------------------------------------
#
# The browser-side "verify" call (pay.py / customer_portal.py) covers the
# normal case, but it depends on the buyer/customer's browser staying open
# and actually making that second request. If they close the tab, lose
# their connection, or the JS errors out right after paying, Razorpay still
# captured real money but our database would never find out. A webhook -
# Razorpay's server calling ours directly the moment a payment is captured
# - closes that gap; it doesn't replace the browser flow, it backs it up.
#
# Configure it at Razorpay Dashboard -> Settings -> Webhooks, pointing at
# this app's /pay/webhook, subscribed to at least the "payment.captured"
# event, and set RAZORPAY_WEBHOOK_SECRET in .env to the secret shown there
# (a different value from RAZORPAY_KEY_SECRET - Razorpay generates one per
# webhook endpoint you register).


def webhook_configured():
    return bool(current_app.config.get("RAZORPAY_WEBHOOK_SECRET"))


def verify_webhook_signature(raw_body, signature):
    """
    Razorpay signs each webhook delivery with HMAC-SHA256 of the exact raw
    request body, using the webhook secret - hex-encoded, sent as the
    X-Razorpay-Signature header. This is computed directly with the
    standard library rather than the razorpay SDK, since it's a documented,
    simple formula and doesn't depend on the SDK having webhook support.

    Comparing with hmac.compare_digest (not ==) avoids a timing attack that
    could let an attacker guess the correct signature one byte at a time.
    """
    secret = current_app.config.get("RAZORPAY_WEBHOOK_SECRET")
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def claim_payment_event(db, razorpay_payment_id):
    """
    Idempotency guard shared by the webhook AND the browser-side verify
    routes: both paths can fire for the same real payment (the browser
    calls verify right after checkout closes; the webhook calls in
    separately, sometimes only seconds apart, sometimes because Razorpay
    retried a delivery). Without this, the SAME payment could get credited
    to an invoice/payment cycle twice.

    Uses razorpay_payment_id as the document's _id - MongoDB enforces _id
    uniqueness natively, so a second insert_one with the same id raises
    DuplicateKeyError instead of silently succeeding twice. This function
    returns True only for whichever caller's insert wins that race
    (meaning: go ahead and credit it), and False for every other caller
    handling the same payment_id (meaning: already handled, do nothing).
    """
    try:
        db.processed_payment_events.insert_one(
            {"_id": razorpay_payment_id, "processed_at": datetime.now(timezone.utc)}
        )
        return True
    except DuplicateKeyError:
        return False
