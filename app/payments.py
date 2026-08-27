from __future__ import annotations

import hashlib
import hmac
import json
import re
from base64 import b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
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
    "subscription.activated",
    "subscription.updated",
    "subscription.canceled",
    "subscription.uncanceled",
    "subscription.deactivated",
    "subscription.payment.overdue",
    "subscription.paused",
    "subscription.resumed",
}
_PRO_ACCESS_STATUSES = {
    "on_trial",
    "trial",
    "active",
    "paused",
    "past_due",
    "overdue",
    "cancelled",
    "canceled",
}
_PRO_REVOKE_STATUSES = {"expired", "unpaid", "deactivated"}


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


def build_fastspring_checkout_page(
    storefront_url: str, product_path: str, workspace_id: str, secret: str
) -> str:
    """Build a tiny Store Builder launcher carrying a signed workspace binding."""
    if not _WORKSPACE_ID.fullmatch(workspace_id):
        raise ValueError("Invalid workspace identifier")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", product_path):
        raise ValueError("Invalid FastSpring product path")
    session = {
        "reset": True,
        "products": [{"path": product_path, "quantity": 1}],
        "tags": {
            "workspace_id": workspace_id,
            "entitlement_token": _entitlement_token(workspace_id, secret),
        },
        "checkout": True,
    }
    session_json = json.dumps(session, separators=(",", ":")).replace("<", "\\u003c")
    safe_storefront = escape(storefront_url, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Opening EvidenceDesk checkout</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#080b11;color:#e8eef8;font:16px system-ui,sans-serif}}
main{{max-width:520px;padding:32px;text-align:center}}button{{border:0;border-radius:12px;padding:13px 18px;background:#4f8cff;color:white;font-weight:700;cursor:pointer}}
p{{color:#a8b3c7;line-height:1.55}}
</style></head><body><main><h1>Opening secure checkout</h1><p>Payment details are entered only on FastSpring's hosted checkout.</p>
<button type="button" onclick="fastspring.builder.checkout()">Open checkout</button></main>
<script>var fscSession={session_json};</script>
<script id="fsc-api" src="https://sbl.onfastspring.com/sbl/1.0.3/fastspring-builder.min.js" data-storefront="{safe_storefront}"></script>
</body></html>"""


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


def _fastspring_timestamp(value: object) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _fastspring_amount_minor(value: object) -> int:
    try:
        return max(0, int((Decimal(str(value or 0)) * 100).quantize(Decimal("1"))))
    except (InvalidOperation, TypeError, ValueError):
        raise InvalidPaymentWebhook("Invalid payment amount") from None


def parse_fastspring_webhook(
    body: bytes, signature: str | None, secret: str
) -> list[PaymentEvent]:
    """Verify and normalize a FastSpring webhook, including multi-event posts."""
    if not signature:
        raise InvalidPaymentWebhook("Missing payment signature")
    expected = b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(signature, expected):
        raise InvalidPaymentWebhook("Invalid payment signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidPaymentWebhook("Invalid payment payload") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise InvalidPaymentWebhook("Incomplete payment payload")
    raw_events = payload["events"]
    if not raw_events or len(raw_events) > 100:
        raise InvalidPaymentWebhook("Invalid payment event count")

    events: list[PaymentEvent] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, dict) or not isinstance(raw_event.get("data"), dict):
            raise InvalidPaymentWebhook("Incomplete payment event")
        data = raw_event["data"]
        event_id = str(raw_event.get("id") or "")[:100]
        event_name = str(raw_event.get("type") or "")[:80]
        provider_id = str(data.get("subscription") or data.get("id") or "")[:100]
        if not event_id or not event_name or not provider_id:
            raise InvalidPaymentWebhook("Incomplete payment event")

        state = str(data.get("state") or event_name.rsplit(".", 1)[-1]).lower()[:40]
        if event_name == "subscription.deactivated":
            state = "deactivated"
        product = data.get("product")
        if isinstance(product, dict):
            product = product.get("product") or product.get("path")
        variant_id = str(product)[:100] if isinstance(product, str) and product else None

        tags = data.get("tags")
        order = data.get("order")
        if not isinstance(tags, dict) and isinstance(order, dict):
            tags = order.get("tags")
        tags = tags if isinstance(tags, dict) else {}
        workspace_id = tags.get("workspace_id")
        if not isinstance(workspace_id, str) or not _WORKSPACE_ID.fullmatch(workspace_id):
            workspace_id = None
        entitlement_token = tags.get("entitlement_token")
        if not isinstance(entitlement_token, str) or len(entitlement_token) != 64:
            entitlement_token = None

        account = data.get("account")
        contact = account.get("contact") if isinstance(account, dict) else None
        customer_hint = _masked_email(contact.get("email")) if isinstance(contact, dict) else None
        changed = data.get("changed") or raw_event.get("created")
        live = data.get("live") if isinstance(data.get("live"), bool) else raw_event.get("live")
        resource_type = "subscriptions" if event_name.startswith("subscription.") else "orders"
        events.append(
            PaymentEvent(
                event_key=f"fastspring:{event_id}",
                event_name=event_name,
                provider_id=provider_id,
                status=state,
                amount_minor=_fastspring_amount_minor(data.get("total", 0)),
                currency=str(data.get("currency") or "USD").upper()[:3],
                customer_hint=customer_hint,
                test_mode=not bool(live),
                resource_type=resource_type,
                variant_id=variant_id,
                workspace_id=workspace_id,
                entitlement_token=entitlement_token,
                provider_updated_at=_fastspring_timestamp(changed),
            )
        )
    return events
