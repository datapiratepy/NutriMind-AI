"""BMI service — deterministic, WHO-based, never LLM-computed."""

from __future__ import annotations

from dataclasses import dataclass, field

from nutrimind.utils.validators import HEIGHT_CM_RANGE, WEIGHT_KG_RANGE, validate_range

#: WHO adult BMI cut-offs: (upper_bound_exclusive, category).
_CATEGORIES = (
    (18.5, "underweight"),
    (25.0, "normal"),
    (30.0, "overweight"),
    (float("inf"), "obese"),
)

#: Healthy BMI band used for the ideal-weight range.
_HEALTHY_BMI_MIN = 18.5
_HEALTHY_BMI_MAX = 24.9

_SUGGESTIONS: dict[str, list[str]] = {
    "underweight": [
        "Aim for a moderate calorie surplus from nutrient-dense foods (nuts, dairy, legumes, whole grains).",
        "Include protein at every meal to support healthy weight gain.",
        "Consider discussing unexplained weight loss with a healthcare professional.",
    ],
    "normal": [
        "You're in the healthy range — focus on maintaining balanced meals and regular activity.",
        "Keep an eye on fiber, hydration, and micronutrients rather than calories alone.",
    ],
    "overweight": [
        "A modest calorie deficit (300-500 kcal/day) with adequate protein preserves muscle while losing fat.",
        "Prioritize whole foods and vegetables; small sustainable changes beat crash diets.",
        "Regular activity — even brisk walking — meaningfully improves outcomes.",
    ],
    "obese": [
        "Gradual, sustainable weight loss of about 0.5 kg/week is a safe target for most people.",
        "Focus on protein, fiber and portion awareness rather than eliminating food groups.",
        "Consider professional guidance (dietitian/physician), especially alongside other conditions.",
    ],
}


@dataclass(frozen=True)
class BMIResult:
    """Full BMI assessment for the API and dashboard."""

    bmi: float
    category: str
    ideal_weight_min_kg: float
    ideal_weight_max_kg: float
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "bmi": self.bmi,
            "category": self.category,
            "ideal_weight_min_kg": self.ideal_weight_min_kg,
            "ideal_weight_max_kg": self.ideal_weight_max_kg,
            "suggestions": list(self.suggestions),
        }


def compute_bmi(height_cm: float, weight_kg: float) -> float:
    """BMI = weight (kg) / height (m)^2, rounded to one decimal."""
    height_m = height_cm / 100.0
    return round(weight_kg / (height_m * height_m), 1)


def categorize(bmi: float) -> str:
    """WHO adult category for a BMI value."""
    for upper_bound, category in _CATEGORIES:
        if bmi < upper_bound:
            return category
    return "obese"  # pragma: no cover — unreachable, kept for safety


def ideal_weight_range(height_cm: float) -> tuple[float, float]:
    """Healthy weight band (kg) for a height, from the WHO BMI 18.5-24.9 band."""
    height_m = height_cm / 100.0
    low = _HEALTHY_BMI_MIN * height_m * height_m
    high = _HEALTHY_BMI_MAX * height_m * height_m
    return round(low, 1), round(high, 1)


def assess(height_cm: float, weight_kg: float) -> BMIResult:
    """Validate inputs and produce the full assessment.

    :raises nutrimind.exceptions.ValidationError: for out-of-range inputs.
    """
    height = validate_range(height_cm, "height_cm", *HEIGHT_CM_RANGE)
    weight = validate_range(weight_kg, "weight_kg", *WEIGHT_KG_RANGE)
    bmi = compute_bmi(height, weight)
    category = categorize(bmi)
    low, high = ideal_weight_range(height)
    return BMIResult(
        bmi=bmi,
        category=category,
        ideal_weight_min_kg=low,
        ideal_weight_max_kg=high,
        suggestions=list(_SUGGESTIONS[category]),
    )
