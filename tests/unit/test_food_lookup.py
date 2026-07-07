"""Unit tests for the curated food table lookup."""

import pytest

from nutrimind.exceptions import ValidationError
from nutrimind.services.nutrition_service import get_food_table


@pytest.fixture(scope="module")
def table():
    return get_food_table()


def test_table_loads_curated_foods(table):
    assert len(table) >= 80


def test_exact_and_alias_search(table):
    assert table.search("roti")[0].name == "roti"
    assert table.search("chapati")[0].name == "roti"       # alias
    assert table.search("dahi")[0].name == "curd"          # hindi alias
    assert table.search("PANEER")[0].name == "paneer"      # case-insensitive


def test_substring_search(table):
    names = [f.name for f in table.search("biryani")]
    assert "biryani chicken" in names and "biryani veg" in names


def test_empty_query_rejected(table):
    with pytest.raises(ValidationError):
        table.search("   ")


def test_serving_scaling(table):
    roti = table.search("roti")[0]
    two = table.compute(roti, quantity=2)
    assert two["calories"] == pytest.approx(roti.calories * 2)
    assert two["grams"] == pytest.approx(roti.serving_g * 2)


def test_gram_scaling(table):
    paneer = table.search("paneer")[0]                      # 50 g serving
    hundred = table.compute(paneer, grams=100)
    assert hundred["protein_g"] == pytest.approx(paneer.protein_g * 2, abs=0.1)


def test_estimate_meal_totals_and_unmatched(table):
    result = table.estimate_meal([
        {"name": "roti", "quantity": 2},
        {"name": "dal", "quantity": 1},
        {"name": "unicorn stew", "quantity": 1},
    ])
    assert result["unmatched"] == ["unicorn stew"]
    assert len(result["items"]) == 2
    expected = sum(i["calories"] for i in result["items"])
    assert result["totals"]["calories"] == pytest.approx(expected, abs=0.2)
    assert result["totals"]["iron_mg"] > 0                  # micros aggregate too


def test_micronutrients_present_where_curated(table):
    guava = table.search("guava")[0]
    assert guava.vitamin_c_mg > 100
