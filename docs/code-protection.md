# Protecting the private pilot

Keeping the GitHub repository private prevents anonymous source access. Grant the hosting provider access to this repository only, invite no unnecessary collaborators, require multi-factor authentication on GitHub and Render, and review installed GitHub Apps periodically.

Secrets belong in hosting environment variables. `.env`, local databases, uploaded files, generated outputs, and work folders are excluded by `.gitignore`. The pilot access code is compared without ordinary string equality, exchanged for an HttpOnly and SameSite session cookie, and can be invalidated immediately by rotating the code and redeploying.

The production pilot also disables interactive API documentation, rejects unexpected host names, adds restrictive browser headers, blocks framing, tells search engines not to index it, and gates API routes before application authentication runs. These controls reduce accidental exposure; `robots.txt` and `noindex` are not authentication.

No web application can hide all code sent to a visitor's browser. EvidenceDesk keeps the Python backend and security logic on the server, but its HTML, CSS, and JavaScript interface remains inspectable by an authorized visitor. Obfuscation would add inconvenience without meaningful protection, so the project relies on access control, least privilege, secret management, and a private repository instead.

The pilot must contain only fictional or non-sensitive documents. Before an official launch, replace the shared access code with individual identity-provider accounts and least-privilege roles, add durable encrypted storage and backups, put rate limiting at the edge, centralize audit logs, run dependency and container scanning, and complete a security review.
