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


# -- the asynchronous upload contract -----------------------------------------

def test_upload_does_not_claim_the_document_is_indexed():
    """Upload answers 202 'queued', so the toast must not report an outcome.

    The old message read ``Indexed "<name>" — N chunks`` from the response body.
    Against an asynchronous API those fields are ``pending`` and ``0``, so the
    user would be told their document was indexed with zero chunks while the
    table beneath it said "pending".
    """
    source = _strip_comments((JS_DIR / "knowledge.js").read_text(encoding="utf-8"))
    upload_body = source.split("async function upload")[1].split("drop.addEventListener")[0]
    assert "Indexed" not in upload_body, (
        "the upload toast claims the document is indexed; the 202 response only "
        "means it was accepted and queued")
    assert "Queued" in upload_body


def test_the_document_table_polls_while_work_is_outstanding():
    """Polling is now the only way a user learns the outcome.

    While ingestion was synchronous this branch was unreachable — the upload
    response already said 'indexed' — so losing it would not have failed any
    test. It is load-bearing now.
    """
    source = _strip_comments((JS_DIR / "knowledge.js").read_text(encoding="utf-8"))
    assert "processing" in source and "pending" in source, (
        "the table no longer recognises non-terminal states, so it will never poll")
    assert "setTimeout(refresh" in source, "the refresh poll is gone"


def test_no_test_reads_a_repo_file_with_the_platform_encoding():
    """``read_text()`` with no encoding is a Windows-only failure waiting to happen.

    ``Path.read_text()`` and ``open()`` default to the *platform* encoding: UTF-8
    on Linux, cp1252 on Windows. A test that reads a repository file without
    saying ``encoding="utf-8"`` therefore passes in CI and crashes on a
    developer's machine — which is exactly what happened:

        UnicodeDecodeError: 'charmap' codec can't decode byte 0x90

    The byte is the third of ``\\xe2\\x86\\x90`` — the ``←`` in
    ``templates/errors/404.html``. Only five byte values are undefined in
    cp1252 (0x81, 0x8D, 0x8F, 0x90, 0x9D), so most non-ASCII text decodes to
    *mojibake* rather than raising; this one happens to raise, which is the
    luckier outcome.

    Checked across the whole test suite rather than fixed once, because the next
    person to read a template will reach for the same convenient default.
    """
    tests_dir = Path(__file__).resolve().parent.parent
    pattern = re.compile(r"\.(?:read_text|write_text)\(\s*\)|"
                         r"\.(?:read_text|write_text)\((?![^)]*encoding=)[^)]+\)")
    # Docstrings are stripped first, or this test flags the prose in its own
    # docstring explaining the rule — which is how a guard becomes noise nobody
    # trusts. Blanked rather than deleted so line numbers stay meaningful.
    docstrings = re.compile(r'("""|\'\'\')(?:.|\n)*?\1')

    offenders = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        source = docstrings.sub(lambda m: "\n" * m.group(0).count("\n"), source)
        for number, line in enumerate(source.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, (
        "read_text()/write_text() without encoding=\"utf-8\" — these fail on "
        f"Windows where the default is cp1252: {offenders}")


def test_polling_uses_a_single_timer():
    """Every manual Refresh during indexing would otherwise start another chain.

    The timers multiply rather than replace, so the page ends up calling the API
    several times per second and never stops.
    """
    source = _strip_comments((JS_DIR / "knowledge.js").read_text(encoding="utf-8"))
    assert "clearTimeout" in source, (
        "refresh() does not clear its pending timer, so repeated refreshes stack "
        "into overlapping polling loops")
