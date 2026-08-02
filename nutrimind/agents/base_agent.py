"""Agent foundation: request context, event protocol, shared helpers.

Every agent is a generator of **events** so one code path serves both SSE
streaming and blocking JSON (docs/AGENTS.md "Streaming protocol"):

    {"type": "status", "message": str}      progress for the UI
    {"type": "token",  "text": str}         one generated text delta
    {"type": "final",  "text": str, "meta": {...}}   complete answer + metadata

The ``meta`` dict is the UI-metadata contract (agent, grounded,
response_source, provider, citations, chunks, tools_used, timing, tokens) —
the frontend renders badges from it verbatim, never inferring.

``embedding_provider`` reports what actually ran this turn: a provider name
when retrieval happened, ``"not_used"`` when the turn needed no retrieval, and
``"unavailable"`` when the vector store could not be opened.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from nutrimind.agents.tools import Toolbox
from nutrimind.prompts import load_prompt
from nutrimind.services.llm.base import LLMClient, Message

Event = dict


@dataclass
class AgentRequest:
    """Everything an agent needs for one turn (built by the chat route)."""

    message: str
    toolbox: Toolbox
    llm: LLMClient
    profile: object | None = None            # UserProfile | None
    history: Sequence[Message] = field(default_factory=tuple)


def status(message: str) -> Event:
    return {"type": "status", "message": message}


def token(text: str) -> Event:
    return {"type": "token", "text": text}


class BaseAgent(ABC):
    """Contract + shared machinery for all specialist agents."""

    #: registry id, e.g. "knowledge_agent" — also the UI badge label key.
    name: str = "unset"
    #: prompt file (nutrimind/prompts/<prompt_name>.txt)
    prompt_name: str = "unset"

    @abstractmethod
    def run(self, request: AgentRequest) -> Iterator[Event]:
        """Yield status/token events and exactly one terminal 'final' event."""

    # -- shared helpers -------------------------------------------------------

    def system_prompt(self) -> str:
        return load_prompt(self.prompt_name)

    def _profile_context(self, request: AgentRequest) -> str:
        if request.profile is None:
            return "No user profile is available."
        return f"User profile: {request.profile.summary_for_prompt()}."

    def _stream_llm(
        self,
        request: AgentRequest,
        messages: list[Message],
        collector: list[str],
        *,
        max_tokens: int = 600,
        temperature: float = 0.4,
    ) -> Iterator[Event]:
        """Stream generation as token events, accumulating text in ``collector``."""
        for delta in request.llm.chat_stream(messages, max_tokens=max_tokens,
                                             temperature=temperature):
            collector.append(delta)
            yield token(delta)

    def _final(
        self,
        request: AgentRequest,
        text: str,
        *,
        started: float,
        grounded: bool = False,
        response_source: str = "general_knowledge",
        citations: list[dict] | None = None,
        retrieved_chunks: int = 0,
        extra: dict | None = None,
        tokens_used: int | None = None,
    ) -> Event:
        """Build the terminal event with the full UI-metadata contract."""
        meta = {
            "agent": self.name,
            "grounded": grounded,
            "response_source": "grounded" if grounded else response_source,
            "embedding_provider": request.toolbox.embedding_provider_name(),
            "llm_mode": request.llm.mode,
            "retrieved_chunks": retrieved_chunks,
            "citations": citations or [],
            "tools_used": request.toolbox.calls_summary(),
            "generation_ms": round((time.perf_counter() - started) * 1000),
            "tokens": ({"total": tokens_used, "estimated": False}
                       if tokens_used is not None
                       else {"total": max(1, len(text) // 4), "estimated": True}),
        }
        if extra:
            meta.update(extra)
        return {"type": "final", "text": text, "meta": meta}
