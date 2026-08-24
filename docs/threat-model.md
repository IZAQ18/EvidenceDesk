# Lightweight threat model

| Risk | MVP control | Residual risk / next step |
|---|---|---|
| Cross-tenant or over-classification disclosure | Tenant and classification are mandatory retrieval predicates; tests exercise both | Use database row-level security and authorization integration tests in production |
| Unauthorized source/history access | Original, page, conversation, feedback, and report routes repeat tenant, clearance, owner, and role checks | Add database row-level security and centralized policy decision logging |
| Prompt injection inside a document | Treat content as data; flag common patterns; no document-driven tools; fixed workflow | Isolate future model/tool permissions and continuously expand adversarial evaluations |
| Malicious or oversized uploads | Protected staging, allowlist, byte/page/OCR-page/DPI limits, PDF signature and SHA-256 integrity checks, safe generated IDs | Antivirus/content disarm, true quarantine bucket/prefix, isolated parsers and OCR workers |
| OCR resource exhaustion | OCR only weak pages; bounded page count and DPI; upload/rate limits; durable job attempts and leases | Enforce container CPU/memory/time quotas, use SQS/dead-letter handling, and isolate workers in production |
| Misleading OCR output | Extraction method, page/word counts, warnings, page citations, and minimum-text checks | Confidence scoring, layout-aware OCR, and human review for consequential use |
| Unsupported answer | Extractive synthesis, structured citations, verification, refusal | Add entailment checks and human-reviewed evaluations before model rollout |
| Key theft or forged tokens | Constant-time API-key comparison; generic auth errors; configurable rate limit; RS256/JWKS, issuer, audience, expiry, and required-claim validation in OIDC mode | Short-lived tokens, identity-provider integration tests, Secrets Manager, WAF/API Gateway throttles |
| Original-file path attack | Generated storage paths, filename separation, containment checks, atomic writes, authorized download endpoint | Encrypted S3 with per-tenant prefixes, restrictive IAM, object scanning, and retention controls |
| Sensitive data in logs | Log identifiers and counts, not questions or document text | Central redaction rules, access controls, retention policy, audits |
| Dependency/parser vulnerability | Minimal dependencies, CI tests | Pin/scan dependencies and container images; patch SLA |
| Local database/original loss | Persistent Docker volume and explicit local storage boundary | Encrypted managed database/object storage, versioning, backups, and restore drills |

Security controls here are illustrative engineering measures. They do not constitute a certification or a guarantee.
