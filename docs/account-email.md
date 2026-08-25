# Verified accounts and password recovery

This runbook describes the implemented email boundary. It does not claim that a sender domain or
email provider has been accepted in production.

## User journey

1. Registration creates a private workspace but leaves the user `pending_verification`.
2. EvidenceDesk emails a one-time verification link. Opening the page does not mutate data; the
   user explicitly presses **Verify**.
3. A successful POST consumes the token, records the verification time, activates the account,
   and creates a secure session.
4. Forgotten-password requests always show the same response, whether or not an account exists.
5. A valid reset changes the scrypt password hash, consumes every outstanding reset token, revokes
   every server-side session, and clears the browser session cookie.

Tokens are generated with Python's cryptographic secrets API. The database stores only SHA-256
hashes plus user, purpose, creation, expiry, and consumption timestamps. Verification and reset
tokens are not interchangeable.

## Production configuration

```dotenv
EVIDENCEDESK_ACCOUNTS_ENABLED=true
EVIDENCEDESK_ACCOUNT_REGISTRATION_ENABLED=true
EVIDENCEDESK_ACCOUNT_EMAIL_VERIFICATION_REQUIRED=true
EVIDENCEDESK_ACCOUNT_PASSWORD_RESET_ENABLED=true
EVIDENCEDESK_ACCOUNT_VERIFICATION_TOKEN_HOURS=24
EVIDENCEDESK_ACCOUNT_PASSWORD_RESET_TOKEN_MINUTES=30

EVIDENCEDESK_EMAIL_DELIVERY_BACKEND=smtp
EVIDENCEDESK_SMTP_HOST=smtp.example.com
EVIDENCEDESK_SMTP_PORT=587
EVIDENCEDESK_SMTP_USERNAME=<server-only username>
EVIDENCEDESK_SMTP_PASSWORD=<server-only secret>
EVIDENCEDESK_SMTP_FROM_ADDRESS=EvidenceDesk <accounts@example.com>
EVIDENCEDESK_SMTP_STARTTLS=true
EVIDENCEDESK_PUBLIC_BASE_URL=https://app.example.com
```

Production startup refuses account email features without SMTP, STARTTLS, a sender address, and an
HTTPS public base URL. Store credentials in the hosting platform's secret manager, not Git or the
browser. EvidenceDesk sends plain-text transactional messages and does not log their recipients,
bodies, links, or tokens.

## Sender-domain acceptance

Before enabling registration:

1. Use a dedicated transactional subdomain such as `accounts.example.com`.
2. Configure and verify SPF, DKIM, and DMARC using the selected provider's current instructions.
3. Send to test mailboxes across major providers and check inbox placement, link correctness,
   expiry, replay rejection, resend rotation, and support contact details.
4. Create alerts for delivery failures and unusual request volume. The current synchronous SMTP
   boundary deliberately returns a generic response even when delivery fails, so operators must
   monitor failures.
5. Put rate limiting at the public edge and define a response process for abuse and account
   takeover reports.
6. Publish privacy, retention, and support information before collecting real customer emails.

## Current boundary

Automated tests use a capturing fake sender; no real mailbox is contacted. SMTP transport itself is
tested for STARTTLS, authentication, and plain-text message construction. Public Render account
features remain explicitly disabled. Managed PostgreSQL, delivery acceptance, invitations,
account deletion/export, and operational monitoring remain launch gates.
