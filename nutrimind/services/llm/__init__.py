"""LLM client factory — decides between live watsonx.ai and demo mode.

Resolution rules (ARCHITECTURE.md §6, "Demo mode"):
  * ``APP_MODE=demo``  -> DemoClient, always.
  * ``APP_MODE=live``  -> WatsonxClient; any failure is fatal (fail fast,
    never a silent fallback — 'live' means live).
  * ``APP_MODE=auto``  -> WatsonxClient when credentials exist *and* a
    zero-token ping succeeds; otherwise DemoClient with the reason recorded
    (missing credentials / auth failure / service unreachable).

The chosen client is cached process-wide; ``get_llm_client(refresh=True)``
rebuilds it (e.g. after credentials change).
"""

from __future__ import annotations

import logging
import threading

from nutrimind.config import Settings, load_settings
from nutrimind.exceptions import ConfigurationError, WatsonxError
from nutrimind.services.llm.base import ChatResult, LLMClient, Message  # noqa: F401
from nutrimind.services.llm.demo_client import DemoClient

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client: LLMClient | None = None


def _build_client(settings: Settings) -> LLMClient:
    if settings.app_mode == "demo":
        logger.info("LLM mode: demo (APP_MODE=demo)")
        return DemoClient(reason="Demo mode explicitly enabled (APP_MODE=demo).")

    if not settings.watsonx.has_credentials:
        if settings.app_mode == "live":
            raise ConfigurationError(
                "APP_MODE=live but IBM watsonx.ai credentials are missing.",
                hint="Fill WATSONX_APIKEY / WATSONX_PROJECT_ID / WATSONX_URL in "
                     ".env (docs/IBM_SETUP.md) or use APP_MODE=auto.",
            )
        logger.warning("LLM mode: demo (IBM credentials not configured)")
        return DemoClient(reason="IBM credentials not configured.")

    # Imported lazily so demo mode never needs the IBM SDK at import time.
    from nutrimind.services.llm.watsonx_client import WatsonxClient

    try:
        client = WatsonxClient(settings.watsonx)
        client.ping()
        logger.info("LLM mode: live (%s)", settings.watsonx.model_id)
        return client
    except WatsonxError as exc:
        if settings.app_mode == "live":
            raise
        logger.warning("LLM mode: demo — watsonx.ai unavailable: %s", exc.user_message)
        return DemoClient(reason=f"watsonx.ai unavailable: {exc.user_message}")


def get_llm_client(settings: Settings | None = None, *, refresh: bool = False) -> LLMClient:
    """Return the process-wide LLM client, building it on first use."""
    global _client
    with _lock:
        if _client is None or refresh:
            _client = _build_client(settings or load_settings())
        return _client


def describe_llm(settings: Settings | None = None) -> dict:
    """Mode summary for ``/api/health`` and the UI mode banner."""
    return get_llm_client(settings).describe()
