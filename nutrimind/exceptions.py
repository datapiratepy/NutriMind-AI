"""Exception hierarchy for NutriMind AI.

Every exception carries a short, user-friendly ``message``, an optional
``hint`` with a concrete next step, and an ``http_status`` used by the JSON
error handlers. Route handlers and CLI tools render these
directly instead of leaking stack traces (ARCHITECTURE.md §9).

This module is deliberately dependency-free: it never imports the IBM SDK.
SDK-specific error translation lives in ``nutrimind.services.llm.watsonx_client``.
"""

from __future__ import annotations


class NutriMindError(Exception):
    """Base class for all application errors."""

    default_message: str = "Something went wrong."
    #: HTTP status used when this error escapes to a JSON error handler.
    http_status: int = 500
    #: Stable machine-readable code for API clients.
    error_code: str = "internal_error"

    def __init__(self, message: str | None = None, *, hint: str | None = None) -> None:
        self.message = message or self.default_message
        self.hint = hint
        super().__init__(self.message)

    @property
    def user_message(self) -> str:
        """Message safe to show to an end user, with the hint appended if present."""
        if self.hint:
            return f"{self.message} {self.hint}"
        return self.message

    def to_payload(self) -> dict:
        """JSON body used by the API error handlers."""
        payload: dict = {"code": self.error_code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return payload


class ConfigurationError(NutriMindError):
    """Invalid or incomplete application configuration (.env problems)."""

    default_message = "The application is not configured correctly."
    http_status = 500
    error_code = "configuration_error"


# ---------------------------------------------------------------------------
# IBM watsonx.ai errors
# ---------------------------------------------------------------------------

class WatsonxError(NutriMindError):
    """Base class for all IBM watsonx.ai related failures."""

    default_message = "The IBM watsonx.ai service returned an error."
    http_status = 502
    error_code = "watsonx_error"


class WatsonxAuthError(WatsonxError):
    """Authentication or authorization failed (401/403)."""

    default_message = "IBM Cloud rejected the API key."
    error_code = "watsonx_auth_error"


class WatsonxProjectError(WatsonxError):
    """The project ID is wrong, or watsonx.ai Runtime is not associated with it."""

    default_message = "The watsonx.ai project could not be used."
    error_code = "watsonx_project_error"


class WatsonxModelError(WatsonxError):
    """The configured model ID is not available on this instance/region."""

    default_message = "The configured model is not available."
    error_code = "watsonx_model_error"


class WatsonxQuotaError(WatsonxError):
    """Rate limited or the Lite plan's monthly token quota is exhausted (429)."""

    default_message = "The IBM watsonx.ai usage limit was reached."
    http_status = 429
    error_code = "watsonx_quota_error"


class WatsonxConnectionError(WatsonxError):
    """Network failure, timeout, or a 5xx from the service."""

    default_message = "Could not reach IBM watsonx.ai."
    http_status = 503
    error_code = "watsonx_connection_error"


# ---------------------------------------------------------------------------
# Retrieval / documents / input validation
# ---------------------------------------------------------------------------

class RetrievalError(NutriMindError):
    """The vector store or retriever failed."""

    default_message = "Knowledge retrieval failed."
    http_status = 500
    error_code = "retrieval_error"


class DocumentProcessingError(NutriMindError):
    """A knowledge-base document could not be read, chunked, or indexed."""

    default_message = "The document could not be processed."
    http_status = 422
    error_code = "document_error"


class ValidationError(NutriMindError):
    """User input failed server-side validation."""

    default_message = "The provided input is not valid."
    http_status = 400
    error_code = "validation_error"
