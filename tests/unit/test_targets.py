"""Unit tests for BMR, TDEE and nutrition targets (Mifflin-St Jeor)."""

import pytest

from nutrimind.exceptions import ValidationError
from nutrimind.services.nutrition_service import (
    ACTIVITY_FACTORS,
    bmr_mifflin_st_jeor,
    calculate_targets,
    tdee,
)


def test_bmr_male_reference_value():
    # 10*70 + 6.25*175 - 5*30 + 5 = 1648.75
    assert bmr_mifflin_st_jeor(70, 175, 30, "male") == pytest.approx(1648.75)


def test_bmr_female_reference_value():
    assert bmr_mifflin_st_jeor(70, 175, 30, "female") == pytest.approx(1482.75)


def test_bmr_other_is_between_male_and_female():
    other = bmr_mifflin_st_jeor(70, 175, 30, "other")
    assert 1482.75 < other < 1648.75


def test_tdee_applies_activity_factor():
    bmr = 1648.75
    assert tdee(bmr, "moderate") == pytest.approx(bmr * ACTIVITY_FACTORS["moderate"])


def test_targets_maintain_goal():
    targets = calculate_targets(weight_kg=70, height_cm=175, age=30, gender="male",
                                activity_level="moderate")
    assert targets.calories == round(1648.75 * 1.55)
    assert targets.protein_g == pytest.approx(84.0)         # 1.2 g/kg
    assert targets.fat_g == pytest.approx(targets.calories * 0.27 / 9, abs=0.1)
    # Macros must reconstruct the calorie total within rounding error.
    reconstructed = targets.protein_g * 4 + targets.fat_g * 9 + targets.carbs_g * 4
    assert reconstructed == pytest.approx(targets.calories, abs=10)
    assert 2.4 <= targets.water_l <= 2.5                    # 35 ml/kg -> 2.45 L rounded


def test_targets_lose_goal_reduces_calories():
    maintain = calculate_targets(weight_kg=70, height_cm=175, age=30, gender="male",
                                 activity_level="moderate")
    lose = calculate_targets(weight_kg=70, height_cm=175, age=30, gender="male",
                             activity_level="moderate", weight_goal="lose")
    assert lose.calories == maintain.calories - 450
    assert lose.protein_g > maintain.protein_g              # protects muscle


def test_explicit_calorie_goal_overrides():
    targets = calculate_targets(weight_kg=70, height_cm=175, age=30, gender="male",
                                activity_level="moderate", daily_calorie_goal=2000)
    assert targets.calories == 2000


def test_calorie_floor_for_small_sedentary_profile():
    targets = calculate_targets(weight_kg=42, height_cm=150, age=70, gender="female",
                                activity_level="sedentary", weight_goal="lose")
    assert targets.calories >= 1200
    assert targets.carbs_g >= 50


def test_invalid_inputs_rejected():
    with pytest.raises(ValidationError):
        calculate_targets(weight_kg=70, height_cm=175, age=30, gender="male",
                          activity_level="extreme")
    with pytest.raises(ValidationError):
        calculate_targets(weight_kg=70, height_cm=175, age=30, gender="male",
                          activity_level="moderate", daily_calorie_goal=100)
