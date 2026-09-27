"""
Minimal stand-in for the real `razorpay` PyPI package, matching its
documented public shape closely enough to exercise
app/services/payment_gateway_service.py in tests without any network
access or real Razorpay account:
  - razorpay.Client(auth=(key_id, key_secret))
  - client.order.create({...}) -> dict with at least 'id' and 'amount'
  - client.utility.verify_payment_signature({...}) -> True, or raises
    razorpay.errors.SignatureVerificationError

This is TEST INFRASTRUCTURE ONLY (see tests/conftest.py, which swaps it in
for the real `razorpay` module for the duration of the test run). The real
app always uses the real `razorpay` package from requirements.txt - this
stub is never imported outside of tests/. It still performs the real
HMAC-SHA256 signature math Razorpay documents, so a test that tampers with
a signature genuinely fails verification here, the same as it would
against the real API.
"""
import hashlib
import hmac
import itertools

from . import errors  # noqa: F401

_order_counter = itertools.count(1)


class _OrderAPI:
    def __init__(self, client):
        self._client = client

    def create(self, data):
        order_id = f"order_TEST{next(_order_counter):06d}"
        order = {
            "id": order_id,
            "amount": data["amount"],
            "currency": data.get("currency", "INR"),
            "receipt": data.get("receipt"),
            "status": "created",
            "notes": data.get("notes", {}),
        }
        self._client._orders[order_id] = order
        return order


class _UtilityAPI:
    def __init__(self, client):
        self._client = client

    def verify_payment_signature(self, params):
        order_id = params["razorpay_order_id"]
        payment_id = params["razorpay_payment_id"]
        signature = params["razorpay_signature"]
        secret = self._client._auth[1].encode()
        expected = hmac.new(
            secret, f"{order_id}|{payment_id}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise errors.SignatureVerificationError("Signature verification failed")
        return True


class Client:
    def __init__(self, auth):
        self._auth = auth
        self._orders = {}
        self.order = _OrderAPI(self)
        self.utility = _UtilityAPI(self)
