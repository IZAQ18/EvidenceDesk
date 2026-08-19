# Delivery plan

## MVP — included here

- FastAPI service and responsive evidence workspace.
- TXT, Markdown, searchable-PDF, and bounded scanned-PDF OCR ingestion with extraction diagnostics and duplicate-resistant hashes.
- Tenant/classification-aware FTS retrieval with optional document scope.
- Controlled concept expansion, metadata signals, relevance reranking, and evidence-quality labels.
- Grounded extractive answers, page-level citations, safe refusal, and citation verification.
- Original/page preview, collections, versioning, supersession, archive/restore, and bulk actions.
- Persistent conversations, follow-ups, feedback, Markdown reports, and admin insight metrics.
- API-key roles, optional OIDC/JWT validation, upload limits, security headers, rate limiting, prompt-injection flags, safe paths, and non-sensitive audit logs.
- Automated evaluation/security tests, Docker Compose, and CI.

## Pilot hardening — next 2–4 weeks

- Connect the OIDC adapter to a managed identity provider and acceptance-test organization-specific claim mapping.
- Replace local originals with encrypted object storage; scan uploads asynchronously.
- Move OCR to isolated workers; add PostgreSQL/pgvector hybrid retrieval, reranking, and a model gateway with strict schema output.
- Add organization-specific human-reviewed evaluation sets, tracing, backup/restore drills, and dependency/image scanning.

## Production readiness — organization dependent

- Infrastructure as code, separate accounts/environments, private networking, WAF, secret rotation, alarms, and runbooks.
- Load, failure, penetration, privacy, and disaster-recovery testing.
- Retention/deletion policy, data classification mapping, vendor review, threat-model review, and any required compliance evidence.

“Production-ready” is an evidence threshold, not a repository feature. This project deliberately does not claim that threshold has been met.
