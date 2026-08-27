# Pro billing entitlement runbook

This is a configuration and acceptance guide for the guarded Pro foundation. It is not evidence
that EvidenceDesk billing is live, that FastSpring or Lemon Squeezy has approved the store, or that the current
free Render pilot is suitable for paying customers.

## What the code does

1. A signed-in workspace administrator selects **Upgrade to Pro**.
2. EvidenceDesk creates a provider-hosted checkout session with a workspace ID and a server-made
   HMAC binding in checkout metadata. The webhook secret itself never enters the browser.
3. The provider returns that metadata in its subscription webhook.
4. EvidenceDesk verifies the provider signature, resource and event type, live mode, approved
   variant, workspace binding, status, and provider timestamp.
5. A valid current subscription sets only the bound workspace to `pro`. A newer `expired` or
   `unpaid` event returns it to `free`; older and duplicate events cannot overwrite newer state.
6. Server-side account allowances change with the stored plan: Free receives 5 document slots and
   100 question attempts per calendar month; Pro receives 100 and 2,000. Queued uploads reserve a
   document slot, deletion releases it, and the account panel reads the same usage ledger.

`cancelled`, `past_due`, and `paused` keep access so a customer is not cut off before the provider
subscription reaches an end state. The payment provider remains the billing source of truth.

## Required environment values

Do not set these on the current shared-code, ephemeral SQLite Render pilot. First deploy account
mode with accepted PostgreSQL and private object storage, backups, monitoring, and policies.

```dotenv
EVIDENCEDESK_ACCOUNTS_ENABLED=true
EVIDENCEDESK_PAYMENT_PROVIDER=fastspring
EVIDENCEDESK_FASTSPRING_STOREFRONT_URL=https://yourstore.onfastspring.com/popup-yourstore
EVIDENCEDESK_FASTSPRING_PRODUCT_PATH=evidencedesk-pro-monthly
EVIDENCEDESK_PAYMENT_PRO_VARIANT_IDS=evidencedesk-pro-monthly
EVIDENCEDESK_PAYMENT_WEBHOOK_SECRET=YOUR_SERVER_ONLY_SIGNING_SECRET
EVIDENCEDESK_PAYMENT_ENTITLEMENTS_ENABLED=true
```

Multiple approved Pro products can be comma-separated. FastSpring requires its HTTPS popup
checkout URL and product path; Lemon Squeezy requires its HTTPS Pro checkout URL and numeric
variant IDs. Invalid configuration, missing account mode, or a missing webhook secret prevents
startup when entitlements are enabled.

Create the provider webhook at:

```text
https://YOUR-DOMAIN/v1/payments/webhook
```

For FastSpring, enable webhook expansion and subscribe to `subscription.activated`,
`subscription.updated`, `subscription.canceled`, `subscription.uncanceled`,
`subscription.deactivated`, `subscription.payment.overdue`, `subscription.paused`, and
`subscription.resumed`. Never paste the webhook secret into
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
- Confirm a newer `expired` or `unpaid` event returns the workspace to `free` and the lower
  allowances apply immediately without deleting existing data.
- Confirm document and monthly-question limits cannot be bypassed by concurrent requests.
- Confirm the account usage panel matches the server response from `/v1/account/usage`.
- Exercise backup/restore and alerting before relying on the local entitlement record.

Automated tests cover the entitlement, allowance, usage-display structure, and refusal cases
locally. Before charging customers, also connect the provider-hosted customer portal, publish
terms/privacy/refund/cancellation policies, add operator alerts and reconciliation, complete live
mobile/accessibility testing, and obtain appropriate local legal and tax advice.

Provider references:

- [FastSpring Store Builder sessions](https://developer.fastspring.com/reference/session-object)
- [FastSpring webhook events](https://developer.fastspring.com/reference/webhooks-overview)
- [FastSpring HMAC message security](https://developer.fastspring.com/reference/message-security)
- [Passing custom checkout data](https://docs.lemonsqueezy.com/help/checkout/passing-custom-data)
- [Webhook event types](https://docs.lemonsqueezy.com/help/webhooks/event-types)
- [Subscription object and status values](https://docs.lemonsqueezy.com/api/subscriptions/the-subscription-object)
- [Webhook implementation guide](https://docs.lemonsqueezy.com/guides/developer-guide/webhooks)
