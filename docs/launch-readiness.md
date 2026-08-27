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
| Account safety | Individual sign-in, verified email, recovery, revocable sessions, CSRF protection, tenant isolation | Verification/recovery, scoped invitations, and workspace switching implemented and tested; real SMTP/domain and managed-data acceptance pending |
| Durable data | Managed PostgreSQL, private object storage, backups, restore exercise | Adapters, bounded pooling, readiness checks, guarded copy tool, restore verifier, and unapplied AWS data-plane template implemented; PostgreSQL 16 path tested in CI. Managed-resource provisioning and operator-run restore acceptance pending |
| Ingestion | Async processing, malware scan, progress, resilient OCR and table handling | Quarantine/promotion, private-clamd adapter, bounded parser subprocess, standalone worker command, dead-letter visibility, progress, retry, integrity, and lease recovery implemented and tested. Live private-scanner acceptance and advanced table extraction pending |
| Answer quality | Organization-specific gold set, >=95% retrieval success, >=98% valid citations | Hybrid local semantic retrieval, reranking, diagnostics, and an 8-case synthetic regression benchmark implemented; customer-specific held-out evaluation pending |
| Customer AI | Server-managed provider, quotas, cost controls, no customer key required | Gateway, fallbacks, usage controls, and fake-provider tests implemented; provider/privacy acceptance and live configuration pending |
| Billing | Hosted checkout, verified webhooks, entitlements, portal, cancellation, refunds | Disabled-by-default, account-bound Pro checkout, ordered entitlements, atomic Free/Pro allowances, and usage UI implemented and locally tested; live provider acceptance, portal, policies, refund flow, and durable deployment pending |
| User control | Export, document deletion, conversation deletion, complete account deletion | Bounded workspace export, guarded account deletion, member removal, safe departure, and ownership transfer implemented; operational cleanup/retention acceptance pending |
| Trust | Terms, privacy, retention, subprocessors, support and honest limitations | Pilot notices exist; commercial documents pending |
| Operations | Monitoring, error tracking, alerts, backups, incident procedure | Readiness checks, structured request logs, admin ingestion/retrieval/model metrics, dependency audit in CI, and restore tooling implemented; centralized monitoring, alerts, and operator drills pending |
| Experience | First cited answer within two minutes; desktop/mobile/accessibility acceptance | Responsive layouts, reduced-motion support, labeled controls, and static accessibility regressions implemented; live device, browser, keyboard, and screen-reader acceptance pending |

## Delivery order

1. Account foundation and private workspaces.
2. PostgreSQL, object storage, migrations, and background jobs.
3. Semantic retrieval, reranking, grounded model synthesis, and expanded evaluation.
4. Server-managed model usage, quotas, and cost controls.
5. Email-provider/domain acceptance, invitations, deletion, and organization controls.
6. Complete Lemon Squeezy live acceptance, customer portal, and billing operations around the guarded Pro entitlement and allowance foundation.
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

This closes the request-timeout and invisible-progress gap for the MVP. The later secure-ingestion
milestone adds a quarantine namespace, private-clamd adapter, resource-limited parser process,
standalone worker entry point, and explicit quarantined/dead-letter operations. A live scanner,
separate constrained worker service, and managed SQS/DLQ still require deployment acceptance.

## Milestone 3 evidence

The answer-intelligence path now provides:

- authorization-first candidate selection before any semantic scoring;
- deterministic local semantic feature vectors combined with SQLite/PostgreSQL full-text search;
- reciprocal rank fusion and question-aware passage reranking;
- clearer extractive answers whose citation markers and exact excerpts are verified;
- per-answer retrieval diagnostics stored without question or document text;
- an admin summary of semantic hits, candidate volume, latency, and refusals; and
- an executable eight-case synthetic benchmark enforced in CI.

The local benchmark currently passes all eight cases. This is regression evidence for that small
synthetic corpus, not a claim of 100% customer accuracy. No generative model or pretrained neural
embedding provider is active in the free pilot. Those require provider selection, privacy terms,
cost controls, adversarial evaluation, and a customer-specific held-out benchmark.

## Milestone 4 evidence

The optional server-managed AI path now provides:

- a server-only project key, with no customer or browser API-key field;
- a bounded request containing the current question and selected authorized excerpts—not full
  files, whole documents, or conversation history;
- strict structured output with server-owned citation markers;
- rejection of unknown citation IDs, unsupported quantities, changed negation, weak term support,
  invalid responses, timeouts, and instruction-like evidence;
- atomic daily request, token, and estimated-cost reservations before provider calls;
- a text-free usage ledger and admin visibility for completions, fallbacks, tokens, latency, and
  estimated cost; and
- the local extractive answer as a fail-safe for every gateway failure.

The gateway remains disabled on the public Render pilot. Enabling it requires a private server
key, current provider prices, a deliberate application ceiling, a provider-side hard budget,
privacy/subprocessor review, and customer-specific evaluation. Mechanical claim checks are not a
proof that every paraphrase is semantically entailed.

## Milestone 5 evidence

The durable-data deployment seam now provides:

- a bounded PostgreSQL connection pool with startup failure and shutdown cleanup;
- a public, low-detail readiness endpoint that checks the database and original storage;
- separate RDS component-secret configuration with safe credential encoding and required
  production TLS;
- a read-only-by-default migration preflight that verifies local original sizes and SHA-256
  digests;
- an explicit copy mode that requires idle ingestion, an empty PostgreSQL target, private S3,
  unique-prefix object copies, S3 round trips, a single database transaction, and final row-count
  verification; and
- an unapplied CloudFormation data-plane template with private RDS networking, managed secrets,
  encrypted/versioned S3, backups, retain/snapshot behavior, and scoped ECS data access.

No AWS resources were created by this milestone. Public registration remains disabled until a
managed target is created, migration and restore acceptance pass, and account recovery is ready.

## Milestone 6 evidence

The opt-in account lifecycle now provides:

- pending accounts that cannot authenticate before email verification;
- purpose-bound verification and reset tokens stored only as SHA-256 hashes;
- short expiries, rotation on resend, one-time consumption, and replay rejection;
- generic request responses that do not directly reveal whether an email is registered;
- a provider-neutral SMTP boundary that requires STARTTLS in production and never logs message
  bodies, recipients, passwords, or tokens;
- password changes that revoke every database session and clear the browser cookie; and
- accessible verification, forgotten-password, and reset screens that keep GET requests
  read-only so automated mail-link scanners cannot consume tokens.

The Render pilot explicitly keeps accounts, registration, verification, reset, invitations,
export, and deletion disabled. Public registration still requires managed-data acceptance, an
authenticated sender domain, delivery and abuse monitoring, privacy/retention review, and
operational acceptance of the implemented controls.

## Milestone 7 evidence

The opt-in workspace and privacy controls now provide:

- administrator invitations with bounded roles and classification clearances;
- expiring, rotating, one-time invitation links stored only as SHA-256 hashes;
- existing-account and invitation-created-account acceptance paths with email matching and CSRF;
- session-rotating workspace switching across verified memberships;
- administrator-only, size-bounded ZIP exports that omit credentials, token hashes, and storage
  paths while including available originals;
- password plus explicit-word account deletion, with refusal when an owned workspace has other
  members; and
- database-first removal followed by best-effort original cleanup that logs no object names.

SQLite acceptance tests cover invitations, replay, CSRF, switching, export content, secret
exclusion, deletion, storage cleanup, and disabled-feature behavior. The PostgreSQL CI path covers
the invitation/membership schema and flow. This does not claim managed SMTP, S3 cleanup, backup
expiry, or legal/privacy acceptance.

## Milestone 8 evidence

The shared-workspace lifecycle now provides:

- administrator member review plus bounded role and classification-clearance changes;
- immediate revocation of the affected member's sessions after access changes or removal;
- password-confirmed removal that preserves the person's account and unrelated workspaces;
- password-confirmed ownership transfer only to an existing member, with automatic full-clearance
  administrator access for the new owner and protection against owner removal/demotion;
- safe non-owner departure that rotates the browser into another workspace and refuses to orphan
  a one-workspace account; and
- a tested handoff in which the former owner deletes their account while the transferred workspace
  and its remaining membership survive.

These controls close the MVP's application-level ownership-transfer gap. Managed identity,
periodic access certification, phishing-resistant step-up authentication, real SMTP/S3 cleanup,
backup expiry, and operational recovery still require deployment acceptance before public accounts.

## Milestone 9 evidence

The durable-staging acceptance seam now provides:

- a read-only PostgreSQL connection-pool open path that does not apply schema changes;
- two-pass fingerprints for every application table, with refusal if data changes during capture;
- streaming size and SHA-256 verification for every database-referenced original under bounded row,
  object, and byte limits;
- managed-target refusal unless production PostgreSQL and KMS-encrypted S3 expected-owner settings
  are configured;
- read-only checks for S3 versioning, KMS default encryption, and all public-access blocks;
- content-free, identifier-free baseline and restore-result artifacts authenticated with an
  operator-only HMAC key;
- automated tests for successful comparison, database drift, modified originals, baseline tampering,
  local-target refusal, and S3 control failure; and
- clearer RDS/S3 recovery steps plus retained automated backups and PostgreSQL log exports in the
  unapplied CloudFormation template.

No managed resources or backups were created. This closes the tooling gap, not the operational
gate: an operator must still provision a cost-reviewed staging target, capture a baseline, restore
RDS separately, recover a synthetic S3 version, and obtain `verified: true` before public accounts.

## Milestone 10 evidence

The secure-ingestion path now provides:

- quarantine-first local/S3 storage and explicit copy-before-commit promotion;
- a bounded private-clamd INSTREAM client with required/fail-closed startup and readiness modes;
- scanning on both asynchronous and legacy synchronous upload routes;
- a secret-scrubbed parser/OCR subprocess with wall-clock, extracted-text, POSIX CPU, memory, and
  open-file ceilings;
- a standalone worker command so the API task can stop consuming jobs;
- non-retryable `quarantined` detections and an explicit database-backed `dead_letter` lane after
  bounded attempts;
- safe removal of quarantined staged files and admin Insights counts for pending, quarantine, and
  dead-letter work; and
- adversarial tests for scanner detection/outage, retry exhaustion, synchronous-route bypass,
  storage promotion, protocol framing, secret inheritance, and isolated extraction.

This completes the application seam and local evidence. The public Render pilot enables parser
process isolation but intentionally has no private ClamAV service and must use synthetic files only.
Live signature updates, a separately constrained task, managed SQS redrive, and an acceptance drill
remain deployment work—not completed production experience.
