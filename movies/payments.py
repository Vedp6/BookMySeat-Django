

import hashlib
import hmac
import uuid

from django.conf import settings


def is_mock_mode():
    return not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def _get_client():
    import razorpay
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def create_order(amount_rupees, receipt):
    """
    Creates a Razorpay Order and returns its dict (same shape whether real
    or mocked): {'id': 'order_...', 'amount': <paise>, 'currency': 'INR', ...}
    """
    amount_paise = int(round(amount_rupees * 100))

    if is_mock_mode():
        return {
            "id": f"order_MOCK{uuid.uuid4().hex[:14]}",
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "status": "created",
        }

    client = _get_client()
    return client.order.create(
        {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
        }
    )


def _mock_secret():
    # Deterministic, non-secret key used only when no real Razorpay
    # credentials are configured - purely so mock signatures verify
    # against themselves for local testing.
    return "mock-local-dev-secret"


def sign_mock_payment(razorpay_order_id, razorpay_payment_id):
    """
    Only used by local test/demo code to produce a signature for a mock
    payment that verify_payment_signature() will accept, mirroring exactly
    how the browser's Razorpay Checkout would hand this off in production.
    """
    secret = settings.RAZORPAY_KEY_SECRET or _mock_secret()
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    return hmac.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
    """
    Verifies the signature Razorpay Checkout returns to the browser after a
    successful payment, per Razorpay's documented scheme:
        expected = HMAC_SHA256(key_secret, "{order_id}|{payment_id}")
    This MUST be done server-side (never trust the client's "success" flag
    alone) - this function is that server-side check.
    """
    secret = settings.RAZORPAY_KEY_SECRET or _mock_secret()
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected = hmac.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, razorpay_signature or "")


def create_refund(razorpay_payment_id, amount_rupees):
    """
    Issues a refund for a captured payment. Returns a dict with at least
    an 'id' key (same shape whether real or mocked).
    """
    amount_paise = int(round(amount_rupees * 100))

    if is_mock_mode() or (razorpay_payment_id or "").startswith("pay_MOCK"):
        return {
            "id": f"rfnd_MOCK{uuid.uuid4().hex[:14]}",
            "amount": amount_paise,
            "status": "processed",
        }

    client = _get_client()
    return client.payment.refund(razorpay_payment_id, {"amount": amount_paise})


def verify_webhook_signature(request_body_bytes, received_signature):
    """
    Verifies the X-Razorpay-Signature header on incoming webhook requests,
    per Razorpay's scheme:
        expected = HMAC_SHA256(webhook_secret, raw_request_body)
    """
    secret = settings.RAZORPAY_WEBHOOK_SECRET or _mock_secret()
    expected = hmac.new(
        secret.encode(), request_body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, received_signature or "")
