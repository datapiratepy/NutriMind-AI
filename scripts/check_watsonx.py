#!/usr/bin/env python3
"""IBM watsonx.ai connectivity check for NutriMind AI.

Verifies, in order: .env presence, configuration validity, application mode,
demo-mode fallback, SDK installation, endpoint URL, IAM authentication +
project access, Granite chat model availability (with lifecycle status),
embedding model availability, live chat generation, streaming, and embedding
generation.

Usage:
    python scripts/check_watsonx.py                 # full check
    python scripts/check_watsonx.py --skip-inference  # zero-token checks only
    python scripts/check_watsonx.py --env path/to/.env

Exit code 0 when nothing failed; 1 otherwise. The full run consumes fewer
than ~100 of your monthly Lite-plan tokens (inference smoke tests only).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow `python scripts/check_watsonx.py` from the repo root (or anywhere).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nutrimind.config import (  # noqa: E402
    KNOWN_WATSONX_HOSTS,
    load_settings,
    resolve_app_mode,
    validate_settings,
)
from nutrimind.exceptions import NutriMindError, WatsonxError  # noqa: E402

# ---------------------------------------------------------------------------
# Color-coded reporting
# ---------------------------------------------------------------------------

class Reporter:
    """Minimal color console reporter (ANSI; honors NO_COLOR)."""

    def __init__(self) -> None:
        if os.name == "nt":
            os.system("")  # enable VT escape processing on Windows consoles
        self.use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        self.passed = 0
        self.failed = 0
        self.warned = 0
        self.skipped = 0

    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.use_color else text

    def header(self, text: str) -> None:
        print(f"\n{self._c('1', text)}")

    def ok(self, name: str, detail: str = "") -> None:
        self.passed += 1
        print(f"  {self._c('32', '[PASS]')} {name}" + (f" — {detail}" if detail else ""))

    def fail(self, name: str, detail: str = "", hint: str = "") -> None:
        self.failed += 1
        print(f"  {self._c('31', '[FAIL]')} {name}" + (f" — {detail}" if detail else ""))
        if hint:
            print(f"         {self._c('33', 'fix:')} {hint}")

    def warn(self, name: str, detail: str = "") -> None:
        self.warned += 1
        print(f"  {self._c('33', '[WARN]')} {name}" + (f" — {detail}" if detail else ""))

    def skip(self, name: str, detail: str = "") -> None:
        self.skipped += 1
        print(f"  {self._c('36', '[SKIP]')} {name}" + (f" — {detail}" if detail else ""))

    def info(self, text: str) -> None:
        print(f"         {text}")

    def summary(self) -> int:
        print(
            f"\n{self._c('1', 'Summary:')} "
            f"{self._c('32', str(self.passed) + ' passed')}, "
            f"{self._c('31', str(self.failed) + ' failed')}, "
            f"{self._c('33', str(self.warned) + ' warnings')}, "
            f"{self._c('36', str(self.skipped) + ' skipped')}"
        )
        return 1 if self.failed else 0


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_demo_fallback(report: Reporter) -> None:
    """Prove the zero-credential fallback works end to end."""
    from nutrimind.services.llm.demo_client import DemoClient

    client = DemoClient(reason="connectivity-check probe")
    result = client.chat([{"role": "user", "content": "hello"}])
    chunks = list(client.chat_stream([{"role": "user", "content": "hello"}]))
    vector = client.embed(["demo fallback"])[0]
    if result.text and chunks and len(vector) == 384:
        report.ok("Demo mode fallback", "chat, streaming and embeddings respond deterministically")
    else:  # pragma: no cover — deterministic by construction
        report.fail("Demo mode fallback", "demo client returned unexpected output")


def run_checks(env_path: Path | None, skip_inference: bool) -> int:
    report = Reporter()
    report.header("NutriMind AI — IBM watsonx.ai connectivity check")

    # 1. .env file -----------------------------------------------------------
    dotenv = env_path or (REPO_ROOT / ".env")
    if dotenv.exists():
        report.ok(".env file", str(dotenv))
    else:
        report.warn(".env file", f"not found at {dotenv} — using environment/defaults "
                                 "(copy .env.example to .env to configure)")

    # 2. Configuration -------------------------------------------------------
    try:
        settings = load_settings(dotenv if dotenv.exists() else None)
    except Exception as exc:  # noqa: BLE001
        report.fail("Configuration", str(exc))
        return report.summary()

    errors, warnings = validate_settings(settings)
    for message in errors:
        report.fail("Configuration", message)
    for message in warnings:
        report.warn("Configuration", message)
    if not errors:
        report.ok("Configuration", "all settings valid")

    # 3. Application mode ----------------------------------------------------
    resolved = resolve_app_mode(settings)
    report.ok("Application mode", f"requested='{settings.app_mode}' → effective='{resolved}'")

    # 4. Demo fallback (always verified — it is the safety net) --------------
    check_demo_fallback(report)

    if errors:
        return report.summary()
    if resolved == "demo":
        report.header("Demo mode is active — IBM checks skipped")
        report.info("Add credentials to .env (docs/IBM_SETUP.md) and rerun for live checks.")
        return report.summary()

    # 5. SDK installed -------------------------------------------------------
    try:
        import ibm_watsonx_ai

        report.ok("IBM SDK installed", f"ibm-watsonx-ai {getattr(ibm_watsonx_ai, '__version__', '?')}")
    except ImportError:
        report.fail("IBM SDK installed", "the 'ibm-watsonx-ai' package is missing",
                    hint="pip install -r requirements.txt")
        return report.summary()

    # 6. Endpoint URL --------------------------------------------------------
    wx = settings.watsonx
    if wx.host_is_known_region:
        report.ok("Endpoint URL", wx.url)
    else:
        report.warn("Endpoint URL", f"{wx.url} is not a recognized regional endpoint")
        report.info("Known endpoints: " + ", ".join(f"https://{h}" for h in KNOWN_WATSONX_HOSTS))

    # 7. IAM authentication + project access ---------------------------------
    from nutrimind.services.llm.watsonx_client import WatsonxClient

    try:
        client = WatsonxClient(wx)
        report.ok("IAM authentication + project", f"project {wx.project_id[:8]}…")
    except WatsonxError as exc:
        report.fail("IAM authentication + project", exc.message, hint=exc.hint or "")
        return report.summary()

    # 8. Granite chat model ---------------------------------------------------
    try:
        chat_models = client.available_chat_models()
        if wx.model_id in chat_models:
            status = client.model_lifecycle_status(wx.model_id)
            if status and status != "available":
                report.warn("Granite chat model",
                            f"{wx.model_id} is accessible but lifecycle status is '{status}'")
            else:
                report.ok("Granite chat model", f"{wx.model_id} is available")
        else:
            granite = [m for m in chat_models if "granite" in m][:8]
            report.fail("Granite chat model",
                        f"'{wx.model_id}' is not in this region's chat catalog",
                        hint="Set WATSONX_MODEL_ID to one of: " + (", ".join(granite) or "—"))
    except WatsonxError as exc:
        report.fail("Granite chat model", exc.message, hint=exc.hint or "")

    # 9. Embedding model -----------------------------------------------------
    try:
        embedding_models = client.available_embedding_models()
        if wx.embedding_model_id in embedding_models:
            report.ok("Embedding model", f"{wx.embedding_model_id} is available")
        else:
            report.fail("Embedding model",
                        f"'{wx.embedding_model_id}' is not in this region's catalog",
                        hint="Set WATSONX_EMBEDDING_MODEL_ID to one of: "
                             + (", ".join(embedding_models[:8]) or "—"))
    except WatsonxError as exc:
        report.fail("Embedding model", exc.message, hint=exc.hint or "")

    # 10-12. Inference smoke tests (token-consuming) --------------------------
    if skip_inference:
        report.skip("Chat generation", "--skip-inference")
        report.skip("Streaming", "--skip-inference")
        report.skip("Embedding generation", "--skip-inference")
        return report.summary()

    if report.failed:
        report.skip("Inference smoke tests", "skipped because earlier checks failed")
        return report.summary()

    try:
        result = client.chat(
            [{"role": "user", "content": "Reply with exactly one word: OK"}],
            max_tokens=5, temperature=0.0,
        )
        report.ok("Chat generation",
                  f"reply='{result.text.strip()[:40]}' · {result.total_tokens} tokens")
    except WatsonxError as exc:
        report.fail("Chat generation", exc.message, hint=exc.hint or "")

    try:
        pieces = list(client.chat_stream(
            [{"role": "user", "content": "Count: 1 2 3"}], max_tokens=8, temperature=0.0,
        ))
        report.ok("Streaming", f"received {len(pieces)} chunks")
    except WatsonxError as exc:
        report.fail("Streaming", exc.message, hint=exc.hint or "")

    try:
        vector = client.embed(["NutriMind connectivity test"])[0]
        report.ok("Embedding generation", f"vector dimensions: {len(vector)}")
    except WatsonxError as exc:
        report.fail("Embedding generation", exc.message, hint=exc.hint or "")

    report.info("Estimated tokens consumed by this run: < 100")
    return report.summary()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--env", type=Path, default=None, help="path to a .env file")
    parser.add_argument("--skip-inference", action="store_true",
                        help="run only zero-token checks (no quota consumed)")
    args = parser.parse_args()
    try:
        return run_checks(args.env, args.skip_inference)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except NutriMindError as exc:
        print(f"\nError: {exc.user_message}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
