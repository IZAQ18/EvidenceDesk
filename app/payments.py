from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


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
    resource_type: str
    variant_id: str | None
    workspace_id: str | None
    entitlement_token: str | None
    provider_updated_at: str


@dataclass(frozen=True)
class EntitlementDecision:
    workspace_id: str
    plan: str
    status: str
    provider_id: str
    variant_id: str
    provider_updated_at: str


class InvalidPaymentWebhook(ValueError):
    pass


_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_PRO_SUBSCRIPTION_EVENTS = {
    "subscription_created",
    "subscription_updated",
    "subscription_cancelled",
    "subscription_resumed",
    "subscription_expired",
    "subscription_paused",
    "subscription_unpaused",
}
_PRO_ACCESS_STATUSES = {"on_trial", "active", "paused", "past_due", "cancelled"}
_PRO_REVOKE_STATUSES = {"expired", "unpaid"}


def _entitlement_token(workspace_id: str, secret: str) -> str:
    message = f"evidencedesk:pro:{workspace_id}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def build_pro_checkout_url(base_url: str, workspace_id: str, secret: str) -> str:
    """Bind an account workspace to hosted checkout without exposing a billing secret."""
    if not _WORKSPACE_ID.fullmatch(workspace_id):
        raise ValueError("Invalid workspace identifier")
    parts = urlsplit(base_url)
    protected_keys = {
        "checkout[custom][workspace_id]",
        "checkout[custom][entitlement_token]",
    }
    query = [
        item for item in parse_qsl(parts.query, keep_blank_values=True) if item[0] not in protected_keys
    ]
    query.extend(
        [
            ("checkout[custom][workspace_id]", workspace_id),
            ("checkout[custom][entitlement_token]", _entitlement_token(workspace_id, secret)),
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def pro_entitlement_decision(
    event: PaymentEvent, *, approved_variant_ids: set[str], secret: str
) -> EntitlementDecision | None:
    """Return a fail-closed plan change for a live, approved subscription webhook."""
    if (
        event.test_mode
        or event.resource_type != "subscriptions"
        or event.event_name not in _PRO_SUBSCRIPTION_EVENTS
        or not event.workspace_id
        or not event.variant_id
        or event.variant_id not in approved_variant_ids
        or not event.entitlement_token
        or not event.provider_updated_at
    ):
        return None
    expected = _entitlement_token(event.workspace_id, secret)
    if not hmac.compare_digest(event.entitlement_token, expected):
        return None
    if event.status in _PRO_ACCESS_STATUSES:
        plan = "pro"
    elif event.status in _PRO_REVOKE_STATUSES:
        plan = "free"
    else:
        return None
    return EntitlementDecision(
        workspace_id=event.workspace_id,
        plan=plan,
        status=event.status,
        provider_id=event.provider_id,
        variant_id=event.variant_id,
        provider_updated_at=event.provider_updated_at,
    )


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
    custom_data = meta.get("custom_data")
    custom_data = custom_data if isinstance(custom_data, dict) else {}
    workspace_id = custom_data.get("workspace_id")
    if not isinstance(workspace_id, str) or not _WORKSPACE_ID.fullmatch(workspace_id):
        workspace_id = None
    entitlement_token = custom_data.get("entitlement_token")
    if not isinstance(entitlement_token, str) or len(entitlement_token) != 64:
        entitlement_token = None
    variant = attributes.get("variant_id")
    if variant is None and isinstance(attributes.get("first_order_item"), dict):
        variant = attributes["first_order_item"].get("variant_id")
    variant_id = str(variant)[:40] if variant is not None else None
    provider_updated_at = ""
    raw_updated_at = attributes.get("updated_at") or attributes.get("created_at")
    if isinstance(raw_updated_at, str):
        try:
            provider_updated_at = (
                datetime.fromisoformat(raw_updated_at.replace("Z", "+00:00"))
                .astimezone(UTC)
                .isoformat()
            )
        except ValueError:
            provider_updated_at = ""
    return PaymentEvent(
        event_key=hashlib.sha256(body).hexdigest(),
        event_name=event_name,
        provider_id=provider_id,
        status=status,
        amount_minor=amount_minor,
        currency=currency,
        customer_hint=customer_hint,
        test_mode=bool(attributes.get("test_mode", False)),
        resource_type=str(data.get("type") or "")[:40],
        variant_id=variant_id,
        workspace_id=workspace_id,
        entitlement_token=entitlement_token,
        provider_updated_at=provider_updated_at,
    )
