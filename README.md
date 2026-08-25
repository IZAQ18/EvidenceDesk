# EvidenceDesk

![EvidenceDesk — answers you can inspect](docs/assets/evidencedesk-social-card.png)

EvidenceDesk is a secure, citation-first knowledge assistant for organizational documents. It ingests TXT, Markdown, searchable PDFs, and scanned PDFs; keeps the original file and useful metadata; retrieves only evidence the caller may access; and returns page-level citations that open beside the answer. When the available evidence is weak, it says so instead of inventing a policy.

This is an intentionally honest portfolio MVP. It runs locally and demonstrates production-minded design, but it is **not** represented as GovCloud-ready, compliance-certified, or proven at large scale.

## What v1.0 includes

- Page-aware PDF extraction with bounded OCR fallback for weak pages.
- Durable asynchronous upload jobs with real security-check, extraction, OCR, indexing,
  ready, and failed states; bounded retries; staged-file integrity checks; and expired-worker
  lease recovery.
- Original-file and extracted-page preview with highlighted cited passages.
- Authorization-first hybrid retrieval combining database full-text search with deterministic,
  auditable local semantic feature vectors, reciprocal rank fusion, and a question-aware reranker.
- Strong, partial, or insufficient evidence labels with an explanation.
- Per-answer retrieval diagnostics and text-free admin quality metrics: authorized candidate
  count, lexical/semantic hits, rerank strength, refusal count, and retrieval latency.
- An optional server-managed Responses API gateway: visitors never paste a model key; only the
  question and selected authorized excerpts are sent; strict structured claims are checked for
  citation IDs, quantities, negation, and term support; every failure uses the extractive answer.
- Atomic daily request, token, and estimated-cost reservations plus a text-free model usage ledger
  and admin metrics. The protected free pilot keeps this provider gateway disabled.
- Explicit document scope, collections, versions, effective/expiry dates, supersession, archive/restore, and bulk actions.
- Persistent conversations, follow-up questions, per-answer feedback, and Markdown report export.
- Tenant and classification filtering before retrieval; role checks for administration; ownership checks for conversation data.
- API-key authentication for local use plus an optional standards-based OIDC/JWT validation adapter.
- An opt-in account foundation with scrypt password hashing, database-backed revocable sessions,
  HttpOnly cookies, CSRF protection, one-time hashed email-verification/password-reset tokens,
  SMTP delivery boundaries, scoped one-time workspace invitations and switching, bounded private
  exports, guarded deletion, member access review/removal, password-confirmed ownership transfer,
  safe workspace departure, and a separate private workspace for every registered user.
- A replaceable original-storage boundary with backward-compatible local files and an optional
  private S3 backend using tenant-scoped object keys, authenticated streaming, server-side
  encryption, KMS support, and expected-bucket-owner protection.
- A dual database boundary: portable SQLite for local/pilot use and PostgreSQL with versioned,
  advisory-lock-protected schema migrations, bounded connection pooling, readiness checks, and
  PostgreSQL full-text retrieval for durable multi-instance deployments. CI exercises the complete
  account-to-citation path on PostgreSQL 16.
- Rate limits, upload limits, security headers, safe file storage, prompt-injection flags, and text-free audit events.
- A dark-first editorial interface with layered evidence surfaces, gold/emerald trust states, a polished light theme, responsive mobile layout, keyboard focus states, and an IZAQ / 4UP design-engineering signature.
- Prompt starters, auto-growing questions, toast feedback, `/` question focus, and `Ctrl/Cmd + K` library search.
- A replayable cinematic intro with an IZAQ maker reveal, progress counter, skip control, mobile composition, and reduced-motion fallback.
- Aurora, Cobalt, and Ember accent palettes, interactive pointer light, spectrum navigation, and more expressive verified-evidence states.
- An IZAQ portrait signature across the access gate, cinematic intro, header, and product rail.
- A clearly labeled freemium product preview with Free, planned Pro, a founding Team pilot, and provider-hosted checkout/support links.
- Lemon Squeezy-compatible HMAC webhook verification, idempotent payment-event storage, masked customer hints, and admin-only payment notifications. Paid feature entitlements are not active yet.
- A public `/about` product page for LinkedIn visitors and payment-provider review, while the document workspace remains behind the private pilot gate.
- A larger IZAQ support presentation with consented MCB and NayaPay recipient IBANs plus a clearly labeled 40% developer charity pledge.
- Docker, GitHub Actions CI, evaluation/security tests, and a concrete path to standard AWS.

## Architecture at a glance

```text
Browser / API client
       │  API key or validated OIDC bearer token
       ▼
FastAPI ──► identity + tenant/classification/role policy
       │
       ├──► upload ─► protected staging ─► durable job queue
       │                                  └──► worker lease ─► validate
       │                                       ─► extract/OCR ─► chunks/index
       │                         ├──► private local or S3 original store
       │                         └──► SQLite or PostgreSQL metadata/search
       │
       └──► answer workflow
              retrieve → expand/rank → extract ──────────────► verify citations
                                      └→ optional bounded model ┘
                                      └→ refuse when evidence is weak
```

The agent-like workflow is deliberately bounded. Hybrid retrieval, passage reranking, extractive
synthesis, and citation verification add value; open-ended tools and uncontrolled loops do not.
SQLite FTS5 keeps the local demo portable. The local semantic encoder uses transparent sparse
policy features rather than a pretrained neural embedding model. See [architecture](docs/architecture.md),
[evaluation](docs/evaluation.md), [delivery plan](docs/delivery-plan.md), [threat model](docs/threat-model.md),
and [AWS path](docs/aws-deployment.md).

The optional model gateway is server-managed and fail-safe; it is not an autonomous agent. See the
[gateway privacy, validation, and cost controls](docs/model-gateway.md). OpenAI's current official
[data controls](https://developers.openai.com/api/docs/guides/your-data) explain why `store:false`
does not by itself promise zero provider retention.

The browser queues uploads through `/v1/ingestion-jobs` and polls the durable job record. The
legacy `/v1/documents` upload remains synchronous for API compatibility. The bundled worker is
crash-recoverable through database leases, but it runs inside the web container and is not an
isolated malware scanner. The AWS launch path replaces it with SQS and a separately constrained
ECS worker plus a real malware-scanning step.

## Run locally

Python 3.11+ is recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows; use `cp` elsewhere
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. Paste an API key from your `.env` file into the Connection box. For an unshared development run only, the fallback admin key is `dev-admin-key`. It is public by design and must never protect a shared service.

### Enable OCR locally

PyMuPDF OCR requires the Tesseract application and language data. On Windows, install Tesseract and set this if it is not detected:

```dotenv
EVIDENCEDESK_TESSDATA_PATH=C:/Program Files/Tesseract-OCR/tessdata
```

On Debian/Ubuntu, install `tesseract-ocr` and `tesseract-ocr-eng`. The Docker image includes them. The header and `GET /v1/capabilities` report the real OCR state; EvidenceDesk does not silently claim success for an unreadable scan.

### Configure authentication

Local API-key mode uses a JSON map in `.env`:

```dotenv
EVIDENCEDESK_API_KEYS={"replace-with-a-random-secret":{"subject":"admin","tenant_id":"demo","role":"admin","clearances":["public","internal","confidential"]}}
```

`api_key`, `oidc`, and `hybrid` authentication modes are supported. OIDC mode validates RS256 JWT signatures against a configured JWKS endpoint, issuer, audience, expiry, subject, tenant, role, and clearance claims. It is an adapter that must be connected to and tested with your real identity provider before deployment.

For private account testing, enable the account boundary instead of exposing API keys in the
browser:

```dotenv
EVIDENCEDESK_ACCOUNTS_ENABLED=true
EVIDENCEDESK_ACCOUNT_REGISTRATION_ENABLED=true
EVIDENCEDESK_ACCOUNT_SESSION_HOURS=168
EVIDENCEDESK_ACCOUNT_INVITATIONS_ENABLED=true
EVIDENCEDESK_ACCOUNT_EXPORT_ENABLED=true
EVIDENCEDESK_ACCOUNT_DELETION_ENABLED=true
```

Each registration creates a user, private workspace, and administrator membership. With email
verification disabled, it also creates a revocable session immediately; verified mode keeps the
account pending until its one-time link is confirmed. Existing tenant filters isolate documents,
retrieval, conversations, reports, feedback, and metrics by workspace. This is a production-minded
boundary, not a claim that public account service is finished.

Verified signup and recovery are separately opt-in. Production fails closed unless SMTP uses
STARTTLS and an HTTPS public URL is configured:

```dotenv
EVIDENCEDESK_ACCOUNT_EMAIL_VERIFICATION_REQUIRED=true
EVIDENCEDESK_ACCOUNT_PASSWORD_RESET_ENABLED=true
EVIDENCEDESK_EMAIL_DELIVERY_BACKEND=smtp
EVIDENCEDESK_SMTP_HOST=smtp.example.com
EVIDENCEDESK_SMTP_PORT=587
EVIDENCEDESK_SMTP_USERNAME=server-only-user
EVIDENCEDESK_SMTP_PASSWORD=server-only-secret
EVIDENCEDESK_SMTP_FROM_ADDRESS=EvidenceDesk <accounts@example.com>
EVIDENCEDESK_SMTP_STARTTLS=true
EVIDENCEDESK_PUBLIC_BASE_URL=https://app.example.com
```

Tokens are random, stored only as SHA-256 hashes, expire, rotate when reissued, and can be used
once. Resetting a password revokes every existing session. Unknown-account requests receive the
same response as known accounts. Real sender-domain authentication, delivery monitoring, abuse
controls, and provider acceptance remain deployment gates; see the
[account email runbook](docs/account-email.md), [account controls](docs/account-controls.md), and
[launch readiness](docs/launch-readiness.md).

### Configure durable originals

Local storage remains the default for development and the protected synthetic demo. A standard
AWS deployment can store originals in a private S3 bucket without changing the ingestion or
citation APIs:

```dotenv
EVIDENCEDESK_DOCUMENT_STORE_BACKEND=s3
EVIDENCEDESK_S3_BUCKET=evidencedesk-private-dev
EVIDENCEDESK_S3_PREFIX=evidencedesk/originals
EVIDENCEDESK_S3_REGION=us-east-1
EVIDENCEDESK_S3_KMS_KEY_ID=alias/evidencedesk-documents
EVIDENCEDESK_S3_EXPECTED_BUCKET_OWNER=123456789012
```

Credentials are deliberately not configuration fields: boto3 reads them from the normal AWS
credential provider chain, so ECS should use a least-privilege task role. Objects are never made
public or exposed with a direct browser URL; EvidenceDesk authorizes the request first and then
streams the original through the application. This adapter has local automated tests but has not
yet been represented as a deployed AWS production workload.

### Configure PostgreSQL

SQLite remains the zero-setup local default. The durable database adapter is selected explicitly:

```dotenv
EVIDENCEDESK_DATABASE_BACKEND=postgres
EVIDENCEDESK_DATABASE_URL=postgresql://user:password@host:5432/evidencedesk?sslmode=verify-full
```

The URL is handled as a secret and is never returned by an API. Production configuration fails
closed unless PostgreSQL TLS is required. Startup takes a transaction-scoped PostgreSQL advisory
lock, creates the migration ledger, and applies each unapplied schema version exactly once. The
PostgreSQL search path uses a GIN index and native full-text ranking while preserving the same
tenant, classification, lifecycle, and citation boundaries as SQLite.

Each process uses a bounded connection pool (one warm connection and five maximum by default).
AWS/ECS can inject the RDS host, database, username, and password as separate environment values;
EvidenceDesk encodes them into the connection URL internally. `GET /ready` checks both the chosen
database and original store, while `GET /health` remains the lightweight process/database check.

GitHub CI starts PostgreSQL 16 and tests migration, registration, login, ingestion, original-file
streaming, retrieval, citation generation, and cross-workspace isolation. This is integration
evidence, not a claim that EvidenceDesk has operated an AWS RDS production workload. A guarded
SQLite/local-to-PostgreSQL/S3 copy tool and an unapplied CloudFormation data-plane template are
included; AWS acceptance and backup/restore drills remain launch gates. Follow the
[AWS migration and rollback runbook](docs/aws-deployment.md).

The migration command is a read-only integrity preflight unless `--execute` is supplied:

```bash
python -m app.migrate_data --source-db data/evidencedesk.db --source-originals data/originals
```

### Optional server-managed model synthesis

Keep the gateway disabled for the free pilot. To evaluate it privately, store a project key only
in the server environment, copy the provider's current input/output prices, set small daily
request/token/cost ceilings, and also set a hard provider-side project budget. Visitors do not need
and cannot view this key.

```dotenv
EVIDENCEDESK_MODEL_GATEWAY_ENABLED=true
EVIDENCEDESK_MODEL_API_KEY=server-only-project-key
EVIDENCEDESK_MODEL_NAME=gpt-5-mini
EVIDENCEDESK_MODEL_INPUT_PRICE_PER_MILLION_USD=replace-with-current-price
EVIDENCEDESK_MODEL_OUTPUT_PRICE_PER_MILLION_USD=replace-with-current-price
EVIDENCEDESK_MODEL_DAILY_REQUEST_LIMIT=25
EVIDENCEDESK_MODEL_DAILY_TOKEN_LIMIT=100000
EVIDENCEDESK_MODEL_DAILY_COST_LIMIT_CENTS=100
```

EvidenceDesk refuses to enable the gateway without a key, HTTPS endpoint, and non-zero prices.
The application estimate is useful for protection and visibility, while the provider dashboard
remains the billing source of truth. Use only synthetic documents until privacy review is complete.

### Docker

```bash
docker compose up --build
```

Docker starts in production mode and fails closed unless authentication is configured. Originals and SQLite data live in the mounted data volume.

### Protected free pilot

The recommended portfolio pilot uses Render's free Docker web service with a private GitHub repository. A separate access-code page creates an HttpOnly session, production API documentation is hidden, search engines are told not to index the site, and two fictional Northstar Labs documents are restored after an ephemeral restart. This is privacy-in-depth, not a claim that browser-delivered interface code is invisible.

See the [Render pilot guide](docs/render-pilot.md) and [code-protection notes](docs/code-protection.md). The free instance sleeps after 15 minutes and its local storage can reset, so use it only for synthetic reviewer demonstrations—not sensitive documents or an official production launch. The [Koyeb guide](docs/koyeb-pilot.md) is retained as a paid-plan alternative.

### Freemium and revenue preview

The **Plans** view shows the intended customer journey without pretending that billing is finished. The current Free pilot is a protected, synthetic-data demonstration. Pro remains a roadmap concept. A founding Team pilot can open an approved hosted checkout, but payment still does not automatically activate application entitlements.

If a payment or creator-support provider approves your account, configure its hosted HTTPS link for product checkout. The public support panel also displays developer-authorized direct-transfer recipient details; payer credentials are never collected by EvidenceDesk:

```dotenv
EVIDENCEDESK_CHECKOUT_URL=https://your-store.example/checkout/buy/your-product
EVIDENCEDESK_SUPPORT_URL=https://your-provider.example/your-support-page
EVIDENCEDESK_PAYMENT_WEBHOOK_SECRET=generated-in-the-provider-dashboard
```

The webhook endpoint is `/v1/payments/webhook`. It verifies the provider signature before storing a minimal event and never receives card or MCB account details. The safest first commercial offer is a **founding team pilot**: one organization pays for controlled onboarding, an agreed synthetic/non-sensitive evaluation, and a clear success report. Subscription checkout should follow only after durable storage, accounts, usage metering, entitlements, terms, privacy, and refunds exist. See the [monetization plan](docs/monetization.md).

The public marketing and provider-review URL is `/about`. It describes the product, plans, honest limitations, privacy boundary, and payment/refund status without exposing the protected workspace. Use the full deployed `https://...onrender.com/about` address in LinkedIn and payment-provider onboarding.

## A simple demo

1. Connect with an admin key.
2. Open **Library** and upload a synthetic employee handbook or code of conduct.
3. Add collection, source, version, and classification metadata.
4. Select the document and ask: “What is the annual leave policy?”
5. Open a citation to show the exact supporting page and original PDF.
6. Ask a follow-up, record feedback, then export the conversation report.

If the uploaded code of conduct contains no leave policy, the correct outcome is an insufficient-evidence response—not a guessed answer.

## Screenshots

### Cinematic product introduction

![EvidenceDesk cinematic introduction with IZAQ maker reveal](docs/assets/intro-vivid.png)

### Grounded answer with highlighted source evidence

![EvidenceDesk vivid workspace showing a cited annual-leave answer](docs/assets/workspace-vivid.png)

### Document library and lifecycle controls

![EvidenceDesk dark library showing a synthetic employee handbook](docs/assets/library-dark.png)

## Portfolio tour and workflow ad

- Open the [plain-English presentation](showcase/index.html) for a non-technical, developer-level walkthrough of the problem, workflow, security model, stack, AWS path, and honest project limits.
- Watch the [70-second workflow advertisement](media/ad/evidencedesk-workflow-ad.mp4). It uses local narration, burned-in captions, real product screenshots, and AI-generated abstract background art.
- Review the [ad source notes](media/ad/README.md) for the script, free/local production tools, image prompt, and truthfulness boundary.

EvidenceDesk is presented as a local portfolio MVP. These assets do not claim GovCloud authorization, enterprise-scale operation, or production deployment history.

## API example

```bash
curl -X POST http://localhost:8000/v1/ingestion-jobs \
  -H "X-API-Key: dev-admin-key" \
  -F "file=@handbook.pdf" \
  -F "classification=internal" \
  -F "source=People Operations" \
  -F "collection=HR Policies" \
  -F "version=2.1"

# Poll the returned job id until status is ready or failed.
curl http://localhost:8000/v1/ingestion-jobs/JOB_ID \
  -H "X-API-Key: dev-admin-key"

curl -X POST http://localhost:8000/v1/answers \
  -H "X-API-Key: dev-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is the annual leave policy?","top_k":6}'
```

Every answer includes `grounded`, `evidence_quality`, `quality_reason`, structured citations, a
`generation` summary (mode, text-free usage, cost estimate, and fallback reason), and a workflow
trace containing step names rather than private model reasoning.

Run the real synthetic answer-quality benchmark with:

```bash
python -m app.evaluation
```

It currently contains eight regression cases and reports retrieval, citation, refusal,
security-boundary, and p95 latency measurements. A perfect result on this small synthetic set is
not represented as customer accuracy or large-scale production evidence; see the
[evaluation interpretation](docs/evaluation.md).

## Verify it

```bash
ruff check app tests
pytest --cov=app --cov-report=term-missing --cov-fail-under=80
```

The suite covers durable ingestion jobs and lease recovery, extraction and OCR fallback, original
storage, page citations, retrieval expansion, scope, lifecycle and bulk operations, conversations
and reports, feedback and metrics, tenant/classification isolation, conversation ownership,
authorization, traversal protection, OIDC claim mapping, upload validation, refusal,
prompt-injection handling, invalid model citations/claims, provider timeout, quota exhaustion,
key non-disclosure, usage-metadata privacy, migration integrity preflight, readiness checks, and
private/recoverable AWS template defaults. Account tests cover pending verification, token hashing,
expiry, resend rotation, replay rejection, generic recovery responses, SMTP STARTTLS, password
replacement, and full session revocation on both SQLite and PostgreSQL paths.
