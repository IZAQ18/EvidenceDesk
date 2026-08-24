# Architecture

## Goals and boundaries

EvidenceDesk optimizes for traceable answers, access decisions before retrieval, and a useful local demo with no paid model dependency. It is a single-service portfolio MVP, not a claim of production scale or regulatory compliance.

## Components

1. **FastAPI edge:** validates requests, authenticates account sessions, API keys, or OIDC bearer tokens, applies rate limits and security headers, and exposes document, answer, conversation, feedback, report, and insight endpoints.
2. **Account boundary:** optional self-registration creates an isolated workspace and administrator membership. Passwords use salted scrypt hashes; random sessions are stored only as SHA-256 token hashes, sent through HttpOnly cookies, expire server-side, and require a per-session CSRF token for mutations.
3. **Policy layer:** maps identity to subject, tenant, role, and classification clearances. Those constraints are SQL predicates used before evidence reaches the answer workflow.
4. **Ingestion:** stages an authorized upload, records a durable job, and returns immediately. A database-leased worker rechecks type, byte count, digest, and PDF signature; enforces page, OCR-page, and resolution limits; extracts embedded text; OCRs weak PDF pages when available; records extraction diagnostics; and atomically creates page-aware chunks and the searchable document. Failed jobs keep a safe explanation and can be retried within a configured attempt limit.
5. **Original store:** writes each original behind a small storage interface. Local mode uses generated tenant/document paths, containment checks, fsync, and an atomic final rename. Optional S3 mode uses tenant-scoped keys, server-side AES-256 or KMS encryption, expected-owner checks, and authenticated application streaming; it never creates a public object URL.
6. **Evidence store:** a dual adapter records documents, pages, chunks, conversations, messages, feedback, accounts, workspaces, sessions, and text-free audit events. SQLite keeps local setup portable. PostgreSQL uses versioned migrations protected by a transaction advisory lock and is exercised in CI against PostgreSQL 16.
7. **Retrieval:** first applies tenant, classification, lifecycle, and explicit document scope in SQL. It then combines database full-text results with a bounded authorized candidate pool scored by transparent local semantic feature vectors. Reciprocal rank fusion and a question-aware passage reranker reward direct term coverage, domain concepts, metadata, and answer shapes such as quantities or deadlines. SQLite uses FTS5; PostgreSQL uses a GIN-backed `to_tsvector` index and native web-search queries.
8. **Bounded answer workflow:** selects supported source passages, creates structured page citations, verifies every marker and exact excerpt, labels evidence quality, and refuses when support is insufficient. No generative model is active in the free pilot.
9. **Browser workspace:** combines account-aware onboarding, conversation history, explicit document scope, evidence inspection, original preview, library lifecycle tools, feedback, reports, and admin-only operational signals.

## Why these decisions

- **Citation-first, not chatbot-first:** citations come from retrieved database rows and page records, never generated URLs.
- **Extractive by default:** source sentences are less conversational than model prose but are reproducible, free to run, and easier to verify.
- **Bounded agent behavior:** retrieve, synthesize, and verify are fixed steps. Documents cannot request tools or create new loops.
- **Authorization in retrieval:** filtered chunks never enter synthesis, citation responses, preview endpoints, reports, or history.
- **OCR only when needed:** OCR is expensive and imperfect, so the service first measures embedded text, then OCRs only weak pages within configured limits.
- **Original and extraction side by side:** the user can compare a cited extract with its source page instead of trusting a detached answer.
- **Honest hybrid retrieval:** the local sparse semantic vector encoder is deterministic,
  dependency-free, and auditable. It improves familiar policy-language variation but is explicitly
  not described as a pretrained neural embedding model.
- **Optional OIDC boundary:** JWT validation is implemented, while identity-provider setup, group mapping, key rotation, and end-to-end acceptance remain deployment work.

## Data model

- `documents`: tenant, owner, filename, type, size, digest, source, collection, classification, version/lifecycle fields, safe original location, extraction counts/method/warnings, and timestamps.
- `document_pages`: per-page extracted text and OCR indicator.
- `chunks` and `chunks_fts`: page-aware evidence, classification, injection-risk flag, and lexical index.
- `conversations` and `messages`: owner-scoped question/answer history with structured citations and workflow summaries.
- `feedback`: one categorized assessment per answer/user pair.
- `audit_events`: action metadata and identifiers, not document or question text.
- `ingestion_jobs`: tenant-scoped staged-upload metadata, progress, bounded attempts, worker lease,
  safe failure details, and the final document identifier; never extracted document text.

Archive removes a document from normal retrieval without destroying it. Permanent delete removes metadata, pages, chunks, index rows, and the original file. Superseding a version marks the older record as superseded.

## Known limitations

- Local semantic features cover configured organizational concepts. A paid pilot should compare
  a selected neural embedding model and reranker on an organization-specific held-out set before
  enabling them.
- OCR depends on scan quality, language data, layout, and Tesseract; handwriting and complex tables remain difficult.
- Browser ingestion is asynchronous and survives web-request timeouts through durable database
  jobs and expiring worker leases. The bundled worker still shares the web container; production
  should use a separate constrained worker, a managed queue, a true quarantine prefix/bucket,
  and malware scanning before extraction.
- SQLite and in-process throttling are single-instance choices. PostgreSQL removes the database
  single-instance constraint, but connection pooling, RDS integration, backups, and restore drills
  remain deployment work.
- The S3 adapter is implemented and unit-tested with a fake client, but real IAM, bucket policy,
  lifecycle, access logging, malware quarantine, and restore behavior still require integration
  testing in the selected AWS account.
- OIDC has unit-tested token and claim mapping, but must be integration-tested with the selected identity provider.
- The account boundary is intentionally backed by SQLite for local acceptance testing. Public
  registration must wait for managed PostgreSQL, verified email, password recovery, invitations,
  deletion/export controls, and abuse monitoring.
- The extractive answerer intentionally favors evidence over conversational polish. Any future model must preserve structured citations, access predicates, refusal, and verification.
