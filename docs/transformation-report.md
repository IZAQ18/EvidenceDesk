# EvidenceDesk transformation report

EvidenceDesk began as a useful local citation-first RAG demonstration. It is now a protected,
portfolio-ready product pilot with production-minded application seams. This report separates
implemented and tested behavior from work that still requires managed services, commercial
accounts, real users, or operational acceptance.

## Covered in the transformation

### Evidence quality

- Authorization-filtered lexical and transparent local semantic retrieval.
- Passage reranking, page-aware citations, evidence-quality labels, and safe refusal.
- Citation/source inspector, original preview, scoped questions, conversations, feedback, reports,
  and text-free retrieval diagnostics.
- Synthetic regression evaluation for retrieval, citations, refusal, and tenant boundaries.

### Document handling

- TXT, Markdown, searchable PDF, and bounded scanned-PDF OCR support.
- Durable asynchronous job progress, leases, integrity checks, retries, quarantine, dead-letter
  handling, and safe removal.
- Private-clamd streaming adapter with fail-closed required mode.
- Secret-scrubbed parser/OCR subprocess with time, output, and POSIX resource ceilings.
- Local/S3 quarantine-to-final promotion and a standalone worker command.

### Identity and data boundaries

- API keys for local/pilot use and an OIDC/JWT validation seam.
- Optional individual accounts with scrypt passwords, revocable HttpOnly sessions, CSRF controls,
  verification, recovery, invitations, workspaces, roles, clearances, switching, member review,
  removal, ownership transfer, export, departure, and guarded deletion.
- SQLite for portable local use plus PostgreSQL migrations, advisory locking, bounded pooling, and
  PostgreSQL 16 CI integration.
- Local originals plus private S3/KMS/expected-owner support.

### Product and business presentation

- Dark/light themes, three accent palettes, responsive workspace, cinematic onboarding, clear
  empty/error/progress states, evidence inspector, and IZAQ/4UP signature system.
- Protected pilot access, public `/about` presentation, social artwork, deployment documentation,
  video/LinkedIn launch material, freemium plan preview, support presentation, and consented payment
  recipient details.
- Hosted-checkout boundaries and verified/idempotent Lemon Squeezy webhook intake. Direct bank/card
  data never enters EvidenceDesk.

### Delivery and recovery

- Docker, private GitHub repository, CI lint/test/coverage/container gates, and Render pilot.
- A browser-tracked five-task pilot walkthrough, anonymous scorecard, tenant-scoped storage,
  aggregate administrator metrics, export coverage, and deletion coverage for low-cost validation.
- Standard commercial AWS data-plane CloudFormation for private KMS/S3 and RDS PostgreSQL; explicitly
  not GovCloud and not claimed as applied production infrastructure.
- Guarded SQLite/local-to-PostgreSQL/S3 migration tooling.
- HMAC-authenticated, identifier-free backup/restore fingerprinting and a detailed restore runbook.

## Still required before an official paid public launch

1. Provision a cost-reviewed managed staging environment and obtain a successful independent
   PostgreSQL/S3 restore verification.
2. Deploy and monitor private ClamAV signature updates plus the standalone worker under container
   CPU, memory, filesystem, and network restrictions; add managed SQS/redrive if the selected
   topology needs it.
3. Connect a real sender domain/SMTP provider and accept verification, recovery, and invitation
   delivery; integrate the selected identity provider if organizational SSO is promised.
4. Acceptance-test the guarded subscription entitlement foundation on durable account-mode
   infrastructure, then add plan allowance enforcement, a customer billing portal, cancellation,
   refund/support workflows, tax/legal review, and international payout acceptance. Implemented
   code alone is not billing completion.
5. Add centralized logs, error tracking, metrics, alerts, dependency/container scanning, incident
   response, retention/privacy/terms documents, subprocessors, and an operator support process.
6. Run accessibility, mobile-browser, load, abuse, and customer-specific held-out answer-quality
   acceptance with real consenting pilot users and approved non-sensitive documents.

The remaining work is mostly deployment and operational proof, not another round of decorative
features. Until those gates pass, EvidenceDesk should be described as a protected product pilot and
strong portfolio project—not a compliance-certified or large-scale production SaaS.
