# EvidenceDesk monetization path

This document is a delivery plan, not a claim that paid plans or production billing are active.

## The first offer

Lead with a **founding team pilot**, not a donation button. A buyer is paying for a defined outcome: connect an approved document set, evaluate citation quality, identify retrieval gaps, and receive a short readiness report. Keep the pilot narrow, time-boxed, and explicit about data handling.

A sensible first customer journey is:

1. Visitor tries the protected synthetic demo.
2. Visitor sees a grounded answer and opens its citation.
3. Visitor reviews the honest product and security boundaries.
4. Interested teams request a founding pilot.
5. The developer qualifies the use case before accepting data or payment.

## Freemium hypothesis

| Tier | Purpose | Status |
| --- | --- | --- |
| Free pilot | Let reviewers understand the product with synthetic examples. | Available as a protected demonstration; no durable service promise. |
| Pro | Private individual library, higher OCR/question allowances, exports, and account controls. | Planned; price and limits need user research. |
| Team | Shared workspace, roles, managed storage, onboarding, and evaluation. | Planned; best candidate for a paid founding pilot. |

Do not advertise exact quotas until the application measures usage and actually enforces them.

## Payment safety

Prefer an approved hosted checkout for product purchases so refunds, receipts, taxes, payer privacy, and fraud controls stay with the payment provider. EvidenceDesk reads hosted URLs from `EVIDENCEDESK_CHECKOUT_URL` and `EVIDENCEDESK_SUPPORT_URL`, requires HTTPS, and opens them outside the application. The public support panel separately displays recipient transfer details that the named account holders authorized for publication; never publish payment secrets, private keys, payer banking data, or provider credentials.

For a real subscription, the browser must never decide that a customer is paid. The minimum backend flow is:

```text
Hosted checkout
      ↓
Provider-signed webhook
      ↓
Customer + subscription record
      ↓
Server-side entitlement and usage checks
      ↓
Billing portal, cancellation, and audit events
```

EvidenceDesk v0.7 implements signed Lemon Squeezy-compatible webhook verification, idempotent minimal event storage, and an admin-only notification surface. It intentionally does not store card/bank details and does not activate plan entitlements.

Before enabling live checkout, still add:

- a durable PostgreSQL database and object storage;
- user accounts and tenant ownership;
- provider-signature verification and idempotent webhook handling;
- server-side plan entitlements and usage counters;
- terms, privacy notice, refund/cancellation policy, and support contact;
- backups, monitoring, abuse limits, and a data-deletion process;
- local legal and tax advice appropriate to the developer's business.

## Suggested rollout

### Phase 1 — learn

Keep Render protected, synthetic, and free. Interview reviewers. Track which questions they ask, where retrieval fails, and whether citations create enough trust to justify a paid pilot.

### Phase 2 — sell one bounded pilot

Create a written scope, success measures, data rules, duration, and fixed price. Prefer non-sensitive or carefully approved documents. Payment can use an invoice or approved hosted product checkout; do not represent this MVP as a production SLA.

### Phase 3 — activate subscriptions

After account approval and the backend billing controls exist, connect a merchant-of-record checkout, webhook, customer portal, and entitlements. Start with one plan and one allowance model. Add more tiers only when customer behavior supports them.

### Phase 4 — team readiness

Move from ephemeral storage to managed services, finish identity integration, isolate tenants, add operational monitoring, and complete security review before promising organizational production use.
