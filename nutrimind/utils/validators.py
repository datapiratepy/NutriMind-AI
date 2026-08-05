"""Server-side input validation for NutriMind AI.

All user-supplied values pass through here before touching services or the
database. Every rejection raises :class:`~nutrimind.exceptions.ValidationError`
with a message that names the field, the received value, and the allowed range.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from nutrimind.exceptions import ValidationError

# Physiological bounds (WHO-plausible extremes, deliberately generous).
HEIGHT_CM_RANGE = (50.0, 272.0)
WEIGHT_KG_RANGE = (20.0, 350.0)
AGE_RANGE = (1, 120)
CALORIE_GOAL_RANGE = (800, 6000)
WATER_GLASSES_RANGE = (0, 30)
QUANTITY_RANGE = (0.1, 50.0)
MEAL_TEXT_MAX_CHARS = 2000
NAME_MAX_CHARS = 80
LIST_ITEM_MAX_CHARS = 60
LIST_MAX_ITEMS = 15

GENDERS = ("male", "female", "other")
ACTIVITY_LEVELS = ("sedentary", "light", "moderate", "active", "very_active")
FOOD_PREFERENCES = ("vegetarian", "non_vegetarian", "vegan")
WEIGHT_GOALS = ("lose", "maintain", "gain")
MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack", "other")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(value: Any, *, max_chars: int, field: str) -> str:
    """Coerce to a trimmed string, strip control characters, enforce length."""
    if value is None:
        raise ValidationError(f"'{field}' is required.")
    text = _CONTROL_CHARS.sub("", str(value)).strip()
    if not text:
        raise ValidationError(f"'{field}' must not be empty.")
    if len(text) > max_chars:
        raise ValidationError(f"'{field}' is too long ({len(text)} chars, max {max_chars}).")
    return text


def parse_number(value: Any, field: str) -> float:
    """Coerce to float with a friendly error."""
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"'{field}' must be a number, got '{value}'.") from None


def validate_range(value: Any, field: str, low: float, high: float) -> float:
    """Numeric coercion + inclusive range check."""
    number = parse_number(value, field)
    if not (low <= number <= high):
        raise ValidationError(f"'{field}' must be between {low:g} and {high:g}, got {number:g}.")
    return number


def validate_choice(value: Any, field: str, choices: Iterable[str]) -> str:
    normalized = str(value or "").strip().lower()
    options = tuple(choices)
    if normalized not in options:
        raise ValidationError(f"'{field}' must be one of {options}, got '{value}'.")
    return normalized


def validate_string_list(value: Any, field: str) -> list[str]:
    """Validate an optional list of short strings (conditions, allergies)."""
    if value in (None, "", []):
        return []
    if not isinstance(value, list):
        raise ValidationError(f"'{field}' must be a list of strings.")
    if len(value) > LIST_MAX_ITEMS:
        raise ValidationError(f"'{field}' has too many items (max {LIST_MAX_ITEMS}).")
    return [sanitize_text(item, max_chars=LIST_ITEM_MAX_CHARS, field=f"{field} item")
            for item in value]


# ---------------------------------------------------------------------------
# Composite payload validators
# ---------------------------------------------------------------------------

#: field -> (validator callable taking the raw value, required flag)

def validate_timezone(value) -> str:
    """Accept only a real IANA zone name.

    Checked against the interpreter's own zone database rather than a regex:
    the value decides which calendar day every aggregate falls into, so a
    plausible-looking typo would silently shift a user's dashboard by hours.
    Rejecting at the boundary keeps ``local_today`` free of surprises — it still
    falls back to UTC defensively, but nothing valid should ever reach that path.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = sanitize_text(value, max_chars=64, field="timezone")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValidationError(
            f"'{name}' is not a known time zone.",
            hint="Use an IANA name such as 'Asia/Kolkata' or 'Europe/London'."
        ) from None
    return name

def validate_profile_payload(data: dict, *, partial: bool = False) -> dict:
    """Validate a profile create/update payload; returns cleaned values only.

    :param partial: when True, absent fields are skipped (PATCH semantics).
    :raises ValidationError: on the first invalid field.
    """
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object.")

    rules = {
        "name": lambda v: sanitize_text(v, max_chars=NAME_MAX_CHARS, field="name"),
        "age": lambda v: int(validate_range(v, "age", *AGE_RANGE)),
        "gender": lambda v: validate_choice(v, "gender", GENDERS),
        "height_cm": lambda v: validate_range(v, "height_cm", *HEIGHT_CM_RANGE),
        "weight_kg": lambda v: validate_range(v, "weight_kg", *WEIGHT_KG_RANGE),
        "activity_level": lambda v: validate_choice(v, "activity_level", ACTIVITY_LEVELS),
        "food_preference": lambda v: validate_choice(v, "food_preference", FOOD_PREFERENCES),
        "weight_goal": lambda v: validate_choice(v, "weight_goal", WEIGHT_GOALS),
        "medical_conditions": lambda v: validate_string_list(v, "medical_conditions"),
        "allergies": lambda v: validate_string_list(v, "allergies"),
        "country": lambda v: sanitize_text(v, max_chars=LIST_ITEM_MAX_CHARS, field="country"),
        "timezone": validate_timezone,
        "daily_calorie_goal": lambda v: int(validate_range(v, "daily_calorie_goal",
                                                           *CALORIE_GOAL_RANGE)),
    }
    required = ("name", "age", "gender", "height_cm", "weight_kg",
                "activity_level", "food_preference", "weight_goal")
    optional_nullable = ("country", "timezone", "daily_calorie_goal")

    cleaned: dict = {}
    for field, rule in rules.items():
        if field not in data or data.get(field) in (None, ""):
            if field in required and not partial:
                raise ValidationError(f"'{field}' is required.")
            if field in optional_nullable and field in data:
                cleaned[field] = None  # explicit clearing
            continue
        cleaned[field] = rule(data[field])
    return cleaned


def validate_meal_items(items: Any) -> list[dict]:
    """Validate structured meal items: [{"name": str, "quantity"?: num, "grams"?: num}]."""
    if not isinstance(items, list) or not items:
        raise ValidationError("'items' must be a non-empty list of foods.")
    if len(items) > 30:
        raise ValidationError("'items' has too many entries (max 30).")
    cleaned: list[dict] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(f"items[{index}] must be an object with a 'name'.")
        entry: dict = {"name": sanitize_text(item.get("name"), max_chars=LIST_ITEM_MAX_CHARS,
                                             field=f"items[{index}].name")}
        if item.get("grams") is not None:
            entry["grams"] = validate_range(item["grams"], f"items[{index}].grams", 1, 3000)
        else:
            entry["quantity"] = validate_range(item.get("quantity", 1),
                                               f"items[{index}].quantity", *QUANTITY_RANGE)
        cleaned.append(entry)
    return cleaned


def validate_meal_type(value: Any) -> str:
    return validate_choice(value or "other", "meal_type", MEAL_TYPES)
