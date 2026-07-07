"""Unit tests for the deterministic BMI service."""

import pytest

from nutrimind.exceptions import ValidationError
from nutrimind.services import bmi_service


def test_known_bmi_value():
    assert bmi_service.compute_bmi(175, 70) == 22.9


def test_category_boundaries():
    assert bmi_service.categorize(18.4) == "underweight"
    assert bmi_service.categorize(18.5) == "normal"
    assert bmi_service.categorize(24.9) == "normal"
    assert bmi_service.categorize(25.0) == "overweight"
    assert bmi_service.categorize(29.9) == "overweight"
    assert bmi_service.categorize(30.0) == "obese"


def test_ideal_weight_range():
    low, high = bmi_service.ideal_weight_range(175)
    assert low == pytest.approx(56.7, abs=0.1)
    assert high == pytest.approx(76.3, abs=0.1)
    assert low < high


def test_assess_returns_suggestions_and_consistent_fields():
    result = bmi_service.assess(175, 95)
    assert result.category == "obese"
    assert result.bmi == 31.0
    assert len(result.suggestions) >= 2
    assert result.ideal_weight_min_kg < 95


@pytest.mark.parametrize("height,weight", [(400, 70), (175, 10), (0, 0), ("abc", 70)])
def test_assess_rejects_invalid_input(height, weight):
    with pytest.raises(ValidationError):
        bmi_service.assess(height, weight)


def test_reproducibility():
    assert bmi_service.assess(160, 55).to_dict() == bmi_service.assess(160, 55).to_dict()
