"""Health Advisor Agent — condition-aware guidance, grounded when possible.

Retrieves condition-relevant passages (query enriched with profile
conditions), includes deterministic daily targets for goal questions, and
always applies the safety framing from its system prompt.
"""

from __future__ import annotations

import time
from typing import Iterator

from nutrimind.agents.base_agent import AgentRequest, BaseAgent, Event, status, token

_GENERAL_KNOWLEDGE_BANNER = "\n\n---\n*General knowledge — not from your documents.*"


class HealthAdvisorAgent(BaseAgent):
    """Practical dietary guidance for conditions, goals and life stages."""

    name = "health_advisor"
    prompt_name = "health_advisor"

    def run(self, request: AgentRequest) -> Iterator[Event]:
        started = time.perf_counter()

        query = request.message
        if request.profile is not None and request.profile.medical_conditions:
            query += " " + " ".join(request.profile.medical_conditions)
        yield status("Searching guidance documents…")
        retrieval = request.toolbox.retrieve_knowledge(query)

        context_parts = [self._profile_context(request)]
        if request.profile is not None:
            targets = request.toolbox.calculate_targets(request.profile)
            context_parts.append(
                f"Deterministic daily targets: {targets.calories} kcal, "
                f"protein {targets.protein_g} g, fat {targets.fat_g} g, "
                f"carbs {targets.carbs_g} g, fiber {targets.fiber_g} g, "
                f"water {targets.water_l} L.")
        if retrieval.grounded:
            yield status(f"Found {len(retrieval.chunks)} relevant passages — "
                         "preparing grounded guidance…")
            context_parts.append(f"PASSAGES:\n{retrieval.context_text()}")
        else:
            yield status("No matching documents — advising from general "
                         "knowledge…")

        user_content = ("\n\n".join(context_parts)
                        + f"\n\nUSER REQUEST: {request.message}")
        messages = [{"role": "system", "content": self.system_prompt()},
                    *request.history,
                    {"role": "user", "content": user_content}]
        collected: list[str] = []
        yield from self._stream_llm(request, messages, collected, max_tokens=700)
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
