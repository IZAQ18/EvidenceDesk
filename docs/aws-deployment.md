# Path to standard AWS

This is an executable deployment seam and runbook, not a claim that EvidenceDesk has operated at
production scale. It targets standard commercial AWS. It is not a GovCloud design, authorization,
or experience claim.

## What is implemented

- PostgreSQL schema migrations run once under a transaction advisory lock.
- Each process uses a bounded Psycopg connection pool and fails startup when the database cannot
  be reached.
- Original documents can use a private S3 bucket with KMS encryption and expected-owner checks.
- `GET /health` checks the process and database; `GET /ready` checks both the database and original
  store, returning `503` when the instance should not receive traffic.
- `python -m app.migrate_data` performs a read-only preflight by default and can copy an idle local
  SQLite/original-file data set into an empty PostgreSQL/S3 target.
- [`infra/aws/evidencedesk-data.json`](../infra/aws/evidencedesk-data.json) defines the durable data
  plane with CloudFormation: a private encrypted/versioned S3 bucket, a private encrypted RDS for
  PostgreSQL instance, separate bootstrap/runtime credentials in Secrets Manager, backups,
  retain/snapshot deletion behavior, and an ECS task role restricted to the EvidenceDesk object
  prefix, application secret, and data key.
- `python -m app.durability` captures a secret-free, content-free database/original fingerprint and
  compares it with a separately restored target. The detailed procedure is in
  [durability-acceptance.md](durability-acceptance.md).

The template has not been applied by this repository. Creating it can incur AWS charges. RDS and
S3 eligibility, credits, and prices depend on account age, region, configuration, and usage; review
the current official [RDS](https://aws.amazon.com/rds/features/) and
[S3](https://aws.amazon.com/s3/faqs/) information before creating a stack.

## Small deployment shape

```text
Route 53 → WAF → ALB → ECS/Fargate API → private RDS PostgreSQL
                            │                    │
                            ├→ private S3        └→ automated backups/snapshots
                            └→ SQS → isolated ingestion workers (next milestone)
```

The supplied template covers the right-hand data plane, not the VPC, load balancer, ECS service,
queue, DNS, or WAF. Keeping those separate prevents a reviewer from mistaking an infrastructure
example for an already-operated production platform.

## Before creating the data stack

1. Select one AWS account and region and set a small budget plus billing alerts.
2. Create or select a VPC with at least two private subnets in different Availability Zones.
3. Create the application ECS security group. The template allows port 5432 only from this group.
4. Review the RDS instance class, storage, backup retention, deletion protection, and Multi-AZ
   choice. Multi-AZ is disabled by default because it materially changes cost.
5. Validate the CloudFormation JSON in the AWS console or CLI, inspect the change set, and create it
   only after the estimated charges are acceptable.

Do not put AWS access keys in `.env`. In ECS, attach the output `ApplicationTaskRoleArn` as the task
role and use an execution role for image pulls/log delivery. The task role can read the separate
application database secret; it cannot read the RDS master secret.

## Application configuration

The RDS-managed secret is for one-time database administration only. During bootstrap, retrieve it
through a tightly controlled operator role, retrieve the generated `ApplicationDatabaseSecretArn`,
and create the matching `evidencedesk_app` PostgreSQL login. Make that role the owner of the empty
EvidenceDesk database so startup migrations can create and update the application schema. Do not
give it RDS administration roles, and do not attach the master secret to ECS.

After bootstrap, inject `username` and `password` JSON fields from
`ApplicationDatabaseSecretArn` into the component variables through the ECS task definition; use
stack outputs for the other values:

```dotenv
EVIDENCEDESK_ENV=production
EVIDENCEDESK_DATABASE_BACKEND=postgres
EVIDENCEDESK_DATABASE_HOST=<DatabaseEndpoint output>
EVIDENCEDESK_DATABASE_PORT=5432
EVIDENCEDESK_DATABASE_NAME=<DatabaseName output>
EVIDENCEDESK_DATABASE_USER=<username from ApplicationDatabaseSecretArn>
EVIDENCEDESK_DATABASE_PASSWORD=<password from ApplicationDatabaseSecretArn>
EVIDENCEDESK_DATABASE_SSLMODE=verify-full
EVIDENCEDESK_DATABASE_POOL_MIN_SIZE=1
EVIDENCEDESK_DATABASE_POOL_MAX_SIZE=5
EVIDENCEDESK_DATABASE_POOL_TIMEOUT_SECONDS=10

EVIDENCEDESK_DOCUMENT_STORE_BACKEND=s3
EVIDENCEDESK_S3_BUCKET=<OriginalsBucketName output>
EVIDENCEDESK_S3_PREFIX=evidencedesk/originals
EVIDENCEDESK_S3_REGION=<stack region>
EVIDENCEDESK_S3_KMS_KEY_ID=<DataKeyArn output>
EVIDENCEDESK_S3_EXPECTED_BUCKET_OWNER=<12-digit AWS account id>
# Set only in the temporary operator shell used for restore acceptance.
EVIDENCEDESK_DURABILITY_MANIFEST_KEY=<random operator-only value, at least 32 characters>
```

Use the current Amazon RDS CA bundle in the application image when `verify-full` is selected. The
component form avoids assembling a URL in an ECS secret; EvidenceDesk percent-encodes credentials
internally. One small process can start with the default five-connection maximum. Multiply that
maximum by the desired task count and keep the result safely below the database connection limit.

## Safe local-to-managed migration

First stop uploads and wait until every ingestion job is `ready` or `failed`. Back up the source
database and originals directory. The default command is read-only and verifies row counts, file
sizes, and SHA-256 digests:

```bash
python -m app.migrate_data \
  --source-db data/evidencedesk.db \
  --source-originals data/originals
```

Configure the target environment shown above, then perform the copy with an explicit, unique ID:

```bash
python -m app.migrate_data \
  --source-db data/evidencedesk.db \
  --source-originals data/originals \
  --migration-id pilot_2026_08_25 \
  --execute
```

Execution refuses a non-PostgreSQL/non-S3 target, an active ingestion queue, or a non-empty target
database. It copies originals to a unique `migration-<id>` prefix, verifies every digest and S3
round trip, copies the database in one transaction, verifies table row counts, and leaves the
source untouched. If object copy succeeds but the database transaction fails, objects may remain
under that unique prefix; inspect and remove only that exact prefix after resolving the failure.

## Acceptance and rollback

Before directing users to the managed service:

1. Start one application task and require both `/health` and `/ready` to return `200`.
2. Sign in with a test account, open an existing original, ask a known question, follow its
   citation, upload a synthetic PDF, and permanently delete that synthetic document.
3. Compare migration row counts and run the full evaluation suite.
4. Stop traffic and capture `outputs/durability/pre-backup.json` with
   `python -m app.durability snapshot`.
5. Take a manual RDS snapshot, restore it to a separate private instance, recover a disposable
   synthetic S3 object version, and run `python -m app.durability verify` against the baseline.
   Automated backups and bucket versioning are configuration, not proof of restore.
6. Confirm the ECS task role cannot retrieve `DatabaseBootstrapSecretArn` and cannot access S3
   objects outside `evidencedesk/originals/`.
7. Keep the original source offline and unchanged through the acceptance window.

Rollback means routing traffic back to the unchanged source deployment before accepting new writes
on both sides. Do not try to merge two independently written databases. After final acceptance,
retain backups according to a written retention/deletion policy rather than deleting them ad hoc.

## Still required before public accounts

- Deploy ALB/ECS in private subnets and terminate TLS with ACM.
- Move ingestion into SQS-backed, separately constrained workers with malware scanning and a
  dead-letter queue.
- Add central logs, metrics, alarms, distributed throttling, incident procedures, and dependency
  scanning.
- Accept a real sender domain and SMTP provider; exercise the implemented invitation,
  account-deletion/export, cleanup, and retention paths against managed services; complete privacy
  terms and customer-specific quality/security acceptance.
- Exercise secret rotation, backup restore, failover, and rollback in the selected AWS account.

These are launch gates. A passing local/CI suite and an unapplied CloudFormation template do not
turn EvidenceDesk into a large-scale production service.
