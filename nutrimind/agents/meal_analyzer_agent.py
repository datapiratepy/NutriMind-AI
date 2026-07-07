"""Meal Analyzer Agent — LLM for language, Python for math (ADR #7).

Three steps: (1) extract food items from free text — Granite in live mode,
the deterministic alias scanner in demo mode or as fallback; (2) price the
items through the curated food table (never the LLM); (3) have the LLM write
a qualitative assessment of the computed numbers. The meal is logged so the
dashboard and health score pick it up.
"""

from __future__ import annotations

import time
from typing import Iterator

from nutrimind.agents.base_agent import AgentRequest, BaseAgent, Event, status, token
from nutrimind.agents.tools import naive_extract_items, parse_llm_json

#: Deterministic meal quality from macro shares (0-100) — never the LLM.
_QUALITY_BANDS = {"protein": (10.0, 35.0), "fat": (20.0, 35.0), "carbs": (45.0, 65.0)}


def _quality_score(totals: dict) -> int | None:
    calories = totals["protein_g"] * 4 + totals["fat_g"] * 9 + totals["carbs_g"] * 4
    if calories <= 0:
        return None
    shares = {"protein": totals["protein_g"] * 4 / calories * 100,
              "fat": totals["fat_g"] * 9 / calories * 100,
              "carbs": totals["carbs_g"] * 4 / calories * 100}
    score = 0.0
    for macro, (low, high) in _QUALITY_BANDS.items():
        value = shares[macro]
        if low <= value <= high:
            score += 100 / 3
        else:
            distance = (low - value) if value < low else (value - high)
            score += max(0.0, (100 / 3) * (1 - distance / (high - low)))
    if totals.get("fiber_g", 0) >= 5:
        score = min(100.0, score + 8)  # fiber bonus
    return round(score)


def _render_table(estimate: dict) -> str:
    lines = ["| Food | Qty | kcal | Protein | Fat | Carbs | Fiber |",
             "|---|---|---|---|---|---|---|"]
    for item in estimate["items"]:
        lines.append(f"| {item['food']} | {item['quantity']:g} | "
                     f"{item['calories']:g} | {item['protein_g']:g} g | "
                     f"{item['fat_g']:g} g | {item['carbs_g']:g} g | "
                     f"{item['fiber_g']:g} g |")
    totals = estimate["totals"]
    lines.append(f"| **Total** |  | **{totals['calories']:g}** | "
                 f"**{totals['protein_g']:g} g** | **{totals['fat_g']:g} g** | "
                 f"**{totals['carbs_g']:g} g** | **{totals['fiber_g']:g} g** |")
    return "\n".join(lines)


class MealAnalyzerAgent(BaseAgent):
    """Estimates and assesses a described meal; logs it for the dashboard."""

    name = "meal_analyzer"
    prompt_name = "meal_analyzer"

    def _extract_items(self, request: AgentRequest) -> tuple[list[dict], str]:
        """Return (items, method). Live: Granite JSON; demo/failure: scanner."""
        if request.llm.mode != "demo":
            try:
                result = request.llm.chat(
                    [{"role": "system", "content": self.system_prompt()},
                     {"role": "user",
                      "content": "Extract the food items. Meal description:\n"
                                 + request.message}],
                    max_tokens=200, temperature=0.0)
                items = parse_llm_json(result.text).get("items", [])
                cleaned = [{"name": str(i["name"]).strip().lower(),
                            "quantity": float(i.get("quantity", 1))}
                           for i in items if str(i.get("name", "")).strip()]
                if cleaned:
                    return cleaned, "granite_extraction"
            except Exception:  # noqa: BLE001 — deterministic fallback below
                pass
        return naive_extract_items(request.message), "deterministic_scanner"

    def run(self, request: AgentRequest) -> Iterator[Event]:
        started = time.perf_counter()
        yield status("Identifying the foods you mentioned…")
        items, extraction_method = self._extract_items(request)

        if not items:
            text = ("I couldn't identify any foods I know in that description. "
                    "Try naming them plainly, e.g. \"2 rotis, dal, paneer and "
                    "an apple\" — or search the food database on the Analyzer page.")
            yield token(text)
            yield self._final(request, text, started=started,
                              response_source="deterministic",
                              extra={"extraction": extraction_method})
            return

        yield status(f"Computing nutrition for {len(items)} item(s) from the "
                     "food table…")
        estimate = request.toolbox.lookup_foods(items)
        quality = _quality_score(estimate["totals"])

        table = _render_table(estimate)
        yield token(table + "\n\n")

        yield status("Writing the assessment…")
        totals = estimate["totals"]
        assessment_prompt = (
            f"{self._profile_context(request)}\n"
            "Computed nutrition for the user's meal (deterministic, do not "
            f"change): calories={totals['calories']}, protein_g={totals['protein_g']}, "
            f"fat_g={totals['fat_g']}, carbs_g={totals['carbs_g']}, "
            f"fiber_g={totals['fiber_g']}. Quality score {quality}/100.\n"
            "Write one short paragraph assessing the meal quality using only "
            "these numbers, then 2-3 practical suggestions."
        )
        collected: list[str] = []
        yield from self._stream_llm(
            request,
            [{"role": "system", "content": self.system_prompt()},
             {"role": "user", "content": assessment_prompt}],
            collected, max_tokens=300, temperature=0.4)
        assessment = "".join(collected)

        request.toolbox.log_meal(meal_type="other", raw_text=request.message,
                                 estimate=estimate, quality_score=quality,
                                 suggestions=assessment[:1000])

        footer = ""
        if estimate["unmatched"]:
            footer = ("\n\n*Not in my food table (not counted): "
                      + ", ".join(estimate["unmatched"]) + ".*")
            yield token(footer)

        text = table + "\n\n" + assessment + footer
        yield self._final(
            request, text, started=started,
            response_source="deterministic_math_plus_llm_assessment",
            extra={"extraction": extraction_method,
                   "totals": estimate["totals"],
                   "quality_score": quality,
                   "unmatched": estimate["unmatched"]},
        )
