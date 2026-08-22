# Protected Koyeb pilot

Koyeb is the recommended no-cost test host for EvidenceDesk because it can build the existing Dockerfile from a private GitHub repository. The Docker image already includes Tesseract OCR. This is a portfolio pilot, not the final AWS production design.

## Create the service

1. In Koyeb, create a Web Service from GitHub.
2. Grant the Koyeb GitHub app access only to the private `IZAQ18/EvidenceDesk` repository.
3. Select the `main` branch and Dockerfile build method.
4. Choose the free instance in Frankfurt or Washington, D.C.
5. Expose HTTP port `8000` and set the health check path to `/health`.
6. Keep automatic deployment enabled so a tested push to `main` refreshes the pilot.

## Add secrets and variables

Generate a unique access code of at least 24 random characters. Store it only in Koyeb's encrypted Secret settings—never in Git, a screenshot, or the LinkedIn post.

```dotenv
EVIDENCEDESK_ENV=production
EVIDENCEDESK_AUTH_MODE=api_key
EVIDENCEDESK_PILOT_ACCESS_CODE=<long-random-secret>
EVIDENCEDESK_PILOT_SESSION_HOURS=12
EVIDENCEDESK_SEED_DEMO_CONTENT=true
EVIDENCEDESK_DB_PATH=/tmp/evidencedesk/evidencedesk.db
EVIDENCEDESK_DOCUMENT_STORE_PATH=/tmp/evidencedesk/originals
EVIDENCEDESK_RATE_LIMIT_PER_MINUTE=30
EVIDENCEDESK_MAX_UPLOAD_BYTES=5000000
EVIDENCEDESK_MAX_PDF_PAGES=40
EVIDENCEDESK_OCR_MAX_PAGES=10
EVIDENCEDESK_EXPOSE_API_DOCS=false
EVIDENCEDESK_ALLOWED_HOSTS=localhost,127.0.0.1,*.koyeb.app
```

After Koyeb assigns the URL, add it as `EVIDENCEDESK_PUBLIC_BASE_URL` so social-card metadata uses the canonical address.

## Verify the pilot

1. Open the Koyeb URL in a private browser window. It should show the IZAQ access page.
2. Check that an incorrect code is rejected and the correct code opens the workspace.
3. Confirm the two Northstar Labs synthetic documents appear automatically.
4. Ask: “What is the annual leave policy?” Open its citation and verify the passage.
5. Ask the Code of Conduct alone about annual leave. EvidenceDesk should refuse because that document does not contain the answer.
6. Visit `/docs`; production should not expose the API explorer.

## Free-tier boundary

The free instance scales to zero after inactivity and uses ephemeral local storage. A restart or deployment can remove uploads, conversations, feedback, and audit events. The synthetic documents are intentionally reseeded when the database is empty. Do not upload personal, confidential, regulated, or client material to this pilot.

For an official launch, move the container to the documented AWS path, use managed identity, persistent encrypted storage, a managed search/data layer, centralized logs, backups, monitoring, and a formal security review.
