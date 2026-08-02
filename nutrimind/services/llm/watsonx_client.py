"""IBM watsonx.ai client — the only module that imports the ``ibm_watsonx_ai`` SDK.

Wraps ``APIClient``/``ModelInference``/``Embeddings`` behind the
:class:`~nutrimind.services.llm.base.LLMClient` contract, translating SDK
exceptions into NutriMind's typed, user-friendly errors.

SDK usage verified against the official v1.5 reference (2026-07-06):
  * ``ModelInference.chat(messages=...)`` -> OpenAI-style response dict
  * ``ModelInference.chat_stream(messages=...)`` -> chunks with choices[0].delta
  * built-in retries: ``max_retries``/``delay_time``/``retry_status_codes``
  * dynamic ``ChatModels``/``EmbeddingModels`` enums list the live catalog
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Iterator, Sequence

from ibm_watsonx_ai import APIClient, Credentials
from ibm_watsonx_ai.foundation_models import Embeddings, ModelInference

from nutrimind.config import WatsonxSettings
from nutrimind.exceptions import (
    WatsonxAuthError,
    WatsonxConnectionError,
    WatsonxError,
    WatsonxModelError,
    WatsonxProjectError,
    WatsonxQuotaError,
)
from nutrimind.services.llm.base import ChatResult, LLMClient, Message

logger = logging.getLogger(__name__)

#: Embedding inputs longer than the model's window are truncated server-side
#: instead of erroring (granite-embedding-278m accepts 512 input tokens).
_EMBED_PARAMS = {"truncate_input_tokens": 512}


def _mentions(text: str, *needles: str) -> bool:
    """True when any needle appears in ``text``.

    Keeps the matcher table below readable and, more importantly, makes the
    boolean grouping explicit — the original chains mixed ``or`` and ``and``
    without parentheses, which relied on the reader knowing Python's operator
    precedence to see that the intent was ``... or (a and b)``.
    """
    return any(needle in text for needle in needles)


def translate_watsonx_error(exc: Exception) -> WatsonxError:
    """Map an SDK/network exception onto NutriMind's typed error hierarchy."""
    if isinstance(exc, WatsonxError):
        return exc

    text = str(exc).lower()

    if _mentions(text, "401", "unauthorized", "invalid api key") or (
            "iam" in text and "token" in text):
        return WatsonxAuthError(
            "IBM Cloud rejected the API key (authentication failed).",
            hint="Re-check WATSONX_APIKEY in .env — see docs/IBM_SETUP.md §4.",
        )
    if _mentions(text, "403", "forbidden", "not authorized"):
        return WatsonxAuthError(
            "The API key is valid but has no access to this resource (403).",
            hint="Ensure the key belongs to the account that owns the watsonx.ai project.",
        )
    if "project" in text and _mentions(text, "404", "not found", "does not exist"):
        return WatsonxProjectError(
            "The watsonx.ai project was not found.",
            hint="Verify WATSONX_PROJECT_ID and that watsonx.ai Runtime is "
                 "associated with the project (docs/IBM_SETUP.md §3, common mistake #1).",
        )
    if "model" in text and _mentions(text, "not supported", "not found",
                                     "unavailable", "invalid"):
        return WatsonxModelError(
            "The configured model ID is not available on this instance/region.",
            hint="Run 'python scripts/check_watsonx.py' to list the models you can use.",
        )
    if _mentions(text, "429", "quota", "rate limit", "too many requests"):
        return WatsonxQuotaError(
            "watsonx.ai rate/usage limit reached (HTTP 429).",
            hint="The Lite plan includes a monthly token allowance that resets "
                 "each month. Try again later or switch APP_MODE=demo.",
        )
    if _mentions(text, "timeout", "timed out", "connection", "name resolution", "ssl"):
        return WatsonxConnectionError(
            "Could not reach IBM watsonx.ai (network problem or timeout).",
            hint="Check your internet connection and that WATSONX_URL matches "
                 "your region (docs/IBM_SETUP.md §6).",
        )
    return WatsonxError(f"watsonx.ai request failed: {exc}")


class WatsonxClient(LLMClient):
    """Live IBM watsonx.ai backend (Granite chat + Granite embeddings)."""

    mode = "live"

    def __init__(self, wx: WatsonxSettings) -> None:
        self._settings = wx
        self.detail = f"IBM watsonx.ai · {wx.model_id}"
        credentials = Credentials(url=wx.url, api_key=wx.api_key)
        try:
            # APIClient performs IAM authentication during construction.
            self._client = APIClient(credentials=credentials, project_id=wx.project_id)
        except Exception as exc:  # noqa: BLE001 — SDK raises broad types
            raise translate_watsonx_error(exc) from exc

        self._model = ModelInference(
            model_id=wx.model_id,
            api_client=self._client,
            validate=False,       # validated explicitly via ping()/check script
            max_retries=3,        # SDK-native retry on 429/503/504/520
            delay_time=0.5,
        )
        self._embeddings: Embeddings | None = None

    # -- internal helpers ---------------------------------------------------

    def _get_embeddings(self) -> Embeddings:
        if self._embeddings is None:
            self._embeddings = Embeddings(
                model_id=self._settings.embedding_model_id,
                params=dict(_EMBED_PARAMS),
                api_client=self._client,
            )
        return self._embeddings

    @staticmethod
    def _chat_params(max_tokens: int, temperature: float) -> dict:
        return {"max_tokens": max_tokens, "temperature": temperature}

    # -- LLMClient contract --------------------------------------------------

    def chat(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> ChatResult:
        try:
            response = self._model.chat(
                messages=list(messages),
                params=self._chat_params(max_tokens, temperature),
            )
        except Exception as exc:  # noqa: BLE001
            raise translate_watsonx_error(exc) from exc

        choices = response.get("choices") or []
        text = (choices[0].get("message") or {}).get("content", "") if choices else ""
        usage = response.get("usage") or {}
        result = ChatResult(
            text=text,
            model_id=response.get("model_id", self._settings.model_id),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            raw=response,
        )
        logger.info(
            "watsonx chat ok model=%s tokens=%s", result.model_id, result.total_tokens
        )
        return result

    def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> Iterator[str]:
        try:
            stream = self._model.chat_stream(
                messages=list(messages),
                params=self._chat_params(max_tokens, temperature),
            )
            for chunk in stream:
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content
        except Exception as exc:  # noqa: BLE001
            raise translate_watsonx_error(exc) from exc

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            return self._get_embeddings().embed_documents(texts=list(texts))
        except Exception as exc:  # noqa: BLE001
            raise translate_watsonx_error(exc) from exc

    def ping(self) -> None:
        """Verify project access + configured model visibility. Zero tokens."""
        try:
            spec = self._client.foundation_models.get_model_specs(
                model_id=self._settings.model_id
            )
        except Exception as exc:  # noqa: BLE001
            raise translate_watsonx_error(exc) from exc
        if not spec:
            raise WatsonxModelError(
                f"Model '{self._settings.model_id}' is not available here.",
                hint="Run 'python scripts/check_watsonx.py' to list usable models.",
            )

    # -- catalog helpers (used by scripts/check_watsonx.py) ------------------

    def available_chat_models(self) -> list[str]:
        """Model IDs usable with the chat API on this instance/region."""
        try:
            enum_cls = self._client.foundation_models.ChatModels
            models = sorted({member.value for member in enum_cls})
            if models:
                return models
        except Exception:  # noqa: BLE001 — fall back to raw specs
            pass
        return self._models_with_function("text_chat")

    def available_embedding_models(self) -> list[str]:
        """Embedding model IDs available on this instance/region."""
        try:
            enum_cls = self._client.foundation_models.EmbeddingModels
            models = sorted({member.value for member in enum_cls})
            if models:
                return models
        except Exception:  # noqa: BLE001
            pass
        return self._models_with_function("embedding")

    def _models_with_function(self, function_id: str) -> list[str]:
        try:
            specs = self._client.foundation_models.get_model_specs()
        except Exception as exc:  # noqa: BLE001
            raise translate_watsonx_error(exc) from exc
        resources = specs.get("resources", []) if isinstance(specs, dict) else []
        model_ids = [
            resource.get("model_id")
            for resource in resources
            if function_id in {f.get("id") for f in resource.get("functions", [])}
        ]
        return sorted(filter(None, model_ids))

    def model_lifecycle_status(self, model_id: str) -> str | None:
        """Current lifecycle phase ('available', 'deprecated', …) or None."""
        try:
            spec = self._client.foundation_models.get_model_specs(model_id=model_id)
        except Exception:  # noqa: BLE001 — lifecycle info is best-effort
            return None
        if not isinstance(spec, dict):
            return None
        today = _dt.date.today().isoformat()
        current = None
        for entry in spec.get("lifecycle", []):
            start = entry.get("start_date") or ""
            if start and start <= today:
                current = entry.get("id")
        return current
