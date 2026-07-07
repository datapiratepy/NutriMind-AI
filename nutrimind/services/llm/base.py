"""LLM client interface shared by the live watsonx client and the demo client.

Everything above this layer (agents, services, routes) depends only on this
contract — nothing else in the codebase knows which backend is active.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Sequence

#: Chat message shape: ``{"role": "system"|"user"|"assistant", "content": str}``
Message = dict[str, str]


@dataclass(frozen=True)
class ChatResult:
    """A completed (non-streamed) chat generation."""

    text: str
    model_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw: dict | None = field(default=None, repr=False)


class LLMClient(ABC):
    """Contract for chat generation and embeddings.

    Implementations:
      * :class:`~nutrimind.services.llm.watsonx_client.WatsonxClient` — IBM
        watsonx.ai via the official SDK (Granite models).
      * :class:`~nutrimind.services.llm.demo_client.DemoClient` — deterministic,
        zero-credential fallback used for demos and tests.
    """

    #: "live" or "demo" — surfaced by /api/health and the UI banner.
    mode: str = "unset"
    #: Human-readable reason/details for the current mode.
    detail: str = ""

    @abstractmethod
    def chat(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> ChatResult:
        """Run a chat completion and return the full result.

        :raises nutrimind.exceptions.WatsonxError: on any backend failure.
        """

    @abstractmethod
    def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> Iterator[str]:
        """Yield text deltas as they are generated (for SSE streaming)."""

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""

    @abstractmethod
    def ping(self) -> None:
        """Cheap connectivity/validity check.

        Must be side-effect free and consume no generation tokens.
        :raises nutrimind.exceptions.WatsonxError: when the backend is unusable.
        """

    def describe(self) -> dict:
        """Summary for health endpoints and logging."""
        return {"mode": self.mode, "detail": self.detail}
