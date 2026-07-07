"""Unit tests for the explainable health score."""

import datetime as dt
from dataclasses import dataclass, field


from nutrimind.services.health_score_service import compute_health_score

TODAY = dt.date(2026, 7, 6)


@dataclass
class FakeMeal:
    ts: dt.datetime
    calories: float
    protein_g: float
    fat_g: float
    carbs_g: float
    quality_score: int | None = None
    items: list = field(default_factory=list)


@dataclass
class FakeWater:
    date: dt.date
    glasses: int


def _good_week():
    """Seven on-target days: ~2000 kcal, balanced macros, varied foods, 8 glasses."""
    meals, water = [], []
    for offset in range(7):
        day = TODAY - dt.timedelta(days=offset)
        for meal_index in range(2):
            meals.append(FakeMeal(
                ts=dt.datetime.combine(day, dt.time(9 + meal_index * 6)),
                calories=1000, protein_g=50, fat_g=31, carbs_g=130,
                quality_score=90,
                items=[{"name": f"food-{offset}-{meal_index}-{n}"} for n in range(2)],
            ))
        water.append(FakeWater(date=day, glasses=8))
    return meals, water


def test_perfect_week_scores_high():
    meals, water = _good_week()
    score = compute_health_score(meals, water, calorie_target=2000, today=TODAY)
    assert score.total >= 90


def test_no_data_scores_low_with_explanations():
    score = compute_health_score([], [], calorie_target=2000, today=TODAY)
    assert score.total == 0
    assert all("log" in c.detail.lower() or "no" in c.detail.lower()
               for c in score.components)


def test_component_maxima_sum_to_100():
    score = compute_health_score([], [], calorie_target=2000, today=TODAY)
    assert sum(c.max_points for c in score.components) == 100


def test_reproducible():
    meals, water = _good_week()
    a = compute_health_score(meals, water, calorie_target=2000, today=TODAY)
    b = compute_health_score(meals, water, calorie_target=2000, today=TODAY)
    assert a.to_dict() == b.to_dict()


def test_poor_calorie_adherence_lowers_score():
    meals, water = _good_week()
    good = compute_health_score(meals, water, calorie_target=2000, today=TODAY)
    bad = compute_health_score(meals, water, calorie_target=3500, today=TODAY)
    assert bad.total < good.total


def test_old_logs_outside_window_ignored():
    old_meal = FakeMeal(ts=dt.datetime.combine(TODAY - dt.timedelta(days=30), dt.time(9)),
                        calories=2000, protein_g=50, fat_g=30, carbs_g=100)
    score = compute_health_score([old_meal], [], calorie_target=2000, today=TODAY)
    assert score.total == 0


def test_total_clamped_between_0_and_100():
    meals, water = _good_week()
    score = compute_health_score(meals, water, calorie_target=2000, today=TODAY)
    assert 0 <= score.total <= 100
