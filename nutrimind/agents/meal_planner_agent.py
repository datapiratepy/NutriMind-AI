"""Meal Planner Agent — deterministic targets, LLM composition, validated JSON.

The LLM never computes numbers: targets come from ``calculate_targets``
(Mifflin-St Jeor). Granite composes a day plan as strict JSON, validated and
retried once with error feedback; the accepted plan is persisted as a
``MealPlan`` and rendered as readable markdown for the chat.
"""

from __future__ import annotations

import time
from typing import Iterator

from nutrimind.agents.base_agent import AgentRequest, BaseAgent, Event, status, token
from nutrimind.agents.tools import parse_llm_json
from nutrimind.exceptions import NutriMindError

_REQUIRED_MEALS = ("Breakfast", "Lunch", "Snack", "Dinner")


def _validate_plan(data: dict) -> dict:
    """Structural validation of the LLM's plan JSON; raises ValueError."""
    meals = data.get("meals")
    if not isinstance(meals, list) or len(meals) != len(_REQUIRED_MEALS):
        raise ValueError(f"'meals' must list exactly {_REQUIRED_MEALS}")
    cleaned_meals = []
    # strict=True: the length check above already guarantees these match, so a
    # mismatch here would mean that check regressed rather than a bad model reply.
    for expected, meal in zip(_REQUIRED_MEALS, meals, strict=True):
        if str(meal.get("name", "")).lower() != expected.lower():
            raise ValueError(f"meal order must be {_REQUIRED_MEALS}")
        items = meal.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"{expected} has no items")
        cleaned_meals.append({
            "name": expected,
            "items": [{"food": str(i.get("food", "")).strip(),
                       "portion": str(i.get("portion", "")).strip()}
                      for i in items if str(i.get("food", "")).strip()],
            "calories": int(float(meal.get("calories", 0))),
            "protein_g": round(float(meal.get("protein_g", 0)), 1),
        })
    return {
        "title": str(data.get("title") or "Personalized day plan").strip()[:120],
        "meals": cleaned_meals,
        "hydration": str(data.get("hydration", "")).strip(),
        "notes": str(data.get("notes", "")).strip(),
    }


def _render_markdown(plan: dict, targets) -> str:
    """Readable chat rendering of the validated plan."""
    lines = [f"**{plan['title']}**",
             f"_Daily targets: {targets.calories} kcal · protein "
             f"{targets.protein_g} g · fat {targets.fat_g} g · carbs "
             f"{targets.carbs_g} g · fiber {targets.fiber_g} g · water "
             f"{targets.water_l} L_", ""]
    for meal in plan["meals"]:
        items = ", ".join(f"{i['portion']} {i['food']}".strip() for i in meal["items"])
        lines.append(f"**{meal['name']}** — {items}")
        lines.append(f"  ≈ {meal['calories']} kcal · {meal['protein_g']} g protein")
    if plan["hydration"]:
        lines.append(f"\n**Hydration** — {plan['hydration']}")
    if plan["notes"]:
        lines.append(f"\n_{plan['notes']}_")
    lines.append("\n*(Per-meal numbers are the model's estimates; daily targets "
                 "are computed deterministically. Plan saved to your meal plans.)*")
    return "\n".join(lines)


class MealPlannerAgent(BaseAgent):
    """Generates and persists a one-day plan honoring deterministic targets."""

    name = "meal_planner"
    prompt_name = "meal_planner"

    def run(self, request: AgentRequest) -> Iterator[Event]:
        started = time.perf_counter()
        if request.profile is None:
            text = ("I need your profile before planning meals — please fill it "
                    "in on the Profile page (age, height, weight, activity, "
                    "diet preference), then ask me again.")
            yield token(text)
            yield self._final(request, text, started=started,
                              response_source="deterministic")
            return

        yield status("Computing your daily targets…")
        targets = request.toolbox.calculate_targets(request.profile)

        yield status("Composing a plan that fits your targets…")
        user_content = (
            f"{self._profile_context(request)}\n"
            f"Daily targets (deterministic, do not change): "
            f"calories={targets.calories}, protein_g={targets.protein_g}, "
            f"fat_g={targets.fat_g}, carbs_g={targets.carbs_g}, "
            f"fiber_g={targets.fiber_g}, water_l={targets.water_l}.\n"
            f"User request: {request.message}\n"
            "Return ONLY valid JSON per your output format."
        )
        messages = [{"role": "system", "content": self.system_prompt()},
                    {"role": "user", "content": user_content}]

        plan = None
        for attempt in (1, 2):
            result = request.llm.chat(messages, max_tokens=900, temperature=0.5)
            try:
                plan = _validate_plan(parse_llm_json(result.text))
                break
            except (ValueError, TypeError) as exc:
                if attempt == 2:
                    raise NutriMindError(
                        "The meal plan could not be generated in a valid format.",
                        hint="Please try again — this occasionally happens.",
                    ) from exc
                yield status("Plan format was invalid — asking the model to fix it…")
                messages.append({"role": "assistant", "content": result.text})
                messages.append({"role": "user",
                                 "content": f"Invalid: {exc}. Return ONLY the "
                                            "corrected JSON object."})

        from nutrimind.extensions import db
        from nutrimind.models import MealPlan

        # Owner comes from the toolbox, which the route built from the session —
        # never from anything the model produced.
        row = MealPlan(user_id=request.toolbox.user_id, title=plan["title"],
                       targets=targets.to_dict(), plan=plan)
        db.session.add(row)
        db.session.commit()

        text = _render_markdown(plan, targets)
        yield token(text)  # plan is rendered at once (composed, not streamed)
        yield self._final(
            request, text, started=started,
            response_source="deterministic_targets_plus_llm_composition",
            extra={"plan_id": row.id, "targets": targets.to_dict()},
        )
