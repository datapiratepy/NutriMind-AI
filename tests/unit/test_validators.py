"""Unit tests for server-side validation."""

import pytest

from nutrimind.exceptions import ValidationError
from nutrimind.utils import validators


def _valid_profile() -> dict:
    return {"name": "Harsh", "age": 21, "gender": "male", "height_cm": 175,
            "weight_kg": 70, "activity_level": "moderate",
            "food_preference": "vegetarian", "weight_goal": "maintain"}


def test_valid_profile_passes_and_normalizes():
    cleaned = validators.validate_profile_payload({**_valid_profile(),
                                                   "gender": "MALE",
                                                   "allergies": [" peanuts "]})
    assert cleaned["gender"] == "male"
    assert cleaned["allergies"] == ["peanuts"]
    assert isinstance(cleaned["age"], int)


@pytest.mark.parametrize("field,value", [
    ("age", 0), ("age", 150), ("height_cm", 20), ("weight_kg", 999),
    ("gender", "unknown"), ("activity_level", "couch"),
    ("food_preference", "carnivore"), ("weight_goal", "bulk"),
    ("daily_calorie_goal", 200), ("name", ""),
])
def test_invalid_profile_fields_rejected(field, value):
    with pytest.raises(ValidationError) as exc_info:
        validators.validate_profile_payload({**_valid_profile(), field: value})
    assert field in str(exc_info.value)


def test_missing_required_field_rejected():
    payload = _valid_profile()
    del payload["age"]
    with pytest.raises(ValidationError, match="age"):
        validators.validate_profile_payload(payload)


def test_partial_mode_allows_missing_fields():
    cleaned = validators.validate_profile_payload({"weight_kg": 72}, partial=True)
    assert cleaned == {"weight_kg": 72.0}


def test_control_characters_stripped():
    cleaned = validators.validate_profile_payload(
        {**_valid_profile(), "name": "Ha\x00rsh\x1f"})
    assert cleaned["name"] == "Harsh"


def test_meal_items_validation():
    items = validators.validate_meal_items([{"name": "roti", "quantity": 2}])
    assert items[0]["quantity"] == 2.0
    with pytest.raises(ValidationError):
        validators.validate_meal_items([])
    with pytest.raises(ValidationError):
        validators.validate_meal_items([{"quantity": 2}])       # no name
    with pytest.raises(ValidationError):
        validators.validate_meal_items([{"name": "x", "quantity": 500}])


def test_meal_type_default_and_rejection():
    assert validators.validate_meal_type(None) == "other"
    assert validators.validate_meal_type("Lunch") == "lunch"
    with pytest.raises(ValidationError):
        validators.validate_meal_type("brunch")
