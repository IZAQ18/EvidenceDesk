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

## Required code changes

Replace the local original-store implementation with S3 and the SQLite repository with PostgreSQL without changing API contracts. Replace the in-memory limiter with edge throttling or Redis. Connect the existing JWT validator to the chosen identity provider and verify real claims, key rotation, logout, and failure behavior. Run extraction/OCR outside the API process with idempotent jobs, status callbacks, dead-letter handling, and CPU/memory/time limits. Add managed migrations, distributed tracing, and an explicit model-provider gateway if generative synthesis is enabled.

## Release sequence

1. Build the S3/PostgreSQL adapters, provision dev infrastructure with IaC, and deploy the API against them.
2. Import a non-sensitive evaluation corpus and pass retrieval, authorization, and restore tests.
3. Run a time-boxed internal pilot with monitoring and a documented rollback.
4. Complete security/privacy review and operational readiness checks before broader use.

GovCloud requires separate service-availability, region, boundary, identity, compliance, and procurement decisions. Nothing in this repository should be described as GovCloud experience or authorization.
