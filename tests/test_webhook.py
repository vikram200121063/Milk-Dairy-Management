"""
Tests for the Razorpay webhook (/pay/webhook) and the idempotency guard it
shares with the existing browser-side /verify routes
(app/services/payment_gateway_service.py's claim_payment_event).
"""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone


def _seed(db):
    db.buyers.insert_one({"buyer_id": "BUY0001", "company_name": "Test Traders", "status": "Active"})
    db.buyer_invoices.insert_one(
        {
            "invoice_id": "INV0001",
            "buyer_id": "BUY0001",
            "buyer_name": "Test Traders",
            "billing_period": "2026-09-B2",
            "total_amount": 5000.0,
            "amount_paid": 0.0,
            "remaining_amount": 5000.0,
            "status": "Unpaid",
            "due_date": datetime.now(timezone.utc) + timedelta(days=5),
            "overdue_days": 0,
        }
    )
    db.payments.insert_one(
        {
            "payment_id": "PAY0001",
            "customer_id": "CUST0001",
            "customer_name": "Ramesh",
            "payment_period": "2026-09-C2",
            "total_quantity": 100.0,
            "gross_amount": 3000.0,
            "deductions": 0.0,
            "previous_pending_amount": 0.0,
            "final_payable_amount": 3000.0,
            "amount_paid": 0.0,
            "remaining_amount": 3000.0,
            "payment_status": "Pending",
        }
    )


def _signed_post(client, path, payload_dict, secret="whsec_test_fake"):
    raw = json.dumps(payload_dict).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return client.post(path, data=raw, headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})


def _invoice_captured_event(payment_id="pay_TEST0001"):
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": 500000,
                    "notes": {"invoice_id": "INV0001", "buyer_id": "BUY0001"},
                }
            }
        },
    }


def _customer_captured_event(payment_id="pay_TEST0002"):
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": 300000,
                    "notes": {"payment_id": "PAY0001", "customer_id": "CUST0001"},
                }
            }
        },
    }


def test_bad_signature_is_rejected_and_does_not_credit(app, db, client):
    _seed(db)
    resp = client.post(
        "/pay/webhook",
        data=json.dumps(_invoice_captured_event()).encode("utf-8"),
        headers={"X-Razorpay-Signature": "deadbeef", "Content-Type": "application/json"},
    )
    assert resp.status_code == 400

    inv = db.buyer_invoices.find_one({"invoice_id": "INV0001"})
    assert inv["remaining_amount"] == 5000.0


def test_valid_webhook_credits_buyer_invoice(app, db, client):
    _seed(db)
    resp = _signed_post(client, "/pay/webhook", _invoice_captured_event())
    assert resp.status_code == 200

    inv = db.buyer_invoices.find_one({"invoice_id": "INV0001"})
    assert inv["remaining_amount"] == 0.0
    assert inv["status"] == "Paid"


def test_replayed_webhook_does_not_double_credit(app, db, client):
    _seed(db)
    _signed_post(client, "/pay/webhook", _invoice_captured_event())

    # Razorpay retries webhook deliveries - the same event can arrive twice.
    resp2 = _signed_post(client, "/pay/webhook", _invoice_captured_event())
    assert resp2.status_code == 200

    inv = db.buyer_invoices.find_one({"invoice_id": "INV0001"})
    payments_for_invoice = list(db.buyer_payments.find({"invoice_id": "INV0001"}))
    assert len(payments_for_invoice) == 1
    assert inv["remaining_amount"] == 0.0


def test_valid_webhook_credits_customer_payment_cycle(app, db, client):
    _seed(db)
    resp = _signed_post(client, "/pay/webhook", _customer_captured_event())
    assert resp.status_code == 200

    pay = db.payments.find_one({"payment_id": "PAY0001"})
    assert pay["remaining_amount"] == 0.0
    assert pay["payment_status"] == "Paid"


def test_idempotency_guard_is_shared_with_browser_verify_path(app, db, client):
    _seed(db)
    _signed_post(client, "/pay/webhook", _customer_captured_event())

    with app.app_context():
        from app.services import payment_gateway_service

        # Same payment_id the webhook just claimed - the browser-side
        # /verify route calls this exact function before crediting
        # anything, so this proves the two paths can't double-credit.
        claimed_again = payment_gateway_service.claim_payment_event(db, "pay_TEST0002")
    assert claimed_again is False


def test_non_captured_events_are_acknowledged_but_ignored(app, db, client):
    _seed(db)
    resp = _signed_post(client, "/pay/webhook", {"event": "payment.failed", "payload": {}})
    assert resp.status_code == 200


def test_webhook_without_secret_configured_returns_503(app, db, client):
    # Config.RAZORPAY_WEBHOOK_SECRET is read from the environment once at
    # process start (config.py), like any real deployment - it doesn't
    # change mid-process just because os.environ does. Flip it off
    # directly on this app's already-loaded config to simulate a
    # deployment that never set the variable.
    app.config["RAZORPAY_WEBHOOK_SECRET"] = None

    resp = client.post("/pay/webhook", data=b"{}", headers={"X-Razorpay-Signature": "x"})
    assert resp.status_code == 503
