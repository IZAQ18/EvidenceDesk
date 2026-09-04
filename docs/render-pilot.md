# Protected Render pilot

Render is the closest no-cost test host for EvidenceDesk because its free web service can build the
existing Dockerfile from a private GitHub repository and provides an `onrender.com` HTTPS address.
The Blueprint also creates a private-network PostgreSQL database on the free plan. Render may still
require a card for account identity verification; that is separate from choosing paid resources.
The repository includes `render.yaml` so deployment settings remain reviewable without committing
Google credentials or the database connection string.

## Create the service

1. Sign in to Render and choose **New → Blueprint**.
2. Connect GitHub and grant the Render GitHub app access only to `IZAQ18/EvidenceDesk`.
3. Select the private repository. Render detects `render.yaml` at the repository root.
4. Enter the Google Web client ID and client secret when prompted. Keep both values in Render only.
5. Confirm that both the web service and PostgreSQL database show **Free**, then apply the Blueprint.

The Blueprint builds the Dockerfile, includes Tesseract OCR, waits for GitHub checks before automatic
deployment, uses `/health`, and supplies safe pilot limits. It injects the database connection from
Render without committing it. Google credentials are declared with `sync: false`, so their values
are entered in Render and never committed to Git.

## Optional global checkout and payment notifications

Create and activate the hosted checkout with your payment provider first. Connect your MCB payout account only inside that provider's verified dashboard. Then add these values manually under the Render service's **Environment** settings:

```dotenv
EVIDENCEDESK_CHECKOUT_URL=https://your-store.example/checkout/buy/your-product
EVIDENCEDESK_SUPPORT_URL=https://your-provider.example/your-support-page
EVIDENCEDESK_PAYMENT_WEBHOOK_SECRET=generated-in-the-provider-dashboard
```

Configure the provider callback as `https://YOUR-SERVICE.onrender.com/v1/payments/webhook`.
EvidenceDesk verifies the `X-Signature`, stores a minimal idempotent event, masks the customer email,
and displays it to admins under **Insights → Payment notifications**. Leave
`EVIDENCEDESK_PAYMENT_ENTITLEMENTS_ENABLED=false` on this synthetic-data pilot. Automated Pro access
requires fully durable account mode and the separate [billing entitlement runbook](billing-entitlements.md).

The pilot stores payment-event metadata in managed PostgreSQL, but Render's free database expires
after 30 days and provides no backups. It is not a financial ledger. The payment provider dashboard
remains the source of truth, and a commercial launch needs a paid, backed-up database before relying
on the in-app panel.

## Optional Google sign-in acceptance

The repository includes a disabled-by-default **Continue with Google** account flow. It cannot run
at the same time as the shared pilot access code. Follow the [Google sign-in runbook](google-signin.md)
to create a Google Web application client and enter its secret only in Render. This pilot keeps
accounts and extracted evidence in PostgreSQL, while original files remain on ephemeral local
storage. Use it only for a synthetic-data acceptance test; durable public accounts still require
private object storage, backups, monitoring, and the remaining launch controls.

## Verify the pilot

1. Wait for the initial Docker deployment to become live.
2. Open the assigned `onrender.com` URL in a private browser window.
3. Confirm the IZAQ access page rejects a wrong code and accepts the correct code.
4. Confirm the two Northstar Labs synthetic documents appear automatically.
5. Ask: “What is the annual leave policy?” Open its citation and verify the source passage.
6. Ask the Code of Conduct alone about annual leave. EvidenceDesk should refuse because that document does not contain the answer.
7. Visit `/docs`; production should not expose the API explorer.

## Free-tier boundary

Free Render web services spin down after inactivity, so the first request can be slow. Render may
restart a free service at any time and free services do not support persistent disks. PostgreSQL
preserves accounts, extracted evidence, conversations, feedback, audit events, and aggregate usage
metrics across those web-service restarts. Original uploaded files can still disappear, and the
free PostgreSQL database expires after 30 days without backups.

Do not upload personal, confidential, regulated, or client material. For an official launch, move the container to the documented AWS path, use individual identity-provider accounts, persistent encrypted storage, a managed data/search layer, centralized logs, backups, monitoring, and a formal security review.
