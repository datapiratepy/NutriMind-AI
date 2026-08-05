"""Keep user content out of the logs without losing the diagnostics.

NutriMind is a health application. A chat message routinely contains a
diagnosis, a medication, a weight, a pregnancy, or an eating disorder. Before
this module those messages were written verbatim to the log file **and** to
stdout, twice per request:

    INFO nutrimind.agents.coordinator  routing[rules]
         'I have type 2 diabetes, what should I eat?' -> health_advisor
    INFO nutrimind.retrieval.retriever retrieve
         'I have type 2 diabetes, what should I eat?': 0 hits
    INFO nutrimind.services.auth_service failed login for 'patient@example.test'

Deleting the log lines outright would be the easy fix and the wrong one: they
are how routing and retrieval are debugged, and losing them means the next
"why did it pick that agent" question has no evidence at all.

So the content is replaced with a **fingerprint plus a length**. The
fingerprint is stable within a process run, which is what makes the useful
questions still answerable — "is this the same query retried?", "did routing and
retrieval see the same text?", "is one account being brute-forced?" — while the
text itself never reaches disk.

The fingerprint is salted per process, deliberately. A bare hash of a short
message is trivially reversible with a dictionary of likely questions; re-salting
on every start means a captured log cannot be cracked offline, at the cost of not
being able to correlate across restarts. For debugging a live incident that trade
is correct.
"""

from __future__ import annotations

import hashlib
import secrets

#: Regenerated on import, i.e. once per process. See the module docstring.
_SALT = secrets.token_bytes(16)


def fingerprint(value: str) -> str:
    """Short, stable-within-this-process, non-reversible tag for a string."""
    digest = hashlib.blake2b(_SALT + value.encode("utf-8"), digest_size=4)
    return digest.hexdigest()


def summarize(value: str | None) -> str:
    """A log-safe stand-in for arbitrary user text.

    ``"I have type 2 diabetes, what should I eat?"`` becomes
    ``<len=42 fp=1a2b3c4d>`` — enough to tell two queries apart and to see that
    a retry is the same text, with nothing recoverable from it.
    """
    if not value:
        return "<empty>"
    return f"<len={len(value)} fp={fingerprint(value)}>"


def redact_email(email: str | None) -> str:
    """A log-safe stand-in for an email address.

    The domain is kept because it is operationally useful — a burst of failures
    against one domain looks different from a spray across many — and the local
    part, which identifies the person, is replaced by a fingerprint.

    ``patient@example.test`` becomes ``<fp=9f2c1d0a>@example.test``.
    """
    if not email or "@" not in email:
        return "<none>" if not email else f"<fp={fingerprint(email)}>"
    _, _, domain = email.partition("@")
    return f"<fp={fingerprint(email)}>@{domain}"
