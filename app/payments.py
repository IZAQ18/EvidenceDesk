from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentEvent:
    event_key: str
    event_name: str
    provider_id: str
    status: str
    amount_minor: int
    currency: str
    customer_hint: str | None
    test_mode: bool


class InvalidPaymentWebhook(ValueError):
    pass


def _masked_email(value: object) -> str | None:
    if not isinstance(value, str) or "@" not in value:
        return None
    local, domain = value.strip().split("@", 1)
    if not local or not domain:
        return None
    visible = local[:2]
    return f"{visible}{'*' * max(2, min(len(local) - len(visible), 6))}@{domain}"


def parse_lemonsqueezy_webhook(
    body: bytes, signature: str | None, secret: str
) -> PaymentEvent:
    if not signature:
        raise InvalidPaymentWebhook("Missing payment signature")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise InvalidPaymentWebhook("Invalid payment signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidPaymentWebhook("Invalid payment payload") from exc
    if not isinstance(payload, dict):
        raise InvalidPaymentWebhook("Invalid payment payload")
    meta = payload.get("meta")
    data = payload.get("data")
    if not isinstance(meta, dict) or not isinstance(data, dict):
        raise InvalidPaymentWebhook("Incomplete payment payload")
    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        raise InvalidPaymentWebhook("Incomplete payment payload")
    event_name = str(meta.get("event_name") or "")[:80]
    provider_id = str(data.get("id") or "")[:100]
    if not event_name or not provider_id:
        raise InvalidPaymentWebhook("Incomplete payment payload")
    amount = attributes.get("total", 0)
    try:
        amount_minor = max(0, int(amount or 0))
    except (TypeError, ValueError) as exc:
        raise InvalidPaymentWebhook("Invalid payment amount") from exc
    currency = str(attributes.get("currency") or "USD").upper()[:3]
    status = str(attributes.get("status") or event_name).lower()[:40]
    customer_hint = _masked_email(
        attributes.get("user_email") or attributes.get("customer_email")
    )
    return PaymentEvent(
        event_key=hashlib.sha256(body).hexdigest(),
        event_name=event_name,
        provider_id=provider_id,
        status=status,
        amount_minor=amount_minor,
        currency=currency,
        customer_hint=customer_hint,
        test_mode=bool(attributes.get("test_mode", False)),
    )
