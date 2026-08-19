# Architecture

## Goals and boundaries

EvidenceDesk optimizes for traceable answers, access decisions before retrieval, and a useful local demo with no paid model dependency. It is a single-service portfolio MVP, not a claim of production scale or regulatory compliance.

## Components

1. **FastAPI edge:** validates requests, authenticates API keys or OIDC bearer tokens, applies rate limits and security headers, and exposes document, answer, conversation, feedback, report, and insight endpoints.
2. **Policy layer:** maps identity to subject, tenant, role, and classification clearances. Those constraints are SQL predicates used before evidence reaches the answer workflow.
3. **Ingestion:** enforces type, byte, page, OCR-page, and resolution limits; validates PDF signatures; extracts embedded text; OCRs weak PDF pages when available; records extraction diagnostics; and creates page-aware overlapping chunks.
4. **Original store:** writes each original to a generated tenant/document path with containment checks and an atomic final rename. The storage interface is deliberately small so S3 can replace local disk.
5. **Evidence store:** SQLite records documents, pages, chunks, conversations, messages, feedback, and text-free audit events. FTS5 provides lexical candidates.
6. **Retrieval:** expands a small, controlled vocabulary of common organizational concepts, combines FTS rank, exact query overlap, metadata signals, and a second relevance pass, and deduplicates repeated sentences.
7. **Bounded answer workflow:** extracts supported statements, creates structured page citations, verifies every marker, labels evidence quality, and refuses when support is insufficient.
8. **Browser workspace:** combines conversation history, explicit document scope, evidence inspection, original preview, library lifecycle tools, feedback, reports, and admin-only operational signals.

## Why these decisions

- **Citation-first, not chatbot-first:** citations come from retrieved database rows and page records, never generated URLs.
- **Extractive by default:** source sentences are less conversational than model prose but are reproducible, free to run, and easier to verify.
- **Bounded agent behavior:** retrieve, synthesize, and verify are fixed steps. Documents cannot request tools or create new loops.
- **Authorization in retrieval:** filtered chunks never enter synthesis, citation responses, preview endpoints, reports, or history.
- **OCR only when needed:** OCR is expensive and imperfect, so the service first measures embedded text, then OCRs only weak pages within configured limits.
- **Original and extraction side by side:** the user can compare a cited extract with its source page instead of trusting a detached answer.
- **Honest hybrid retrieval:** local FTS, concept expansion, metadata, and reranking improve recall. This is not described as vector search or semantic embeddings.
- **Optional OIDC boundary:** JWT validation is implemented, while identity-provider setup, group mapping, key rotation, and end-to-end acceptance remain deployment work.

## Data model

- `documents`: tenant, owner, filename, type, size, digest, source, collection, classification, version/lifecycle fields, safe original location, extraction counts/method/warnings, and timestamps.
- `document_pages`: per-page extracted text and OCR indicator.
- `chunks` and `chunks_fts`: page-aware evidence, classification, injection-risk flag, and lexical index.
- `conversations` and `messages`: owner-scoped question/answer history with structured citations and workflow summaries.
- `feedback`: one categorized assessment per answer/user pair.
- `audit_events`: action metadata and identifiers, not document or question text.

Archive removes a document from normal retrieval without destroying it. Permanent delete removes metadata, pages, chunks, index rows, and the original file. Superseding a version marks the older record as superseded.

## Known limitations

- Controlled expansion only covers configured concepts; a pilot should evaluate embeddings and a reranker on an organization-specific test set.
- OCR depends on scan quality, language data, layout, and Tesseract; handwriting and complex tables remain difficult.
- Extraction and OCR are synchronous. A pilot should quarantine uploads and use isolated asynchronous workers with malware scanning.
- Local originals, SQLite, and in-process throttling are single-instance choices.
- OIDC has unit-tested token and claim mapping, but must be integration-tested with the selected identity provider.
- The extractive answerer intentionally favors evidence over conversational polish. Any future model must preserve structured citations, access predicates, refusal, and verification.
