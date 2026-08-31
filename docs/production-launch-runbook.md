# Production customer launch runbook

EvidenceDesk has an explicit fail-closed switch:
`EVIDENCEDESK_PUBLIC_CUSTOMER_LAUNCH_ENABLED=true`. Do not set it merely to change marketing copy.
The application refuses to start unless the repository-controlled production gates below are
configured. Provider and operator acceptance still need evidence outside the code.

## Application-enforced prerequisites

- production environment and account authentication;
- PostgreSQL plus private S3 original storage;
- required private ClamAV scanning;
- account export and deletion;
- an approved registration path;
- the temporary-account override disabled;
- explicit confirmation that edge/distributed rate limiting is operating; and
- explicit confirmation that production monitoring and alerting are operating.

The confirmations are operator assertions, not automatic proof. Keep the acceptance report, alert
screenshots, restore result, scanner test, and responsible approver with the release record.

## Acceptance sequence

1. Provision PostgreSQL, encrypted/versioned private S3, backups, a separate ingestion worker, and
   private ClamAV.
2. Run the durability snapshot and restore verification against synthetic data and require
   `verified: true`.
3. Test clean, infected-test-vector, corrupt, oversized, OCR-heavy, timeout, retry, quarantine, and
   dead-letter ingestion paths.
4. Put the application behind an edge limiter/WAF and verify login, upload, question, and webhook
   abuse cases return bounded responses.
5. Connect centralized logs and alerts. Prove a forced readiness failure and a worker failure page
   the operator. Request logs include a safe `X-Request-ID` for correlation.
6. Publish the Google OAuth consent screen and finish any provider verification required for the
   intended audience.
7. Test account creation, session revocation, export, deletion, backup retention, and support
   handling on desktop and mobile.
8. If charging, complete provider acceptance plus purchase, webhook, entitlement, cancellation,
   refund, duplicate event, stale event, and customer-portal tests.
9. Run the held-out customer document evaluation and record retrieval and citation results.
10. Only then enable the launch switch and run the Chromium smoke journey from CI.

## Rollback

Disable public registration and uploads first, leave read-only access available if safe, preserve
logs and database/object versions, and follow the restore runbook. Never hide a data-loss or
security incident by redeploying over its evidence.
