# Backup and restore acceptance

This runbook proves that a stopped EvidenceDesk staging environment can be restored without silently
changing its database or original documents. It does not create a backup, snapshot, AWS account, or
production claim. Run it only with synthetic or explicitly approved staging data.

## What the verifier proves

`python -m app.durability` creates a small JSON manifest containing:

- the applied PostgreSQL migration versions;
- row counts and deterministic HMAC-SHA-256 fingerprints for every EvidenceDesk application table;
- the count, total bytes, and aggregate fingerprint of every referenced original after streaming
  each object and comparing its size and SHA-256 digest with the database;
- database and original-storage readiness; and
- S3 versioning, default KMS encryption, and all four public-access-block settings.

The manifest contains no database URL, AWS credential, bucket name, object key, filename, document
text, question, answer, email address, or token. Its fingerprints are still operational security
artifacts: store them in a restricted, immutable location and do not commit them to Git.

Before capture, inject a random value of at least 32 characters as
`EVIDENCEDESK_DURABILITY_MANIFEST_KEY` into the operator shell from an approved secret manager. Use
the same value for verification. The key authenticates table, original, and whole-manifest
fingerprints; it is never written into the report. Do not give this operator-only key to the normal
web or worker task. Losing it makes an old baseline unverifiable.

The capture reads the database twice. It refuses to run while ingestion is queued/processing or if
any table changes during original verification. Stop the web service and worker first; this is not
an online consistent-snapshot protocol.

## Operator permissions

Use a temporary operator identity, not the EvidenceDesk runtime task role. It needs read-only
database access, `s3:GetObject` for the configured originals prefix, and these bucket-control reads:

- `s3:GetBucketVersioning`
- `s3:GetEncryptionConfiguration`
- `s3:GetBucketPublicAccessBlock`

The command uses the normal AWS credential provider chain. Never put AWS access keys in `.env` or
the repository.

## 1. Capture the pre-backup baseline

Configure the production PostgreSQL and S3 variables plus the operator manifest key documented in
[aws-deployment.md](aws-deployment.md), stop application traffic and the ingestion worker, then run:

```bash
python -m app.durability snapshot \
  --output outputs/durability/pre-backup.json
```

A successful capture prints only the report path. Review the JSON for `durable_target: true`, the
expected migration versions, table counts, verified-original count, and positive control checks.

The default limits are one million rows, 10,000 originals, and 10 GB read. Raise a limit explicitly
only after estimating run time and data-transfer cost:

```bash
python -m app.durability snapshot \
  --max-rows 2000000 \
  --max-originals 20000 \
  --max-bytes 20000000000 \
  --output outputs/durability/pre-backup.json
```

`--allow-local` exists only to rehearse the command with SQLite/local storage. A report created that
way says `durable_target: false` and is not managed-stage acceptance evidence.

## 2. Exercise both restore mechanisms

1. Record the baseline manifest digest and chosen recovery point in the operator change record.
2. Create a manual RDS snapshot and wait until AWS reports it available.
3. Restore that snapshot into a separate private RDS instance. Never overwrite the source database.
4. On a disposable synthetic S3 original, create a new version or delete marker, then recover the
   previous version using the S3 version-ID workflow. Do not experiment on customer data.
5. Point a stopped acceptance task or operator shell at the restored RDS endpoint and the recovered
   private S3 originals. Keep public routing and registration disabled.

The RDS console/CLI and S3 version recovery are AWS operator actions and can incur charges. Inspect
the exact resources and price impact before creating them.

## 3. Verify the restored target

With the restored PostgreSQL/S3 configuration active, run:

```bash
python -m app.durability verify \
  --baseline outputs/durability/pre-backup.json \
  --output outputs/durability/restore-result.json
```

Success requires `verified: true` and an empty `mismatches` list. A difference is reported only as
one of: deployment boundary, database fingerprint, original-storage fingerprint, or durability
controls. The tool deliberately does not print the affected row, filename, object key, or content.

After a match, start one isolated application task and verify `/health`, `/ready`, sign-in, one known
cited answer, its original preview, and a synthetic upload/delete. Keep the restored environment
isolated until the operator approves either promotion or removal.

## Failure and rollback

- Never route users to a mismatched restore.
- Keep the original environment stopped and unchanged until acceptance finishes.
- If verification fails, preserve both reports and provider event identifiers, investigate under
  restricted access, and repeat from a fresh restore.
- Do not merge two databases that accepted independent writes.
- Removing test restore resources is a separate destructive AWS action. Confirm exact identifiers,
  snapshot retention, S3 versions, and cost consequences before deletion.

Passing this runbook proves one bounded restore exercise at one point in time. It does not prove
future backups, failover, disaster recovery objectives, or large-scale production operation.
