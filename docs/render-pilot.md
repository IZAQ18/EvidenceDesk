# Protected Render pilot

Render is the closest no-cost test host for EvidenceDesk because its free web service can build the existing Dockerfile from a private GitHub repository and provides an `onrender.com` HTTPS address. Render may still require a credit card for account identity verification even when the Free instance type is selected; that verification is separate from choosing a paid service. The repository includes `render.yaml` so the settings remain reviewable without committing the access secret.

## Create the service

1. Sign in to Render and choose **New → Blueprint**.
2. Connect GitHub and grant the Render GitHub app access only to `IZAQ18/EvidenceDesk`.
3. Select the private repository. Render detects `render.yaml` at the repository root.
4. When prompted for `EVIDENCEDESK_PILOT_ACCESS_CODE`, enter a unique random code of at least 24 characters.
5. Confirm that the plan is **Free**, then apply the Blueprint.

The Blueprint builds the Dockerfile, includes Tesseract OCR, waits for GitHub checks before automatic deployment, uses `/health`, and supplies the safe pilot limits. The access code is declared with `sync: false`, so its value is entered in Render and never committed to Git.

## Optional global checkout and payment notifications

Create and activate the hosted checkout with your payment provider first. Connect your MCB payout account only inside that provider's verified dashboard. Then add these values manually under the Render service's **Environment** settings:

```dotenv
EVIDENCEDESK_CHECKOUT_URL=https://your-store.example/checkout/buy/your-product
EVIDENCEDESK_SUPPORT_URL=https://your-provider.example/your-support-page
EVIDENCEDESK_PAYMENT_WEBHOOK_SECRET=generated-in-the-provider-dashboard
```

Configure the provider callback as `https://YOUR-SERVICE.onrender.com/v1/payments/webhook`. EvidenceDesk verifies the `X-Signature`, stores a minimal idempotent event, masks the customer email, and displays it to admins under **Insights → Payment notifications**. Leave `EVIDENCEDESK_PAYMENT_ENTITLEMENTS_ENABLED=false` on this shared, ephemeral pilot. Automated Pro access requires durable account mode and the separate [billing entitlement runbook](billing-entitlements.md).

Render's free ephemeral SQLite database is not a financial ledger. The payment provider dashboard remains the source of truth, and live commercial launch should move payment-event state to durable PostgreSQL before relying on the in-app panel.

## Verify the pilot

1. Wait for the initial Docker deployment to become live.
2. Open the assigned `onrender.com` URL in a private browser window.
3. Confirm the IZAQ access page rejects a wrong code and accepts the correct code.
4. Confirm the two Northstar Labs synthetic documents appear automatically.
5. Ask: “What is the annual leave policy?” Open its citation and verify the source passage.
6. Ask the Code of Conduct alone about annual leave. EvidenceDesk should refuse because that document does not contain the answer.
7. Visit `/docs`; production should not expose the API explorer.

## Free-tier boundary

Free Render web services spin down after 15 minutes without traffic, so the first request after inactivity can take about a minute. Render may restart a free service at any time and free services do not support persistent disks. Uploads, conversations, feedback, and audit events can therefore reset. The synthetic documents are intentionally restored when the database is empty.

Do not upload personal, confidential, regulated, or client material. For an official launch, move the container to the documented AWS path, use individual identity-provider accounts, persistent encrypted storage, a managed data/search layer, centralized logs, backups, monitoring, and a formal security review.
