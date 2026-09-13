# Reviewer walkthrough

13 September 2026

The public landing page previously led directly to an authenticated workspace. A new `/demo` route gives reviewers a quick, readable introduction before signing in.

The route contains three fixed illustrations from the bundled fictional Northstar Labs handbook. It shows an answer beside its source, preserves a remote-work condition, and demonstrates insufficient evidence for a salary question. The unsupported example labels its source pane as a coverage summary, not a quotation. Every example is labeled as prepared content rather than a live search or model response.

The route has no database, provider or user-input dependency. Its public allowlist entry does not unlock protected routes or issue account/pilot cookies. Private account access, document permissions and authentication remain enforced.

Validation:

- Source quotations are matched exactly against the bundled synthetic handbook.
- Tests confirm the public page loads under the pilot gate while private document access remains blocked, no cookies are issued, and configured links are escaped.
- Desktop and 360-pixel browser checks exercised all three examples. The narrow layout had equal document/client width (345 CSS pixels excluding the scrollbar), with no horizontal overflow.
- The local complete test suite passed, with optional browser/PostgreSQL cases skipped; those integrations remain CI responsibilities. This is not evidence of production scale, real user demand or model accuracy.

This presentation change builds on an existing portfolio project. It must not be described as a new application built entirely during a hackathon. Event-specific eligibility and prior-work rules require organizer verification.
