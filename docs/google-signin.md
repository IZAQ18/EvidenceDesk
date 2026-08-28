# Google sign-in runbook

EvidenceDesk implements Google sign-in as a server-side OpenID Connect authorization-code flow.
It requests only `openid`, `email`, and `profile`. It does not ask for Gmail, Drive, Calendar, or
offline access and does not retain Google's access token or refresh token.

## What is already implemented

- A **Continue with Google** button on the EvidenceDesk sign-in page.
- A signed, ten-minute, HttpOnly OAuth-attempt cookie.
- CSRF `state`, OpenID `nonce`, and PKCE challenge validation.
- RS256 ID-token verification against Google's JWKS, issuer, audience, expiry, authorized party,
  verified-email, and stable-subject claims.
- Local user/workspace creation or verified-email account linking.
- Normal EvidenceDesk Secure/HttpOnly/SameSite session cookies after sign-in.
- No Google password, access token, refresh token, Gmail data, or Drive data stored.

## Google Cloud configuration

1. Open **Google Cloud Console → Google Auth Platform** and create or select a project.
2. Configure the branding/consent screen with the real EvidenceDesk name, support email, homepage,
   privacy URL, and terms URL.
3. Create an OAuth client with application type **Web application**.
4. Add this exact production redirect URI:

   ```text
   https://evidencedesk-e2zk.onrender.com/auth/google/callback
   ```

5. For local testing, optionally add:

   ```text
   http://localhost:8000/auth/google/callback
   ```

6. Copy the generated Client ID and Client Secret. Do not download them into or commit them to this
   repository.

EvidenceDesk uses a server redirect, so an Authorized JavaScript Origin is not required for the
current implementation. Google's redirect URI must match exactly, including scheme, host, path,
and trailing-slash choice.

## Render environment values

Do not enable account mode while the shared pilot code remains configured; EvidenceDesk rejects
that ambiguous combination at startup. When ready for an account-mode acceptance test, configure:

```dotenv
EVIDENCEDESK_PUBLIC_BASE_URL=https://evidencedesk-e2zk.onrender.com
EVIDENCEDESK_ACCOUNTS_ENABLED=true
EVIDENCEDESK_ACCOUNT_REGISTRATION_ENABLED=false
EVIDENCEDESK_GOOGLE_OAUTH_ENABLED=true
EVIDENCEDESK_GOOGLE_OAUTH_REGISTRATION_ENABLED=true
EVIDENCEDESK_GOOGLE_OAUTH_CLIENT_ID=<paste the Google Web client ID>
EVIDENCEDESK_GOOGLE_OAUTH_CLIENT_SECRET=<paste the Google client secret>
```

Remove `EVIDENCEDESK_PILOT_ACCESS_CODE` from the active Render environment before restarting. Keep
the Google client secret in Render only; never put it in `.env.example`, GitHub, screenshots, chat,
or browser-visible JavaScript.

`EVIDENCEDESK_ACCOUNT_REGISTRATION_ENABLED=false` keeps unverified password registration closed.
If Google self-service registration should also remain closed, set
`EVIDENCEDESK_GOOGLE_OAUTH_REGISTRATION_ENABLED=false`; Google can then sign in only an existing
EvidenceDesk account whose email matches Google's verified email.

## Acceptance test

1. Open `/signin` in a private browser window and confirm **Continue with Google** appears.
2. Sign in with a dedicated test Google account and approve only basic identity access.
3. Confirm EvidenceDesk creates one Free workspace and opens the app without an API-key field.
4. Sign out, sign in with Google again, and confirm the same workspace returns.
5. Attempt a cancelled sign-in and confirm EvidenceDesk returns a safe message without exposing
   provider details.
6. Confirm another Google account cannot see the first account's documents or conversations.
7. Confirm the Render logs contain no authorization code, ID token, email, or client secret.

## Deployment boundary

The current free Render pilot uses ephemeral SQLite and local original-file storage. Accounts and
uploads can disappear after a restart. Google sign-in can be demonstrated there with synthetic
documents, but do not call it a durable customer account service. Move to managed PostgreSQL and
private object storage, complete backup/restore acceptance, and enable operational monitoring
before a public account launch.

Primary references: [Google web-server OAuth guide](https://developers.google.com/identity/protocols/oauth2/web-server)
and [Google OpenID Connect guide](https://developers.google.com/identity/openid-connect/openid-connect).
