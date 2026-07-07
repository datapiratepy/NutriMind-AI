"""Nutrition Knowledge Agent — RAG-first Q&A with honest grounding.

Workflow (ARCHITECTURE.md §4.4): retrieve → grounded? → yes: generate from
passages with [n] citations · no: answer from general knowledge, explicitly
labeled. Evidence is never invented; citations come only from the retriever.
"""

from __future__ import annotations

import time
from typing import Iterator

from nutrimind.agents.base_agent import AgentRequest, BaseAgent, Event, status, token

_GENERAL_KNOWLEDGE_BANNER = "\n\n---\n*General knowledge — not from your documents.*"


class KnowledgeAgent(BaseAgent):
    """Answers factual nutrition questions, grounded when possible."""

    name = "knowledge_agent"
    prompt_name = "knowledge_agent"

    def run(self, request: AgentRequest) -> Iterator[Event]:
        started = time.perf_counter()
        yield status("Searching the knowledge base…")
        retrieval = request.toolbox.retrieve_knowledge(request.message)

        if retrieval.grounded:
            yield status(f"Found {len(retrieval.chunks)} relevant passages — "
                         "generating a grounded answer…")
            user_content = (
                f"{self._profile_context(request)}\n\n"
                f"PASSAGES:\n{retrieval.context_text()}\n\n"
                f"QUESTION: {request.message}\n\n"
                "Answer using the passages, citing them as [1], [2] where used."
            )
        else:
            yield status("No sufficiently relevant documents — answering from "
                         "general knowledge…")
            user_content = (
                f"{self._profile_context(request)}\n\n"
                "No knowledge-base passages matched this question.\n"
                f"QUESTION: {request.message}\n\n"
                "Answer from general nutrition knowledge."
            )

        messages = [{"role": "system", "content": self.system_prompt()},
                    *request.history,
                    {"role": "user", "content": user_content}]
        collected: list[str] = []
        yield from self._stream_llm(request, messages, collected)
        text = "".join(collected)
        if not retrieval.grounded:
            text += _GENERAL_KNOWLEDGE_BANNER
            yield token(_GENERAL_KNOWLEDGE_BANNER)

        yield self._final(
            request, text, started=started,
            grounded=retrieval.grounded,
            citations=retrieval.citations(),
            retrieved_chunks=len(retrieval.chunks),
        )
