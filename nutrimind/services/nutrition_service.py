"""Nutrition service: deterministic targets + curated food-composition lookup.

Two responsibilities (FOLDER_STRUCTURE.md §2):

1. **Targets** — BMR (Mifflin-St Jeor), TDEE, calorie/macro/fiber/water
   targets. Pure arithmetic; the LLM never computes numbers (ADR #7).
2. **Food lookup** — search and serving math over the curated composition
   table in ``nutrimind/data/food_composition.csv`` (values are approximate,
   compiled from standard food-composition references such as IFCT/USDA).
"""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from nutrimind.exceptions import ValidationError
from nutrimind.utils.validators import (
    ACTIVITY_LEVELS,
    AGE_RANGE,
    CALORIE_GOAL_RANGE,
    GENDERS,
    HEIGHT_CM_RANGE,
    WEIGHT_GOALS,
    WEIGHT_KG_RANGE,
    validate_choice,
    validate_range,
)

# ---------------------------------------------------------------------------
# Targets (BMR / TDEE / macros / water)
# ---------------------------------------------------------------------------

ACTIVITY_FACTORS: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}

#: Daily calorie adjustment by goal (kcal). Conservative, sustainable values.
GOAL_CALORIE_ADJUST: dict[str, int] = {"lose": -450, "maintain": 0, "gain": 400}

#: Protein target by goal (g per kg body weight).
PROTEIN_G_PER_KG: dict[str, float] = {"lose": 1.6, "maintain": 1.2, "gain": 1.8}

_FAT_CALORIE_FRACTION = 0.27      # fat supplies ~27% of calories
_FIBER_G_PER_1000_KCAL = 14.0     # dietary guideline heuristic
_WATER_ML_PER_KG = 35.0
_MIN_CALORIES = 1200              # safety floor for generated targets
_MIN_CARBS_G = 50.0               # physiological floor; fat absorbs the remainder
_WATER_RANGE_L = (1.5, 4.5)


@dataclass(frozen=True)
class Targets:
    """Daily nutrition targets derived from the profile."""

    calories: int
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float
    water_l: float

    def to_dict(self) -> dict:
        return {
            "calories": self.calories,
            "protein_g": self.protein_g,
            "fat_g": self.fat_g,
            "carbs_g": self.carbs_g,
            "fiber_g": self.fiber_g,
            "water_l": self.water_l,
        }


def bmr_mifflin_st_jeor(weight_kg: float, height_cm: float, age: int, gender: str) -> float:
    """Basal metabolic rate (kcal/day) via Mifflin-St Jeor.

    For gender 'other' the male/female averages are used — a documented,
    common convention when a binary equation must be applied.
    """
    weight = validate_range(weight_kg, "weight_kg", *WEIGHT_KG_RANGE)
    height = validate_range(height_cm, "height_cm", *HEIGHT_CM_RANGE)
    age_v = validate_range(age, "age", *AGE_RANGE)
    gender_v = validate_choice(gender, "gender", GENDERS)

    base = 10.0 * weight + 6.25 * height - 5.0 * age_v
    if gender_v == "male":
        return base + 5.0
    if gender_v == "female":
        return base - 161.0
    return base + (5.0 - 161.0) / 2.0


def tdee(bmr: float, activity_level: str) -> float:
    """Total daily energy expenditure = BMR x activity factor."""
    level = validate_choice(activity_level, "activity_level", ACTIVITY_LEVELS)
    return bmr * ACTIVITY_FACTORS[level]


def calculate_targets(
    *,
    weight_kg: float,
    height_cm: float,
    age: int,
    gender: str,
    activity_level: str,
    weight_goal: str = "maintain",
    daily_calorie_goal: int | None = None,
) -> Targets:
    """Derive the full daily target set from profile attributes.

    An explicit ``daily_calorie_goal`` (validated) overrides the computed
    calories; macros are then derived from the chosen calorie total.
    """
    goal = validate_choice(weight_goal, "weight_goal", WEIGHT_GOALS)

    if daily_calorie_goal is not None:
        calories = int(validate_range(daily_calorie_goal, "daily_calorie_goal",
                                      *CALORIE_GOAL_RANGE))
    else:
        expenditure = tdee(bmr_mifflin_st_jeor(weight_kg, height_cm, age, gender),
                           activity_level)
        calories = max(_MIN_CALORIES, round(expenditure + GOAL_CALORIE_ADJUST[goal]))

    protein_g = round(weight_kg * PROTEIN_G_PER_KG[goal], 1)
    fat_g = round(calories * _FAT_CALORIE_FRACTION / 9.0, 1)
    carbs_g = round((calories - protein_g * 4.0 - fat_g * 9.0) / 4.0, 1)
    if carbs_g < _MIN_CARBS_G:
        # Keep a physiological carb floor; let fat absorb the difference.
        carbs_g = _MIN_CARBS_G
        fat_g = round(max(0.0, (calories - protein_g * 4.0 - carbs_g * 4.0)) / 9.0, 1)

    fiber_g = round(_FIBER_G_PER_1000_KCAL * calories / 1000.0, 1)
    water_l = round(min(_WATER_RANGE_L[1],
                        max(_WATER_RANGE_L[0], weight_kg * _WATER_ML_PER_KG / 1000.0)), 1)
    return Targets(calories=calories, protein_g=protein_g, fat_g=fat_g,
                   carbs_g=carbs_g, fiber_g=fiber_g, water_l=water_l)


def targets_for_profile(profile) -> Targets:
    """Convenience wrapper accepting a ``UserProfile``-shaped object."""
    return calculate_targets(
        weight_kg=profile.weight_kg,
        height_cm=profile.height_cm,
        age=profile.age,
        gender=profile.gender,
        activity_level=profile.activity_level,
        weight_goal=profile.weight_goal,
        daily_calorie_goal=profile.daily_calorie_goal,
    )


# ---------------------------------------------------------------------------
# Food lookup (curated composition table)
# ---------------------------------------------------------------------------

_NUTRIENT_FIELDS = ("calories", "protein_g", "fat_g", "carbs_g", "fiber_g",
                    "iron_mg", "calcium_mg", "vitamin_c_mg")


@dataclass(frozen=True)
class Food:
    """One row of the curated composition table (values per serving)."""

    name: str
    serving_desc: str
    serving_g: float
    calories: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float
    iron_mg: float
    calcium_mg: float
    vitamin_c_mg: float
    category: str
    cuisine: str
    aliases: tuple[str, ...]

    def scaled(self, factor: float) -> dict:
        """Nutrients multiplied by ``factor`` servings, rounded for display."""
        return {f: round(getattr(self, f) * factor, 1) for f in _NUTRIENT_FIELDS}


def _normalize(text: str) -> str:
    """Lowercase, accent-fold, collapse whitespace — tolerant matching."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(folded.lower().split())


class FoodTable:
    """In-memory, read-only lookup over the curated food composition CSV."""

    def __init__(self, csv_path: Path) -> None:
        self._foods: list[Food] = []
        self._by_name: dict[str, Food] = {}
        with csv_path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                food = Food(
                    name=row["food"].strip(),
                    serving_desc=row["serving_desc"].strip(),
                    serving_g=float(row["serving_g"]),
                    calories=float(row["calories"]),
                    protein_g=float(row["protein_g"]),
                    fat_g=float(row["fat_g"]),
                    carbs_g=float(row["carbs_g"]),
                    fiber_g=float(row["fiber_g"]),
                    iron_mg=float(row.get("iron_mg") or 0),
                    calcium_mg=float(row.get("calcium_mg") or 0),
                    vitamin_c_mg=float(row.get("vitamin_c_mg") or 0),
                    category=row.get("category", "").strip(),
                    cuisine=row.get("cuisine", "").strip(),
                    aliases=tuple(a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()),
                )
                self._foods.append(food)
                self._by_name[_normalize(food.name)] = food
                for alias in food.aliases:
                    self._by_name.setdefault(_normalize(alias), food)

    @property
    def foods(self) -> tuple[Food, ...]:
        """Read-only view of all foods (used by the agents' naive extractor)."""
        return tuple(self._foods)

    def __len__(self) -> int:
        return len(self._foods)

    def search(self, query: str, *, limit: int = 8) -> list[Food]:
        """Ranked search: exact name/alias, then prefix, then substring."""
        needle = _normalize(query)
        if not needle:
            raise ValidationError("Search query must not be empty.")
        exact = self._by_name.get(needle)
        results: list[Food] = [exact] if exact else []
        for tier in ("prefix", "substring"):
            for food in self._foods:
                if food in results:
                    continue
                names = (food.name, *food.aliases)
                normalized = [_normalize(n) for n in names]
                if tier == "prefix" and any(n.startswith(needle) for n in normalized):
                    results.append(food)
                elif tier == "substring" and any(needle in n for n in normalized):
                    results.append(food)
                if len(results) >= limit:
                    return results
        return results

    def compute(self, food: Food, *, quantity: float = 1.0, grams: float | None = None) -> dict:
        """Nutrients for ``quantity`` servings, or an explicit gram amount."""
        factor = (grams / food.serving_g) if grams is not None else quantity
        nutrients = food.scaled(factor)
        return {
            "food": food.name,
            "serving_desc": food.serving_desc,
            "quantity": round(factor, 2),
            "grams": round(food.serving_g * factor, 1),
            **nutrients,
        }

    def estimate_meal(self, items: Sequence[dict]) -> dict:
        """Deterministic totals for structured items.

        :param items: validated ``[{"name": str, "quantity"?: float, "grams"?: float}]``.
        :returns: ``{"items": [...], "totals": {...}, "unmatched": [...]}`` —
            the LLM interprets these numbers, never invents them.
        """
        matched: list[dict] = []
        unmatched: list[str] = []
        totals = dict.fromkeys(_NUTRIENT_FIELDS, 0.0)
        for item in items:
            hits = self.search(item["name"], limit=1)
            if not hits:
                unmatched.append(item["name"])
                continue
            entry = self.compute(hits[0], quantity=item.get("quantity", 1.0),
                                 grams=item.get("grams"))
            matched.append(entry)
            for f in _NUTRIENT_FIELDS:
                totals[f] += entry[f]
        return {
            "items": matched,
            "totals": {f: round(v, 1) for f, v in totals.items()},
            "unmatched": unmatched,
        }


@lru_cache(maxsize=1)
def get_food_table() -> FoodTable:
    """Process-wide singleton over the packaged CSV."""
    csv_path = Path(__file__).resolve().parent.parent / "data" / "food_composition.csv"
    return FoodTable(csv_path)
