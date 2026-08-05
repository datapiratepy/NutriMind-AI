"""Dashboard aggregation API.

Endpoint
    GET /api/dashboard/summary -> everything the dashboard needs in one call:
        profile presence, today's calories/macros/water, 7-day calorie series,
        targets, latest BMI, explainable health score, meal count.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from flask import Blueprint
from flask_login import login_required

from nutrimind.extensions import db
from nutrimind.models import BMIRecord, ChatMessage, MealLog, UserProfile, WaterLog
from nutrimind.routes import current_user_id, current_user_today, ok
from nutrimind.services.health_score_service import compute_health_score
from nutrimind.services.nutrition_service import targets_for_profile
from nutrimind.utils.time import utcnow

dashboard_api = Blueprint("dashboard_api", __name__, url_prefix="/api")


@dashboard_api.before_request
@login_required
def _require_login():
    """Default-deny: every endpoint in this blueprint needs a session.

    Applied at the blueprint rather than per route so that adding an
    endpoint cannot accidentally expose one account's data to another.
    """

_WINDOW_DAYS = 7


@dashboard_api.get("/dashboard/summary")
def summary():
    user_id = current_user_id()
    profile = UserProfile.for_user(user_id)
    # UTC date: all timestamps (MealLog.ts, WaterLog.date) are stored in UTC,
    # so "today" must be the UTC day or buckets misalign around midnight.
    today = current_user_today()
    meals = MealLog.since(_WINDOW_DAYS, user_id)
    water_rows = list(db.session.execute(
        db.select(WaterLog).where(
            WaterLog.user_id == user_id,
            WaterLog.date >= today - dt.timedelta(days=_WINDOW_DAYS - 1))
    ).scalars())
    latest_bmi = db.session.execute(
        db.select(BMIRecord).where(BMIRecord.user_id == user_id)
        .order_by(BMIRecord.ts.desc()).limit(1)
    ).scalar_one_or_none()

    targets = targets_for_profile(profile).to_dict() if profile else None

    # Today's totals + 7-day calorie series (zero-filled for chart continuity).
    daily = defaultdict(lambda: {"calories": 0.0, "protein_g": 0.0,
                                 "fat_g": 0.0, "carbs_g": 0.0, "fiber_g": 0.0})
    for meal in meals:
        bucket = daily[meal.ts.date()]
        for key in bucket:
            bucket[key] += getattr(meal, key) or 0.0
    water_by_day = {w.date: w.glasses for w in water_rows}
    series = []
    for offset in range(_WINDOW_DAYS - 1, -1, -1):
        day = today - dt.timedelta(days=offset)
        series.append({"date": day.isoformat(),
                       "calories": round(daily[day]["calories"], 1),
                       "water_glasses": water_by_day.get(day, 0)})

    water_today = next((w.glasses for w in water_rows if w.date == today), 0)

    # AI activity over the window (assistant turns only).
    window_start_dt = utcnow() - dt.timedelta(days=_WINDOW_DAYS)
    assistant_msgs = list(db.session.execute(
        db.select(ChatMessage).where(ChatMessage.role == "assistant",
                                     ChatMessage.user_id == user_id,
                                     ChatMessage.created_at >= window_start_dt)
    ).scalars())
    ai_activity = {
        "responses": len(assistant_msgs),
        "grounded": sum(1 for m in assistant_msgs if m.rag_used),
        "tokens_used": sum(m.tokens_used or 0 for m in assistant_msgs),
        "agents": sorted({m.agent for m in assistant_msgs if m.agent}),
    }
    score = compute_health_score(
        meals, water_rows,
        calorie_target=(targets or {}).get("calories", 0),
        days=_WINDOW_DAYS, today=today,
    )

    return ok({
        "profile_exists": profile is not None,
        "targets": targets,
        "today": {**{k: round(v, 1) for k, v in daily[today].items()},
                  "water_glasses": water_today},
        "week": {"calorie_series": series, "meal_count": len(meals),
                 "meals_today": sum(1 for m in meals if m.ts.date() == today),
                 "ai_activity": ai_activity},
        "bmi": latest_bmi.to_dict() if latest_bmi else None,
        "health_score": score.to_dict(),
    })
