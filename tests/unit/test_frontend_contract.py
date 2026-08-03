"""Static checks on the browser client that Python request tests cannot make.

The test client in ``conftest.py`` attaches a CSRF token to every unsafe request
automatically. That is right for testing the *server*, but it means the suite is
structurally blind to a frontend that forgets to send one: the endpoint is
exercised with a valid token no matter what the JavaScript does.

Document upload broke exactly there. ``knowledge.js`` called ``fetch()``
directly, so the browser sent no token, the server answered with an HTML 400, and
the page reported "JSON.parse: unexpected character at line 1 column 1" — while
every server-side test kept passing.

These read the shipped JavaScript as text. Crude, but they close a real gap, and
they cost nothing to run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parents[2] / "nutrimind" / "static" / "js"

#: The single module allowed to call fetch(); it is the wrapper everything else
#: goes through.
WRAPPER = "app.js"

#: Matches `fetch(` only when it is not preceded by a dot or word character, so
#: `NM.fetch(` and `nmFetch(` are not flagged.
BARE_FETCH = re.compile(r"(?<![.\w])fetch\s*\(")


def _js_files() -> list[Path]:
    files = sorted(JS_DIR.glob("*.js"))
    assert files, f"no JavaScript found under {JS_DIR}"
    return files


def _strip_comments(source: str) -> str:
    """Remove /* */ and // comments so prose about fetch() is not flagged."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


@pytest.mark.parametrize("path", _js_files(), ids=lambda p: p.name)
def test_no_page_script_calls_fetch_directly(path: Path):
    """Only app.js may call fetch(); everything else goes through NM.fetch.

    A bare fetch() silently omits the CSRF header, and the failure surfaces as an
    unparseable response rather than as "missing token" — so it is expensive to
    diagnose and easy to reintroduce.
    """
    if path.name == WRAPPER:
        return

    offenders = [
        line_number
        for line_number, line in enumerate(
            _strip_comments(path.read_text(encoding="utf-8")).splitlines(), start=1)
        if BARE_FETCH.search(line)
    ]
    assert not offenders, (
        f"{path.name} calls fetch() directly at line(s) {offenders}. "
        "Use NM.fetch(), which attaches the CSRF token and handles a signed-out "
        "session; NM.api() on top of it when you want parsed JSON.")


def test_the_wrapper_attaches_a_csrf_token():
    """app.js is exempt from the rule above, so its own behaviour is pinned here."""
    source = (JS_DIR / WRAPPER).read_text(encoding="utf-8")
    assert "X-CSRFToken" in source, "the fetch wrapper no longer sends a CSRF token"
    assert 'csrf-token' in source, "the wrapper no longer reads the CSRF meta tag"


def test_the_wrapper_tolerates_a_non_json_error_body():
    """Error bodies are not always JSON — a proxy timeout or a framework error
    page is HTML — so parsing must be guarded rather than assumed."""
    source = _strip_comments((JS_DIR / WRAPPER).read_text(encoding="utf-8"))
    guarded = re.search(r"try\s*\{[^}]*response\.json\(\)[^}]*\}\s*catch", source)
    assert guarded, "response.json() is called without a catch in app.js"


def test_upload_uses_the_wrapper():
    """Direct regression for the reported bug."""
    source = (JS_DIR / "knowledge.js").read_text(encoding="utf-8")
    assert "NM.fetch(\"/api/documents\"" in source, (
        "the document upload no longer goes through NM.fetch — it will be "
        "rejected for a missing CSRF token")
