# Path to standard AWS

This is a deployment design, not a claim that the repository has operated at production scale.

## Small pilot

- Build the container in CI and store it in Amazon ECR.
- Run the API as an ECS Fargate service behind an Application Load Balancer with TLS from ACM.
- Put originals in an encrypted S3 quarantine bucket; send uploads through an SQS-backed malware-scanning and OCR ingestion task with strict resource limits.
- Replace SQLite with Amazon RDS for PostgreSQL plus `pgvector`, or use OpenSearch when corpus/search needs justify it.
- Store application secrets in Secrets Manager and encrypt data with customer-managed KMS keys where policy requires it.
- Use Cognito or the organization’s OIDC provider; translate identity claims into tenant and classification policy.
- Keep services in private subnets, use security groups and VPC endpoints, and restrict egress.
- Send structured logs/metrics to CloudWatch; add alarms for errors, latency, denied requests, queue age, and ingestion failures.
- Place AWS WAF at the public edge and use managed throttling. Use CloudTrail and AWS Config for control-plane visibility.

## Suggested request path

```text
Route 53 → WAF → ALB → ECS/Fargate API → RDS PostgreSQL/pgvector
                            │               │
                            ├→ S3 originals │
                            └→ SQS → isolated ingestion workers
```

## Implemented deployment seam

The original-store interface now supports local storage and private S3 without changing API
contracts. S3 writes use tenant-scoped generated keys, AES-256 encryption by default or a
configured KMS key, optional expected-bucket-owner checks, and application-authorized streaming.
Boto3 obtains credentials from the standard AWS provider chain; an ECS deployment should use a
least-privilege task role rather than static credentials.

Example task configuration:

```dotenv
EVIDENCEDESK_DOCUMENT_STORE_BACKEND=s3
EVIDENCEDESK_S3_BUCKET=evidencedesk-private-dev
EVIDENCEDESK_S3_PREFIX=evidencedesk/originals
EVIDENCEDESK_S3_REGION=us-east-1
EVIDENCEDESK_S3_KMS_KEY_ID=alias/evidencedesk-documents
EVIDENCEDESK_S3_EXPECTED_BUCKET_OWNER=123456789012
```

The task role needs only the required object actions on the configured prefix plus KMS permissions
when a customer-managed key is used. The bucket should block all public access, require TLS,
reject unencrypted writes, enable versioning where recovery rules justify it, and record access
events according to the organization’s retention policy.

## Remaining required code and infrastructure

Replace the SQLite repository with PostgreSQL without changing API contracts. Add Alembic-style
managed migrations. Replace the in-memory limiter with edge throttling or Redis. Connect the
existing JWT validator to the chosen identity provider and verify real claims, key rotation,
logout, and failure behavior. Put newly uploaded objects into quarantine and run malware scanning,
extraction, and OCR outside the API process with idempotent jobs, status callbacks, dead-letter
handling, and CPU/memory/time limits. Add distributed tracing and an explicit model-provider
gateway if generative synthesis is enabled.

## Release sequence

1. Provision a private development S3 bucket and validate the implemented adapter with IaC and a
   least-privilege ECS task role; build the PostgreSQL adapter.
2. Import a non-sensitive evaluation corpus and pass retrieval, authorization, and restore tests.
3. Run a time-boxed internal pilot with monitoring and a documented rollback.
4. Complete security/privacy review and operational readiness checks before broader use.

GovCloud requires separate service-availability, region, boundary, identity, compliance, and procurement decisions. Nothing in this repository should be described as GovCloud experience or authorization.
