"""Typed tools shared by all agents, with visible invocation logging.

Agents never touch services directly — every capability goes through the
request-scoped :class:`Toolbox`, which records each call so the final
response metadata can show exactly which tools ran (explainable agentic
behavior, ARCHITECTURE.md §3.2).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from nutrimind.retrieval.retriever import RetrievalResult
from nutrimind.services import bmi_service
from nutrimind.services.nutrition_service import (
    Targets,
    get_food_table,
    targets_for_profile,
)
from nutrimind.services.rag_service import RAGService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCall:
    """One recorded tool invocation (name + human-readable outcome)."""

    tool: str
    summary: str


class Toolbox:
    """Request-scoped tool facade; records every invocation."""

    def __init__(self, rag: RAGService) -> None:
        self._rag = rag
        self.calls: list[ToolCall] = []

    def _record(self, tool: str, summary: str) -> None:
        self.calls.append(ToolCall(tool, summary))
        logger.info("tool %s: %s", tool, summary)

    def calls_summary(self) -> list[dict]:
        return [{"tool": c.tool, "summary": c.summary} for c in self.calls]

    def embedding_provider_name(self) -> str:
        return self._rag.provider.name

    # -- tools ------------------------------------------------------------

    def retrieve_knowledge(self, query: str, *, top_k: int | None = None) -> RetrievalResult:
        """Vector search over the knowledge base with grounding threshold."""
        result = self._rag.retrieve(query, top_k=top_k)
        self._record("retrieve_knowledge",
                     f"{len(result.chunks)} passages above threshold "
                     f"{result.threshold} (provider={result.provider})")
        return result

    def calculate_targets(self, profile) -> Targets:
        """Deterministic daily targets (Mifflin-St Jeor); never the LLM."""
        targets = targets_for_profile(profile)
        self._record("calculate_targets",
                     f"{targets.calories} kcal, P{targets.protein_g}g "
                     f"F{targets.fat_g}g C{targets.carbs_g}g")
        return targets

    def lookup_foods(self, items: list[dict]) -> dict:
        """Deterministic nutrient math from the curated composition table."""
        estimate = get_food_table().estimate_meal(items)
        totals = estimate["totals"]
        self._record("lookup_foods",
                     f"{len(estimate['items'])} matched, "
                     f"{len(estimate['unmatched'])} unmatched, "
                     f"{totals['calories']} kcal total")
        return estimate

    def compute_bmi(self, height_cm: float, weight_kg: float) -> bmi_service.BMIResult:
        """Deterministic BMI assessment."""
        result = bmi_service.assess(height_cm, weight_kg)
        self._record("compute_bmi", f"BMI {result.bmi} ({result.category})")
        return result

    def log_meal(self, *, meal_type: str, raw_text: str, estimate: dict,
                 quality_score: int | None = None, suggestions: str | None = None):
        """Persist an analyzed meal to the log (feeds dashboard + history)."""
        from nutrimind.extensions import db
        from nutrimind.models import MealLog

        totals = estimate["totals"]
        log = MealLog(meal_type=meal_type, raw_text=raw_text[:2000],
                      items=estimate["items"], calories=totals["calories"],
                      protein_g=totals["protein_g"], fat_g=totals["fat_g"],
                      carbs_g=totals["carbs_g"], fiber_g=totals["fiber_g"],
                      quality_score=quality_score, suggestions=suggestions)
        db.session.add(log)
        db.session.commit()
        self._record("log_meal", f"logged {totals['calories']} kcal ({meal_type})")
        return log


# ---------------------------------------------------------------------------
# LLM-output helpers shared by agents
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_llm_json(text: str) -> dict:
    """Parse a JSON object out of LLM output (tolerates fences/prose edges).

    :raises ValueError: when no valid JSON object can be found.
    """
    cleaned = _FENCE.sub("", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(cleaned[start:end + 1])


def naive_extract_items(text: str, *, max_items: int = 12) -> list[dict]:
    """Deterministic food-item extraction by scanning known names/aliases.

    Used in demo mode (the demo LLM cannot parse arbitrary text) and as the
    fallback when live LLM extraction fails — the pipeline stays functional
    with zero tokens. Longer matches win on overlap ("veg fried rice" beats
    "rice"); quantities are captured from adjacent numerals.
    """
    table = get_food_table()
    lowered = text.lower()
    spans: list[tuple[int, int, str, float]] = []  # start, end, food, qty

    for food in table.foods:
        for candidate in (food.name, *food.aliases):
            pattern = re.compile(
                r"(?:(\d+(?:\.\d+)?|one|two|three|four|five|half)\s+)?"
                + re.escape(candidate.lower()) + r"e?s?\b")
            for match in pattern.finditer(lowered):
                quantity_word = match.group(1)
                quantity = {"one": 1, "two": 2, "three": 3, "four": 4,
                            "five": 5, "half": 0.5}.get(
                    quantity_word, quantity_word) if quantity_word else 1
                spans.append((match.start(), match.end(), food.name,
                              float(quantity or 1)))

    # Longest-span-wins overlap resolution, then original text order.
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen: list[tuple[int, int, str, float]] = []
    for span in spans:
        if any(not (span[1] <= c[0] or span[0] >= c[1]) for c in chosen):
            continue
        chosen.append(span)
    seen: set[str] = set()
    items: list[dict] = []
    for _, _, name, quantity in sorted(chosen):
        if name not in seen:
            seen.add(name)
            items.append({"name": name, "quantity": quantity})
    return items[:max_items]
