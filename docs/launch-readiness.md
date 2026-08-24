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
| Answer quality | Organization-specific gold set, >=95% retrieval success, >=98% valid citations | Hybrid local semantic retrieval, reranking, diagnostics, and an 8-case synthetic regression benchmark implemented; customer-specific held-out evaluation pending |
| Customer AI | Server-managed provider, quotas, cost controls, no customer key required | Gateway, fallbacks, usage controls, and fake-provider tests implemented; provider/privacy acceptance and live configuration pending |
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
