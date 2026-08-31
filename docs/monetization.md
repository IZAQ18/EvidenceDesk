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

| Tier | Validation price | Purpose | Status |
| --- | --- | --- | --- |
| Free pilot | **$0** | Let reviewers understand the product with synthetic examples. | Available as a protected demonstration; no durable service promise. |
| Pro | **$9/month** | Private individual library, OCR and citation-first questions, exports, and account controls. | Pricing hypothesis; guarded subscription entitlements and allowances exist in code but remain disabled on the acceptance pilot pending provider and durable-deployment acceptance. |
| Founding team pilot | **$149 one time** | A 14-day guided evaluation for up to five reviewers, approved non-sensitive documents, and a written readiness report. | Planned validation offer; qualify the team and document scope before accepting payment. |

The first measurable allowance hypothesis is now enforced for account workspaces: Free includes 5
document slots and 100 question attempts per calendar month; Pro includes 100 and 2,000. Treat
these as validation limits, not proven market-fit packaging, and revise them only from real usage
and cost evidence. The protected shared-code Render pilot is deliberately unmanaged by these limits.

The $9 Pro hypothesis is intentionally below current entry prices published by comparable document-Q&A products: [Humata lists Expert at $9.99/month](https://www.humata.ai/pricing), while [AskYourPDF lists annual-billing Premium and Pro plans at $11.99 and $14.99 per month](https://askyourpdf.com/blog/askyourpdf-research-assistant). This is a benchmark, not proof of willingness to pay. Validate it with real pilot interviews and retention data before treating it as final.

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

EvidenceDesk now includes a disabled-by-default Lemon Squeezy Pro foundation. An authenticated workspace administrator receives a hosted checkout URL carrying a server-generated workspace binding. A signed webhook can update only that workspace, only for an approved variant, only outside provider test mode, and only when the provider event is newer than stored state. Minimal payment notifications and entitlement state are stored; card and bank details are not.

This code is not a claim that billing is live. The protected Render acceptance build uses approved
Google test accounts with ephemeral storage, so it cannot enable subscription entitlements. The
one-time Team pilot remains a manually qualified service offer rather than an automatically granted
plan.

## Provider decision for a Pakistan-based developer

The current site does **not** accept subscription payments. Direct MCB/NayaPay support is voluntary
and never unlocks a product plan.

Lemon Squeezy still lists Pakistan for bank payouts, so an identity-verification problem is not the
same as a country-wide rejection. Keep one support case open, but do not block launch planning on it.
The recommended next application is **Paddle**: its official Pakistan product page markets service
to Pakistani SaaS businesses, Paddle accepts individuals/sole traders without the separate business
identification step, and payouts can use wire transfer or Payoneer. Approval and identity/domain
review are still required, so none of this guarantees acceptance.

**FastSpring** is the fallback Merchant of Record candidate. Its current payout portal supports
international SWIFT wire transfers, although its own onboarding and payout verification still apply.

Apply to one provider at a time using the live `/about` page, matching legal name and bank records,
the synthetic demo, pricing explanation, privacy boundary, refund plan, and support contact. Do not
replace the tested Lemon Squeezy webhook adapter until another provider approves the account and
provides sandbox credentials.

Official references: [Lemon Squeezy supported countries](https://docs.lemonsqueezy.com/help/getting-started/supported-countries),
[Paddle for Pakistan](https://www.paddle.com/billing/pakistan),
[Paddle business identification](https://www.paddle.com/help/start/account-verification/what-is-business-verification),
[Paddle payouts](https://www.paddle.com/help/manage/get-paid/when-and-how-do-i-get-paid), and
[FastSpring payout setup](https://developer.fastspring.com/docs/set-up-your-payout-account).

Before enabling live checkout, still add:

- provision and acceptance-test the existing PostgreSQL and private object-storage adapters;
- deploy and acceptance-test the existing account and tenant ownership flows;
- configure the approved provider product, variant, webhook, customer portal, and live test purchase;
- terms, privacy notice, refund/cancellation policy, and support contact;
- backups, monitoring, abuse limits, and a data-deletion process;
- local legal and tax advice appropriate to the developer's business.

## Suggested rollout

### Phase 1 — learn

Keep Render protected, synthetic, and free. Interview reviewers. Track which questions they ask, where retrieval fails, and whether citations create enough trust to justify a paid pilot.

### Phase 2 — sell one bounded pilot

Create a written scope, success measures, data rules, duration, and fixed price. Prefer non-sensitive or carefully approved documents. Payment can use an invoice or approved hosted product checkout; do not represent this MVP as a production SLA.

### Phase 3 — activate subscriptions

After provider approval, deploy durable account mode, follow the [billing entitlement runbook](billing-entitlements.md), connect the provider customer portal, and enforce one clear Pro allowance model. Add more tiers only when customer behavior supports them.

### Phase 4 — team readiness

Move from ephemeral storage to managed services, finish identity integration, isolate tenants, add operational monitoring, and complete security review before promising organizational production use.
