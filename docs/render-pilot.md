# Protected Render pilot

Render is the recommended no-cost test host for EvidenceDesk. Its free web service requires no payment, can build the existing Dockerfile from a private GitHub repository, and provides an `onrender.com` HTTPS address. The repository includes `render.yaml` so the settings remain reviewable without committing the access secret.

## Create the service

1. Sign in to Render and choose **New → Blueprint**.
2. Connect GitHub and grant the Render GitHub app access only to `IZAQ18/EvidenceDesk`.
3. Select the private repository. Render detects `render.yaml` at the repository root.
4. When prompted for `EVIDENCEDESK_PILOT_ACCESS_CODE`, enter a unique random code of at least 24 characters.
5. Confirm that the plan is **Free**, then apply the Blueprint.

The Blueprint builds the Dockerfile, includes Tesseract OCR, waits for GitHub checks before automatic deployment, uses `/health`, and supplies the safe pilot limits. The access code is declared with `sync: false`, so its value is entered in Render and never committed to Git.

## Verify the pilot

1. Wait for the initial Docker deployment to become live.
2. Open the assigned `onrender.com` URL in a private browser window.
3. Confirm the IZAQ access page rejects a wrong code and accepts the correct code.
4. Confirm the two Northstar Labs synthetic documents appear automatically.
5. Ask: “What is the annual leave policy?” Open its citation and verify the source passage.
6. Ask the Code of Conduct alone about annual leave. EvidenceDesk should refuse because that document does not contain the answer.
7. Visit `/docs`; production should not expose the API explorer.

## Free-tier boundary

Free Render web services spin down after 15 minutes without traffic, so the first request after inactivity can take about a minute. Render may restart a free service at any time and free services do not support persistent disks. Uploads, conversations, feedback, and audit events can therefore reset. The synthetic documents are intentionally restored when the database is empty.

Do not upload personal, confidential, regulated, or client material. For an official launch, move the container to the documented AWS path, use individual identity-provider accounts, persistent encrypted storage, a managed data/search layer, centralized logs, backups, monitoring, and a formal security review.
