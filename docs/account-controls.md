# Workspace membership, ownership, exports, and deletion

These controls are implemented but opt-in. The protected Render portfolio pilot keeps them off
because it uses a shared access code and ephemeral storage, not individual customer accounts.

## What a user can do

- An administrator can invite a person by email and choose `reader`, `editor`, or `admin`, plus
  the classifications that person may retrieve.
- A person with an existing account signs in before accepting. A new person can create an account
  directly from the one-time invitation. Acceptance opens the invited workspace.
- A member who belongs to multiple workspaces can switch the active workspace. Switching rotates
  the current session and changes the tenant boundary used by every later request.
- An administrator can review active members and change another member's role or classification
  clearance. The affected member's sessions for that workspace are revoked immediately.
- An administrator can remove another non-owner member after re-entering the current password.
  Removal revokes workspace sessions but does not delete that person's account or other workspaces.
- The current owner can transfer ownership to an existing member after re-entering the password
  and explicitly confirming `TRANSFER`. The new owner becomes an administrator with full evidence
  clearance. The previous owner remains an administrator until their access is changed or removed.
- A non-owner who has another workspace can leave after entering the password and `LEAVE`. Their
  browser session rotates into the remaining workspace. An owner must transfer first, and a user
  with no alternative workspace uses account deletion instead.
- An administrator can download a ZIP for the current workspace. It contains `manifest.json` and
  available original files. Password hashes, session/token hashes, invitation token hashes, and
  internal storage paths are excluded.
- A user can delete their account only after entering the current password and the word `DELETE`.
  Sole-owned workspaces and their stored originals are removed. Deletion refuses to proceed when
  an owned workspace still has other members, preventing accidental destruction of shared data.

## Security behavior

Invitation links are random, expire, rotate when the same email is reinvited, and are accepted
once with a conditional database update. Only a SHA-256 token hash is stored. An authenticated
recipient must have the invited email and provide the session CSRF token. The email recipient,
link, and token are never written to application logs.

Member mutations are administrator-only and CSRF-protected. Role changes revoke the affected
workspace sessions. Removal and ownership transfer additionally require the administrator's
current password and an explicit action word. The owner cannot be removed or demoted, ownership
can move only to an existing member, and leaving cannot produce an account with no workspace.

Workspace export is administrator-only, is bounded by
`EVIDENCEDESK_ACCOUNT_EXPORT_MAX_BYTES`, and streams each original into the archive instead of
loading every original at once. The final archive is still assembled in process for this MVP;
larger deployments should create asynchronous encrypted exports with short-lived authenticated
downloads and explicit expiry.

Account deletion commits database removal before best-effort object cleanup. This ordering avoids
leaving live database references to undeleted data if object storage fails. Any cleanup failure is
logged without the object name and needs an operator retry/retention process. Backups follow the
deployment's written retention policy and are not erased by this request.

## Configuration

```dotenv
EVIDENCEDESK_ACCOUNTS_ENABLED=true
EVIDENCEDESK_ACCOUNT_INVITATIONS_ENABLED=true
EVIDENCEDESK_ACCOUNT_INVITATION_HOURS=72
EVIDENCEDESK_ACCOUNT_EXPORT_ENABLED=true
EVIDENCEDESK_ACCOUNT_EXPORT_MAX_BYTES=250000000
EVIDENCEDESK_ACCOUNT_DELETION_ENABLED=true
```

Invitations are an email feature. In production they also require the SMTP, STARTTLS, sender, and
HTTPS public-base settings in [account-email.md](account-email.md). Production startup fails closed
when that boundary is incomplete.

## Acceptance checklist

1. Invite a new email with reader/internal access and verify the email contains the right HTTPS
   host but application logs do not contain the address or link.
2. Confirm the invitation cannot be accepted twice and a different signed-in email is rejected.
3. Switch between two workspaces and verify documents never cross the selected tenant boundary.
4. Inspect an export: originals should be present where available and credentials/token hashes/
   storage paths must be absent.
5. Change a synthetic member's role/clearance and confirm their old workspace session is revoked.
6. Confirm removal rejects a wrong password, removes only the selected membership, and preserves
   that person's account and personal workspace.
7. Transfer ownership, confirm the new owner is a full-clearance administrator, then delete the
   previous owner's account and verify the shared workspace and documents remain.
8. Leave a non-owned workspace and verify the browser rotates to another membership; confirm an
   owner and a user with only one workspace are both refused with actionable messages.
9. Confirm deletion rejects a wrong password and refuses an owned workspace with another member.
10. Delete a synthetic sole-owner account and verify database records, local/S3 originals, sessions,
   and the browser cookie are gone; then exercise the backup-retention and cleanup runbooks.

Automated SQLite tests cover these flows, and the PostgreSQL CI suite covers the invitation and
membership path. Real SMTP delivery, managed object cleanup, backup expiry, privacy/legal review,
abuse monitoring, managed identity integration, and operational recovery drills remain deployment
acceptance work.
