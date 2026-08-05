# Security Policy

NutriMind AI stores health information — medical conditions, weight history,
dietary restrictions and uploaded documents. This file states what is protected,
how, and what is not.

## Reporting a vulnerability

Email **harshkamat.2307@gmail.com** with `NutriMind security` in the subject.

Please include what you found, how to reproduce it, and what you think the
impact is. A proof of concept helps. Expect an acknowledgement within 72 hours
and an assessment within seven days.

Please do **not** open a public issue for anything exploitable, and please do not
test against an instance you do not own — run it locally, it takes one command.

This is a portfolio project maintained by one person. There is no bug bounty. It
is deployed and holds real data, so reports are taken seriously regardless.

## Supported versions

The latest tagged release only. There are no maintenance branches.

## What is implemented

**Authentication and sessions.** Passwords hashed with scrypt, minimum length 10
with no composition rules (per NIST SP 800-63B). Sessions are signed cookies with
`Secure`, `HttpOnly` and `SameSite=Lax`; the "remember me" cookie carries the
same flags and the same 14-day lifetime. Session identity embeds the password
hash, so changing a password invalidates every existing session, including
remember-me tokens. Sign-in and password reset never reveal whether an address
has an account.

**Data isolation.** Every owned table carries a `user_id` foreign key and every
query goes through one chokepoint. Access control is default-deny per blueprint,
so a new endpoint is protected before anyone remembers to protect it. The vector
store has no `user_id` column, so retrieval filters on document ids resolved from
the database first — a chunk can only be returned to someone who owns its
document. Another account's row reports "does not exist", never 403, so ids
cannot be enumerated.

**CSRF.** Enabled on every state-changing endpoint. The browser client sends the
token as `X-CSRFToken`. Only `/api/health` is exempt, because an uptime monitor
cannot fetch a token and the endpoint is read-only.

**Response headers.** Content-Security-Policy with a per-request nonce (no
`'unsafe-inline'` in `script-src`), HSTS over HTTPS, `X-Content-Type-Options`,
`X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
`Permissions-Policy`, `Cross-Origin-Opener-Policy` and
`Cross-Origin-Resource-Policy`.

**Uploads.** Validated by content, not by filename or `Content-Type`: header,
trailer and a structural parse. Anything that fails is deleted immediately.
Documents that pass validation but cannot be indexed are also deleted, unless the
failure was transient — a temporary backend outage must not destroy the user's
only copy. Uploads are rate limited and bounded by a per-account storage quota.

**Rate limiting.** Per client on authentication, chat and upload endpoints. The
tracking table is bounded and idle clients are evicted; IPv6 callers are grouped
by /64 so address rotation cannot multiply identities.

**Logging.** User messages, search queries and email addresses are replaced with
a salted per-process fingerprint and a length. Passwords, tokens and secrets are
never logged. Log volume is bounded both in the application and at the container
runtime.

**Account lifecycle.** Data can be exported, and an account can be deleted —
which removes relational rows, uploaded files and vectors, then ends the session.

## What is *not* implemented

Stated plainly, because a security policy that lists only strengths is marketing.

**Email verification.** Addresses are unproven. Someone can register with an
address they do not control.

**Password reset delivery.** The link is generated correctly, signed, single-use
and one-hour-expiring — and written to the application log rather than emailed.
An operator can retrieve it. Until a mail transport is configured, self-service
reset does not work.

**Prompt injection is mitigated, not solved.** Retrieved passages are fenced as
untrusted data and the system prompts instruct the model to treat them as
quotations. A determined injection can still influence a model told to trust its
sources. Today the impact is limited: retrieval is scoped to the caller's own
documents, so this is self-injection. It becomes a real cross-user issue if a
shared knowledge base is ever added, and that feature must not ship before this
is revisited.

**Third-party assets.** Bootstrap, `marked` and DOMPurify load from a CDN by
default. `scripts/vendor_cdn_assets.py` copies them locally, after which no
external origin is allowed at all. **Run it before exposing an instance
publicly** — DOMPurify is the sanitiser applied to model output, and by default
it arrives over the network from a third party.

**No error tracking or alerting.** Failures are logged; nobody is notified.

**Single process.** Horizontal scaling is not supported — ChromaDB caches its
index reader per process. See `docs/DEPLOYMENT.md`.

## Reporting scope

In scope: authentication, session handling, data isolation between accounts,
CSRF, XSS, injection of any kind, the upload path, denial of service that a
single client can cause, and anything that discloses one user's data to another.

Out of scope: findings that require an already-compromised host, social
engineering, missing headers on non-sensitive static assets, and the known
limitations listed above — those are documented, not undiscovered.
