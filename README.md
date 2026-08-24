# EvidenceDesk

![EvidenceDesk — answers you can inspect](docs/assets/evidencedesk-social-card.png)

EvidenceDesk is a secure, citation-first knowledge assistant for organizational documents. It ingests TXT, Markdown, searchable PDFs, and scanned PDFs; keeps the original file and useful metadata; retrieves only evidence the caller may access; and returns page-level citations that open beside the answer. When the available evidence is weak, it says so instead of inventing a policy.

This is an intentionally honest portfolio MVP. It runs locally and demonstrates production-minded design, but it is **not** represented as GovCloud-ready, compliance-certified, or proven at large scale.

## What v0.8 includes

- Page-aware PDF extraction with bounded OCR fallback for weak pages.
- Original-file and extracted-page preview with highlighted cited passages.
- Controlled query expansion, FTS5 ranking, metadata signals, and a second relevance pass.
- Strong, partial, or insufficient evidence labels with an explanation.
- Explicit document scope, collections, versions, effective/expiry dates, supersession, archive/restore, and bulk actions.
- Persistent conversations, follow-up questions, per-answer feedback, and Markdown report export.
- Tenant and classification filtering before retrieval; role checks for administration; ownership checks for conversation data.
- API-key authentication for local use plus an optional standards-based OIDC/JWT validation adapter.
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
       ├──► ingestion ─► validate ─► extract ─► OCR weak pages ─► chunks
       │                         ├──► safe local original store
       │                         └──► SQLite metadata + pages + FTS5
       │
       └──► answer workflow
              retrieve → expand/rank → extract → verify citations
                                      └→ refuse when evidence is weak
```

The agent-like workflow is deliberately bounded. Retrieval, synthesis, and citation verification add value; open-ended tools and uncontrolled loops do not. SQLite FTS5 keeps the local demo portable. Controlled concept expansion improves common policy-language matches, but it is not an embedding model. See [architecture](docs/architecture.md), [delivery plan](docs/delivery-plan.md), [threat model](docs/threat-model.md), and [AWS path](docs/aws-deployment.md).

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
curl -X POST http://localhost:8000/v1/documents \
  -H "X-API-Key: dev-admin-key" \
  -F "file=@handbook.pdf" \
  -F "classification=internal" \
  -F "source=People Operations" \
  -F "collection=HR Policies" \
  -F "version=2.1"

curl -X POST http://localhost:8000/v1/answers \
  -H "X-API-Key: dev-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is the annual leave policy?","top_k":6}'
```

Every answer includes `grounded`, `evidence_quality`, `quality_reason`, structured citations, and a workflow trace containing step names rather than private model reasoning.

## Verify it

```bash
ruff check app tests
pytest --cov=app --cov-report=term-missing --cov-fail-under=80
```

The suite covers extraction and OCR fallback, original storage, page citations, retrieval expansion, scope, lifecycle and bulk operations, conversations and reports, feedback and metrics, tenant/classification isolation, conversation ownership, authorization, traversal protection, OIDC claim mapping, upload validation, refusal, and prompt-injection handling.
