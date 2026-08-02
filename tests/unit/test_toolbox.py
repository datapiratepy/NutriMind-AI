"""Unit tests for the Toolbox's lazy RAG resolution and retrieval degradation.

The point of both behaviours is blast radius: a vector-store problem must not
take down the request paths that never touch the vector store.
"""

from __future__ import annotations

import pytest

from nutrimind.agents.tools import Toolbox
from nutrimind.exceptions import RetrievalError


class _FakeRetrievalResult:
    def __init__(self):
        self.chunks = ["a", "b"]
        self.threshold = 0.35
        self.provider = "hash"


class _FakeRAG:
    """Minimal stand-in: enough surface for the Toolbox, no Chroma, no I/O."""

    def __init__(self):
        self.provider = type("P", (), {"name": "hash"})()
        self.retrieve_calls = 0

    def retrieve(self, query, *, top_k=None):  # noqa: ARG002 — interface parity
        self.retrieve_calls += 1
        return _FakeRetrievalResult()


# -- laziness ------------------------------------------------------------------

def test_factory_is_not_called_when_no_retrieval_happens():
    """Small talk and BMI answer without retrieval; they must not open the store."""
    calls = []
    toolbox = Toolbox(lambda: calls.append(1))
    toolbox.compute_bmi(175, 70)
    assert calls == []


def test_factory_is_called_once_and_memoised():
    built = []

    def factory():
        built.append(1)
        return _FakeRAG()

    toolbox = Toolbox(factory)
    toolbox.retrieve_knowledge("protein")
    toolbox.retrieve_knowledge("calcium")
    assert len(built) == 1


# -- honest provider reporting -------------------------------------------------

def test_provider_reports_not_used_before_any_retrieval():
    """Naming a provider that never ran would be false metadata the UI renders."""
    assert Toolbox(lambda: _FakeRAG()).embedding_provider_name() == "not_used"


def test_provider_reports_real_name_after_retrieval():
    toolbox = Toolbox(lambda: _FakeRAG())
    toolbox.retrieve_knowledge("protein")
    assert toolbox.embedding_provider_name() == "hash"


def test_provider_reports_unavailable_after_failure():
    def boom():
        raise RetrievalError("vector store is gone")

    toolbox = Toolbox(boom)
    toolbox.retrieve_knowledge("protein")
    assert toolbox.embedding_provider_name() == "unavailable"


# -- degradation ---------------------------------------------------------------

@pytest.mark.parametrize("failure", [
    RetrievalError("vector store is gone"),
    RuntimeError("chroma exploded"),
])
def test_retrieval_failure_degrades_instead_of_raising(failure):
    """The agent should fall back to its general-knowledge path, not 500."""
    def boom():
        raise failure

    result = Toolbox(boom).retrieve_knowledge("protein in paneer")
    assert result.grounded is False
    assert result.chunks == []
    assert result.citations() == []
    assert result.provider == "unavailable"


def test_retrieval_failure_stays_visible_in_tool_calls():
    """Degrading silently would look identical to an empty knowledge base."""
    def boom():
        raise RetrievalError("vector store is gone")

    toolbox = Toolbox(boom)
    toolbox.retrieve_knowledge("protein")
    summary = toolbox.calls_summary()
    assert len(summary) == 1
    assert summary[0]["tool"] == "retrieve_knowledge"
    assert "unavailable" in summary[0]["summary"]


def test_successful_retrieval_is_recorded_normally():
    toolbox = Toolbox(lambda: _FakeRAG())
    toolbox.retrieve_knowledge("protein")
    summary = toolbox.calls_summary()
    assert summary[0]["tool"] == "retrieve_knowledge"
    assert "2 passages" in summary[0]["summary"]
