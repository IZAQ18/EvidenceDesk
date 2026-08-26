# Secure ingestion operations

EvidenceDesk now keeps asynchronous uploads in a non-public quarantine namespace until configured
malware scanning, structural validation, extraction/OCR, and indexing all succeed. A clean original
is copied to the final tenant namespace only immediately before the database commit; the quarantine
copy is deleted after success. A detected file is never indexed or exposed by document routes.

This is an implemented deployment seam, not evidence that the public Render pilot has scanned real
files. That synthetic pilot has process isolation enabled but no private ClamAV service.

## Recommended production shape

Run the API and ingestion worker as separate containers from the same image:

```text
API task (INGESTION_WORKER_ENABLED=false)
       │
       ├── PostgreSQL job lease
       └── private S3 quarantine prefix
                    │
Standalone worker: python -m app.ingestion_worker
       ├── private clamd INSTREAM scan
       ├── secret-scrubbed parser/OCR child process
       └── final S3 promotion + PostgreSQL index commit
```

Use these settings in the worker task:

```dotenv
EVIDENCEDESK_INGESTION_WORKER_ENABLED=false
EVIDENCEDESK_MALWARE_SCANNER_BACKEND=clamd
EVIDENCEDESK_MALWARE_SCAN_REQUIRED=true
EVIDENCEDESK_CLAMD_HOST=clamd.internal
EVIDENCEDESK_CLAMD_PORT=3310
EVIDENCEDESK_MALWARE_SCAN_TIMEOUT_SECONDS=15
EVIDENCEDESK_INGESTION_PROCESS_ISOLATION_ENABLED=true
EVIDENCEDESK_INGESTION_EXTRACTION_TIMEOUT_SECONDS=120
EVIDENCEDESK_INGESTION_WORKER_MEMORY_MB=1024
EVIDENCEDESK_INGESTION_WORKER_CPU_SECONDS=90
EVIDENCEDESK_INGESTION_MAX_EXTRACTED_CHARS=5000000
```

`INGESTION_WORKER_ENABLED=false` prevents the API container from also consuming jobs. The standalone
worker command consumes them regardless of that flag. Give the parser task no application secrets;
the child process already receives only a small allowlist of operating-system variables, but the
container and network policy remain the outer security boundary.

## Failure handling

- A scanner detection enters `quarantined`, cannot be retried, and stays inaccessible until an
  authorized editor/admin removes the job and staged object.
- A temporary scanner or worker failure enters `failed` while attempts remain.
- After the configured attempt limit, the job enters `dead_letter` and cannot loop indefinitely.
- Admin Insights reports pending, quarantined, and dead-letter counts without filenames or content.
- Integrity, size, page, OCR-page, extracted-character, elapsed-time, CPU, memory, and open-file
  limits fail closed with a safe action for the user.

On Windows the subprocess boundary and wall-clock timeout apply, but POSIX resource limits do not.
Production containers must additionally enforce CPU, memory, read-only filesystem, non-root user,
egress, and temporary-storage limits.

## Acceptance before real documents

1. Keep registration and public routing disabled; use only a synthetic antivirus test vector.
2. Confirm clamd readiness and signature-update monitoring outside EvidenceDesk.
3. Upload the test vector and require `stage: quarantined`, no document row, and no preview route.
4. Confirm retry is refused, Insights increments the quarantine count, and removal deletes the
   exact staged object.
5. Stop clamd, upload a harmless synthetic file, retry through the configured limit, and require
   `stage: dead_letter` with no document row.
6. Upload clean TXT, searchable PDF, scanned PDF, corrupt PDF, oversized PDF, and OCR-heavy samples.
7. Confirm container CPU/memory ceilings, timeouts, logs, alarms, and cleanup behavior.

The repository implements a database-backed dead-letter lane. A managed SQS queue and redrive DLQ
remain part of the AWS application-plane deployment, which has not been provisioned or claimed as
operated production infrastructure.
