"""Unit tests for IBM SDK error translation (pure logic, no network)."""

import pytest

from nutrimind.exceptions import (
    WatsonxAuthError,
    WatsonxConnectionError,
    WatsonxError,
    WatsonxModelError,
    WatsonxProjectError,
    WatsonxQuotaError,
)
from nutrimind.services.llm.watsonx_client import translate_watsonx_error


@pytest.mark.parametrize("raw,expected", [
    (Exception("401 Unauthorized: invalid api key"), WatsonxAuthError),
    (Exception("Response 403: Forbidden"), WatsonxAuthError),
    (Exception("project 404: project not found"), WatsonxProjectError),
    (Exception("model 'x' is not supported"), WatsonxModelError),
    (Exception("HTTP 429 Too Many Requests"), WatsonxQuotaError),
    (Exception("rate limit exceeded"), WatsonxQuotaError),
    (Exception("connection timed out"), WatsonxConnectionError),
    (Exception("SSL handshake failed"), WatsonxConnectionError),
    (Exception("something entirely different"), WatsonxError),
])
def test_translation_maps_to_typed_errors(raw, expected):
    translated = translate_watsonx_error(raw)
    assert type(translated) is expected
    assert translated.user_message  # always user-presentable


def test_existing_watsonx_errors_pass_through():
    original = WatsonxQuotaError("already typed")
    assert translate_watsonx_error(original) is original


def test_hints_reference_documentation():
    translated = translate_watsonx_error(Exception("401 unauthorized"))
    assert "IBM_SETUP" in (translated.hint or "")
