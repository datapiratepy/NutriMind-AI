"""Coordinator Agent — explainable hybrid routing + orchestration.

Two-stage routing (ARCHITECTURE.md §3.1, token budget principle #4):

1. **Rule stage (0 tokens):** ordered regex rules classify obvious intents,
   including two the coordinator answers *itself* deterministically
   (small talk, BMI check — no LLM call at all).
2. **LLM stage (~60 output tokens):** only ambiguous messages go to Granite
   with a strict JSON classification prompt. In demo mode this stage is
   skipped and the default applies (the demo backend cannot classify).

Every decision carries ``intent``, ``agent``, ``reason`` and ``method`` —
surfaced verbatim in the API response and the UI routing badge.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Iterator

from nutrimind.agents.base_agent import AgentRequest, Event, status, token
from nutrimind.agents.tools import parse_llm_json
from nutrimind.prompts import load_prompt

logger = logging.getLogger(__name__)

VALID_AGENTS = ("knowledge_agent", "meal_planner", "meal_analyzer",
                "health_advisor")


@dataclass(frozen=True)
class RoutingDecision:
    """Explainable outcome of the routing stage."""

    intent: str
    agent: str          # a VALID_AGENTS id, or "coordinator" for direct handling
    reason: str
    method: str         # "rules" | "llm" | "default"

    def to_dict(self) -> dict:
        return asdict(self)


#: Ordered rule table: (intent, agent, reason, compiled pattern).
_RULES: list[tuple[str, str, str, re.Pattern]] = [
    ("Small Talk", "coordinator",
     "Greeting or casual message — answered directly without any AI call.",
     re.compile(r"^(hi|hello|hey|namaste|good\s+(morning|afternoon|evening)|"
                r"thanks|thank you|how are you)\b[\s!.,?]*$", re.IGNORECASE)),
    ("BMI Check", "coordinator",
     "BMI is deterministic math — computed directly from the profile, no LLM.",
     re.compile(r"\b(bmi|body mass index|ideal weight|weight category)\b",
                re.IGNORECASE)),
    ("Meal Analysis", "meal_analyzer",
     "The user described food they already ate and wants it analyzed.",
     re.compile(r"\b(i ate|i had|ate today|today i ate|analyze (my|this)|"
                r"for (breakfast|lunch|dinner|snack) i)\b", re.IGNORECASE)),
    ("Meal Planning", "meal_planner",
     "The user asked for a personalized meal or diet plan.",
     re.compile(r"\b(meal plan|diet plan|diet chart|plan (my|a) (meals?|diet|day)|"
                r"(create|make|suggest|generate|give me) .{0,40}(plan|menu)|"
                r"\d{3,4}\s*(k?cal|calorie)s? .{0,20}plan)\b", re.IGNORECASE)),
    ("Health Advice", "health_advisor",
     "The request concerns a health condition, goal or life stage.",
     re.compile(r"\b(diabet\w*|hypertension|blood pressure|cholesterol|"
                r"heart (health|disease)|thyroid|pcos|pregnan\w*|"
                r"weight (loss|gain)|lose weight|gain weight|muscles?\b|"
                r"bulking|seniors?\b|elderly|child(ren)?('s)? nutrition|"
                r"sports nutrition|workout diet)", re.IGNORECASE)),
    ("Nutrition Question", "knowledge_agent",
     "Factual nutrition question — best answered with knowledge-base retrieval.",
     re.compile(r"\b(protein|calor|vitamin|mineral|iron|calcium|fiber|fibre|"
                r"carb|nutrient|magnesium|zinc|omega|antioxidant|"
                r"is .{2,40} (healthy|good|bad)|can (i|you) eat|benefits of|"
                r"rich in|sources? of|how much .{2,30} in)\b", re.IGNORECASE)),
]

_DEFAULT = RoutingDecision(
    intent="Ambiguous", agent="knowledge_agent",
    reason="No rule matched; defaulted to the Knowledge Agent.",
    method="default")


def apply_rules(message: str) -> RoutingDecision | None:
    """Deterministic stage: first matching rule wins (0 tokens). Pure function."""
    for intent, agent, reason, pattern in _RULES:
        if pattern.search(message):
            return RoutingDecision(intent=intent, agent=agent, reason=reason,
                                   method="rules")
    return None


class Coordinator:
    """Routes each request and orchestrates the chosen agent's event stream."""

    name = "coordinator"

    def __init__(self, registry: dict[str, type]) -> None:
        self._registry = registry

    # -- routing ---------------------------------------------------------------

    def route(self, message: str, llm) -> RoutingDecision:
        decision = apply_rules(message)
        if decision:
            logger.info("routing[rules] '%s' -> %s (%s)",
                        message[:50], decision.agent, decision.intent)
            return decision

        if llm.mode == "demo":  # demo backend cannot classify arbitrary text
            decision = RoutingDecision(
                intent="Ambiguous", agent=_DEFAULT.agent,
                reason=_DEFAULT.reason + " (LLM classification skipped in demo mode.)",
                method="default")
            logger.info("routing[default/demo] -> %s", decision.agent)
            return decision

        try:
            result = llm.chat(
                [{"role": "system", "content": load_prompt("coordinator")},
                 {"role": "user", "content": message}],
                max_tokens=80, temperature=0.0)
            data = parse_llm_json(result.text)
            agent = str(data.get("agent", ""))
            if agent not in VALID_AGENTS:
                raise ValueError(f"unknown agent '{agent}'")
            decision = RoutingDecision(
                intent=str(data.get("intent", "Classified"))[:40],
                agent=agent,
                reason=str(data.get("reason", "Classified by IBM Granite."))[:200],
                method="llm")
            logger.info("routing[llm] '%s' -> %s", message[:50], decision.agent)
            return decision
        except Exception as exc:  # noqa: BLE001 — routing must never fail hard
            logger.warning("LLM routing failed (%s); using default", exc)
            return _DEFAULT

    # -- orchestration -----------------------------------------------------------

    def handle(self, request: AgentRequest) -> Iterator[Event]:
        """Route, then stream the chosen agent's events (routing merged in)."""
        yield status("Analyzing your request…")
        decision = self.route(request.message, request.llm)
        yield {"type": "routing", **decision.to_dict()}

        if decision.agent == "coordinator":
            yield from self._handle_directly(request, decision)
            return

        agent = self._registry[decision.agent]()
        for event in agent.run(request):
            if event["type"] == "final":
                event["meta"]["routing"] = decision.to_dict()
            yield event

    # -- direct deterministic handling (0 LLM tokens) ------------------------------

    def _handle_directly(self, request: AgentRequest,
                         decision: RoutingDecision) -> Iterator[Event]:
        started = time.perf_counter()
        if decision.intent == "BMI Check":
            text = self._bmi_reply(request)
            source = "deterministic"
        else:  # Small Talk
            text = ("Hello! I'm NutriMind — a team of specialized AI agents for "
                    "nutrition. Ask me a nutrition question, request a meal plan, "
                    "tell me what you ate today, or ask for condition-specific "
                    "guidance.")
            source = "deterministic"
        yield token(text)
        meta = {
            "agent": self.name,
            "grounded": False,
            "response_source": source,
            "embedding_provider": request.toolbox.embedding_provider_name(),
            "llm_mode": request.llm.mode,
            "retrieved_chunks": 0,
            "citations": [],
            "tools_used": request.toolbox.calls_summary(),
            "generation_ms": round((time.perf_counter() - started) * 1000),
            "tokens": {"total": 0, "estimated": False},
            "routing": decision.to_dict(),
        }
        yield {"type": "final", "text": text, "meta": meta}

    def _bmi_reply(self, request: AgentRequest) -> str:
        if request.profile is None:
            return ("I can calculate your BMI instantly once your profile has "
                    "height and weight — please fill it in on the Profile page.")
        result = request.toolbox.compute_bmi(request.profile.height_cm,
                                             request.profile.weight_kg)
        tips = " ".join(result.suggestions[:2])
        return (f"Your BMI is **{result.bmi}** ({result.category}). A healthy "
                f"weight range for your height is "
                f"{result.ideal_weight_min_kg}-{result.ideal_weight_max_kg} kg. "
                f"{tips}\n\n*(Computed deterministically — no AI involved.)*")
