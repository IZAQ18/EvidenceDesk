# Pro billing entitlement runbook

This is a configuration and acceptance guide for the guarded Pro foundation. It is not evidence
that EvidenceDesk billing is live, that Lemon Squeezy has approved the store, or that the current
free Render pilot is suitable for paying customers.

## What the code does

1. A signed-in workspace administrator selects **Upgrade to Pro**.
2. EvidenceDesk creates a Lemon Squeezy hosted-checkout URL with a workspace ID and a server-made
   HMAC binding in custom checkout data. The webhook secret itself never enters the browser.
3. Lemon Squeezy returns that custom data in its subscription webhook.
4. EvidenceDesk verifies the provider signature, resource and event type, live mode, approved
   variant, workspace binding, status, and provider timestamp.
5. A valid current subscription sets only the bound workspace to `pro`. A newer `expired` or
   `unpaid` event returns it to `free`; older and duplicate events cannot overwrite newer state.

`cancelled`, `past_due`, and `paused` keep access so a customer is not cut off before the provider
subscription reaches an end state. The payment provider remains the billing source of truth.

## Required environment values

Do not set these on the current shared-code, ephemeral SQLite Render pilot. First deploy account
mode with accepted PostgreSQL and private object storage, backups, monitoring, and policies.

```dotenv
EVIDENCEDESK_ACCOUNTS_ENABLED=true
EVIDENCEDESK_PRO_CHECKOUT_URL=https://your-store.lemonsqueezy.com/checkout/buy/YOUR_VARIANT
EVIDENCEDESK_PAYMENT_PRO_VARIANT_IDS=YOUR_NUMERIC_VARIANT_ID
EVIDENCEDESK_PAYMENT_WEBHOOK_SECRET=YOUR_SERVER_ONLY_SIGNING_SECRET
EVIDENCEDESK_PAYMENT_ENTITLEMENTS_ENABLED=true
```

Multiple approved Pro variants can be comma-separated. Invalid or missing variant IDs, an HTTP
checkout URL, missing account mode, or a missing webhook secret prevents startup when entitlements
are enabled.

Create the provider webhook at:

```text
https://YOUR-DOMAIN/v1/payments/webhook
```

Subscribe it to the provider's subscription lifecycle events. Never paste the webhook secret into
Git, browser code, screenshots, support messages, or a public environment file.

## Acceptance checklist

- Use the provider's test mode first and confirm test events are recorded but cannot grant Pro.
- Use a dedicated test workspace and approved variant for the controlled live acceptance purchase.
- Confirm the checkout URL contains the workspace ID and an opaque 64-character token, not the
  webhook secret.
- Confirm an active live subscription changes `/v1/auth/me` from `free` to `pro`.
- Replay the same event and confirm it does not apply twice.
- Send or replay an older end-state event and confirm it cannot revoke newer access.
- Confirm an unknown variant, altered workspace ID, altered binding, malformed timestamp, and
  invalid signature cannot grant access.
- Confirm a newer `expired` or `unpaid` event returns the workspace to `free`.
- Exercise backup/restore and alerting before relying on the local entitlement record.

Automated tests cover the core acceptance and refusal cases locally. Before charging customers,
also connect the provider-hosted customer portal, enforce and display plan allowances, publish
terms/privacy/refund/cancellation policies, add operator alerts and reconciliation, complete live
mobile/accessibility testing, and obtain appropriate local legal and tax advice.

Provider references:

- [Passing custom checkout data](https://docs.lemonsqueezy.com/help/checkout/passing-custom-data)
- [Webhook event types](https://docs.lemonsqueezy.com/help/webhooks/event-types)
- [Subscription object and status values](https://docs.lemonsqueezy.com/api/subscriptions/the-subscription-object)
- [Webhook implementation guide](https://docs.lemonsqueezy.com/guides/developer-guide/webhooks)
