"""Explainable 0-100 health score (ARCHITECTURE.md §5) — never LLM-generated.

Weighted last-7-day blend:

    calorie-target adherence  30
    macro balance             25
    meal quality & variety    20
    water intake              15
    logging consistency       10

Every component reports its own points and a plain-language detail string so
the dashboard can show *why* the score is what it is. Pure functions over
plain data (duck-typed attribute access) — fully testable without a database.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

#: Acceptable Macronutrient Distribution Ranges (% of calories).
_MACRO_BANDS = {"protein": (10.0, 35.0), "fat": (20.0, 35.0), "carbs": (45.0, 65.0)}
_WATER_TARGET_GLASSES = 8
_VARIETY_FULL_CREDIT = 15  # distinct foods/week for full variety points


@dataclass(frozen=True)
class ScoreComponent:
    """One explainable slice of the health score."""

    name: str
    points: float
    max_points: float
    detail: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "points": round(self.points, 1),
            "max_points": self.max_points,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HealthScore:
    total: int
    components: tuple[ScoreComponent, ...]

    def to_dict(self) -> dict:
        return {"total": self.total,
                "components": [c.to_dict() for c in self.components]}


def _band_score(value: float, low: float, high: float, max_points: float) -> float:
    """Full points inside [low, high]; linear falloff reaching 0 at 2x distance."""
    if low <= value <= high:
        return max_points
    distance = (low - value) if value < low else (value - high)
    span = (high - low) or 1.0
    return max(0.0, max_points * (1.0 - distance / span))


def _calorie_adherence(daily_calories: dict, target: float) -> ScoreComponent:
    max_points = 30.0
    if not daily_calories or target <= 0:
        return ScoreComponent("Calorie adherence", 0.0, max_points,
                              "No meals logged yet — log meals to earn these points.")
    deviations = [abs(total - target) / target for total in daily_calories.values()]
    mean_deviation = sum(deviations) / len(deviations)
    # <=10% average deviation = full credit; >=50% = zero, linear between.
    points = max_points * max(0.0, min(1.0, (0.50 - mean_deviation) / 0.40))
    return ScoreComponent("Calorie adherence", points, max_points,
                          f"Average daily deviation from your {target:.0f} kcal target: "
                          f"{mean_deviation * 100:.0f}%.")


def _macro_balance(totals: dict) -> ScoreComponent:
    max_points = 25.0
    calories = totals["protein_g"] * 4 + totals["fat_g"] * 9 + totals["carbs_g"] * 4
    if calories <= 0:
        return ScoreComponent("Macro balance", 0.0, max_points,
                              "No macro data yet — log meals to earn these points.")
    shares = {
        "protein": totals["protein_g"] * 4 / calories * 100,
        "fat": totals["fat_g"] * 9 / calories * 100,
        "carbs": totals["carbs_g"] * 4 / calories * 100,
    }
    per_macro = max_points / len(_MACRO_BANDS)
    points = sum(_band_score(shares[m], *_MACRO_BANDS[m], per_macro) for m in _MACRO_BANDS)
    detail = ", ".join(f"{m} {shares[m]:.0f}%" for m in ("protein", "fat", "carbs"))
    return ScoreComponent("Macro balance", points, max_points,
                          f"Calorie split — {detail} (healthy bands: P10-35 F20-35 C45-65).")


def _quality_and_variety(meals: Sequence, distinct_foods: int) -> ScoreComponent:
    max_points = 20.0
    if not meals:
        return ScoreComponent("Meal quality & variety", 0.0, max_points,
                              "No meals logged yet — log meals to earn these points.")
    quality_scores = [m.quality_score for m in meals if getattr(m, "quality_score", None)]
    quality_points = (sum(quality_scores) / len(quality_scores) / 100 * 10) if quality_scores else 5.0
    variety_points = min(1.0, distinct_foods / _VARIETY_FULL_CREDIT) * 10
    return ScoreComponent("Meal quality & variety", quality_points + variety_points, max_points,
                          f"{distinct_foods} distinct foods this week"
                          + (f", average meal quality {sum(quality_scores) / len(quality_scores):.0f}/100"
                             if quality_scores else ", no quality ratings yet"))


def _water(water_logs: Sequence, days: int) -> ScoreComponent:
    max_points = 15.0
    if not water_logs:
        return ScoreComponent("Water intake", 0.0, max_points,
                              "No water logged yet — track glasses to earn these points.")
    average = sum(w.glasses for w in water_logs) / days
    points = min(1.0, average / _WATER_TARGET_GLASSES) * max_points
    return ScoreComponent("Water intake", points, max_points,
                          f"Averaging {average:.1f} of {_WATER_TARGET_GLASSES} glasses/day.")


def _consistency(days_logged: int, days: int) -> ScoreComponent:
    max_points = 10.0
    points = days_logged / days * max_points
    return ScoreComponent("Logging consistency", points, max_points,
                          f"Meals logged on {days_logged} of the last {days} days.")


def compute_health_score(
    meal_logs: Iterable,
    water_logs: Iterable,
    calorie_target: float,
    *,
    days: int = 7,
    today: dt.date | None = None,
) -> HealthScore:
    """Compute the reproducible health score from raw logs.

    :param meal_logs: objects with ``ts``, ``calories``, ``protein_g``,
        ``fat_g``, ``carbs_g``, ``quality_score``, ``items`` attributes.
    :param water_logs: objects with ``date`` and ``glasses`` attributes.
    :param calorie_target: the profile's daily calorie target.
    :param today: injectable clock for deterministic tests.
    """
    reference_day = today or dt.date.today()
    window_start = reference_day - dt.timedelta(days=days - 1)

    meals = [m for m in meal_logs
             if window_start <= (m.ts.date() if isinstance(m.ts, dt.datetime) else m.ts) <= reference_day]
    water = [w for w in water_logs if window_start <= w.date <= reference_day]

    daily_calories: dict[dt.date, float] = defaultdict(float)
    totals = {"protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0}
    distinct_foods: set[str] = set()
    for meal in meals:
        day = meal.ts.date() if isinstance(meal.ts, dt.datetime) else meal.ts
        daily_calories[day] += meal.calories or 0.0
        for key in totals:
            totals[key] += getattr(meal, key, 0.0) or 0.0
        for item in (meal.items or []):
            name = item.get("name") if isinstance(item, dict) else str(item)
            if name:
                distinct_foods.add(name.lower())

    components = (
        _calorie_adherence(dict(daily_calories), calorie_target),
        _macro_balance(totals),
        _quality_and_variety(meals, len(distinct_foods)),
        _water(water, days),
        _consistency(len(daily_calories), days),
    )
    total = round(sum(c.points for c in components))
    return HealthScore(total=min(100, max(0, total)), components=components)
