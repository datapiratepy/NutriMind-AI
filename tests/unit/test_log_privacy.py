"""Nothing a user tells NutriMind may reach the logs.

This is a health application. A chat message routinely carries a diagnosis, a
medication, a weight, a pregnancy, or a disordered-eating disclosure. Measured
before this was fixed, during entirely ordinary use at ``LOG_LEVEL=INFO``:

    INFO nutrimind.agents.coordinator  routing[rules]
         'I have type 2 diabetes, what should I eat?' -> health_advisor
    INFO nutrimind.retrieval.retriever retrieve
         'I have type 2 diabetes, what should I eat?': 0 hits
    INFO nutrimind.services.auth_service failed login for 'patient@example.test'

Twice per message, to the log file and to stdout, which Docker retains.

The existing logging tests assert that logging *survives* ``apply_migrations``
— presence, not content — so nothing in the suite could have caught this.
These assert content, which is the property that actually matters.
"""

from __future__ import annotations

import logging
import re

import pytest

from nutrimind.utils.redaction import fingerprint, redact_email, summarize

#: Strings that must never appear in a log record, and what each stands for.
FORBIDDEN = {
    "type 2 diabetes": "a medical condition",
    "sertraline": "a medication",
    "i weigh 92kg": "body weight",
    "patient@example.test": "an email address",
    "correct-horse-battery": "a password",
}


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.text = ""

    def emit(self, record):
        self.text += self.format(record) + "\n"


@pytest.fixture()
def captured():
    handler = _Capture()
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)
    root.setLevel(previous)


# -- the redaction helpers themselves ------------------------------------------

def test_summarize_keeps_length_and_a_fingerprint_but_no_content():
    message = "I have type 2 diabetes, what should I eat?"
    out = summarize(message)
    assert "diabetes" not in out
    assert f"len={len(message)}" in out, "length is a useful diagnostic; keep it"
    assert re.search(r"fp=[0-9a-f]{8}", out)


def test_the_same_text_fingerprints_the_same_within_a_process():
    """Correlating a retry with its original is the point of keeping a tag."""
    assert summarize("pad thai") == summarize("pad thai")
    assert summarize("pad thai") != summarize("pad tha1")


def test_redacted_email_keeps_the_domain_and_drops_the_person():
    out = redact_email("patient@example.test")
    assert "patient" not in out
    assert out.endswith("@example.test"), (
        "the domain is operationally useful — a burst against one domain looks "
        "different from a spray across many")


def test_fingerprints_are_salted_per_process():
    """A bare hash of a short message is reversible with a small dictionary.

    Re-salting on each start means a captured log cannot be cracked offline. The
    cost is that fingerprints do not correlate across restarts, which is the
    right trade for debugging a live incident.
    """
    import importlib

    import nutrimind.utils.redaction as module

    before = fingerprint("pad thai")
    importlib.reload(module)
    assert module.fingerprint("pad thai") != before


def test_empty_and_missing_values_are_handled():
    assert summarize("") == "<empty>"
    assert summarize(None) == "<empty>"
    assert redact_email(None) == "<none>"
    assert redact_email("no-at-sign") .startswith("<fp=")


# -- end to end through the real request path ---------------------------------

def test_no_user_content_reaches_the_logs_during_ordinary_use(client, captured):
    """Drives real endpoints and then greps everything that was logged."""
    client.put("/api/profile", json={
        "name": "Pat", "age": 41, "gender": "female", "height_cm": 165,
        "weight_kg": 92, "activity_level": "low", "food_preference": "vegetarian",
        "weight_goal": "lose", "medical_conditions": ["type 2 diabetes"],
        "allergies": ["peanuts"], "country": "India"})
    client.post("/api/chat", json={
        "message": "I have type 2 diabetes and take sertraline, I weigh 92kg",
        "stream": False})
    client.get("/api/documents/search", query_string={"q": "type 2 diabetes"})
    client.post("/login", data={"email": "patient@example.test",
                                "password": "correct-horse-battery"})

    lowered = captured.text.lower()
    leaked = {value: why for value, why in FORBIDDEN.items() if value in lowered}
    assert not leaked, f"user content reached the logs: {leaked}"


def test_routing_and_retrieval_still_log_something_useful(client, captured):
    """Deleting the lines would also have passed the test above.

    Losing them means the next 'why did it route there' question has no
    evidence, so the diagnostic value has to survive the redaction.
    """
    client.post("/api/chat", json={"message": "how much protein is in paneer?",
                                   "stream": False})
    assert "routing[" in captured.text, "routing decisions are no longer logged"
    assert re.search(r"fp=[0-9a-f]{8}", captured.text), (
        "the correlation fingerprint is gone — repeated queries can no longer "
        "be told apart from distinct ones")
