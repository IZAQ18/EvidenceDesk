# EvidenceDesk launch readiness

EvidenceDesk will keep its public workspace in protected portfolio-pilot mode until the product
gates below are proven. A private acceptance build is not the public launch.

## Product promise

EvidenceDesk is the trustworthy policy and procedure assistant for small organizations. It should
answer from approved documents, open the evidence behind every supported claim, and refuse when
the organization has not supplied enough information.

## Release gates

| Gate | Measurable definition | Current state |
| --- | --- | --- |
| Account safety | Individual sign-in, revocable sessions, CSRF protection, tenant isolation | Foundation implemented; email verification and recovery pending |
| Durable data | Managed PostgreSQL, private object storage, backups, restore exercise | PostgreSQL adapter/migrations and private S3 adapter implemented; PostgreSQL 16 path tested in CI. AWS integration, pooling, backups, and restore exercise pending |
| Ingestion | Async processing, malware scan, progress, resilient OCR and table handling | Durable jobs, visible progress, bounded retry, integrity verification, and lease recovery implemented; isolated workers, malware scanning, and advanced table extraction pending |
| Answer quality | Organization-specific gold set, >=95% retrieval success, >=98% valid citations | Evaluation harness exists; benchmark expansion pending |
| Customer AI | Server-managed provider, quotas, cost controls, no customer key required | Pending |
| Billing | Hosted checkout, verified webhooks, entitlements, portal, cancellation, refunds | Verified event intake exists; entitlements pending |
| User control | Export, document deletion, conversation deletion, complete account deletion | Partial; account deletion pending |
| Trust | Terms, privacy, retention, subprocessors, support and honest limitations | Pilot notices exist; commercial documents pending |
| Operations | Monitoring, error tracking, alerts, backups, incident procedure | Pending |
| Experience | First cited answer within two minutes; desktop/mobile/accessibility acceptance | Account journey validated locally; broader acceptance pending |

## Delivery order

1. Account foundation and private workspaces.
2. PostgreSQL, object storage, migrations, and background jobs.
3. Semantic retrieval, reranking, grounded model synthesis, and expanded evaluation.
4. Server-managed model usage, quotas, and cost controls.
5. Email verification, recovery, invitations, deletion, and organization controls.
6. Lemon Squeezy subscriptions and server-side entitlements.
7. Policy Audit: contradictions, missing dates, version differences, and evidence gaps.
8. Monitoring, backups, legal pages, accessibility, performance, and private acceptance.
9. Public launch only after every blocking gate passes.

## Milestone 1 evidence

The optional account mode now provides:

- salted scrypt password hashes rather than stored passwords;
- random session tokens stored only as SHA-256 hashes;
- Secure/HttpOnly/SameSite cookies in production;
- server-side expiry and logout revocation;
- per-session CSRF protection for mutating authenticated requests;
- a private workspace and administrator membership for each account;
- retrieval and document isolation through the existing tenant predicates;
- a polished sign-in and account-aware workspace with no API-key prompt;
- automated tests covering registration, weak passwords, login failure, CSRF, forgery,
  revocation, duplicate accounts, invite-only mode, and cross-account isolation.

This milestone deliberately does not claim that public registration is ready. Keep
`EVIDENCEDESK_ACCOUNTS_ENABLED=false` on the existing Render portfolio pilot until the durable-data
and account-recovery gates are complete.

## Milestone 2 evidence

The document upload path now provides:

- an immediate `202 Accepted` job response from the browser upload API;
- durable tenant- and classification-scoped job records in SQLite and PostgreSQL;
- real security-check, extraction, OCR inspection, indexing, ready, and failed progress states;
- bounded attempts, user-triggered retry, failed-job cleanup, and safe error actions;
- byte-count and SHA-256 integrity verification before a staged original is processed;
- worker leases that let an expired job be reclaimed after a process crash;
- document visibility only after pages and searchable chunks have been committed; and
- PostgreSQL migration coverage plus an end-to-end job-to-citation CI path.

This closes the request-timeout and invisible-progress gap for the MVP. It does not claim that
the in-process worker is a production quarantine system. Malware scanning, parser isolation,
SQS/ECS worker deployment, job observability, and dead-letter operations remain launch gates.
