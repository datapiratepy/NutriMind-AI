"""Profile, BMI, water and targets API.

Endpoints
    GET  /api/profile          -> {"profile": {...} | null}
    PUT  /api/profile          -> upsert (full payload; partial with ?partial=1);
                                  auto-records a BMIRecord when height/weight change
    GET  /api/targets          -> daily nutrition targets from the profile
    POST /api/bmi              -> {"height_cm", "weight_kg"} -> assessment (persisted)
    GET  /api/bmi/history      -> BMI trend records (newest first)
    POST /api/water            -> {"glasses"?: int (delta, default 1)} -> today's total
    GET  /api/water/today      -> today's total
"""

from __future__ import annotations

import datetime as dt

from flask import Blueprint, request

from nutrimind.exceptions import ValidationError
from nutrimind.extensions import db
from nutrimind.models import BMIRecord, UserProfile, WaterLog
from nutrimind.routes import ok
from nutrimind.services import bmi_service
from nutrimind.services.nutrition_service import targets_for_profile
from nutrimind.utils.validators import (
    WATER_GLASSES_RANGE,
    validate_profile_payload,
    validate_range,
)

profile_api = Blueprint("profile_api", __name__, url_prefix="/api")


def _record_bmi(height_cm: float, weight_kg: float) -> BMIRecord:
    """Persist a BMI snapshot for the trend chart; returns the new record."""
    result = bmi_service.assess(height_cm, weight_kg)
    record = BMIRecord(
        height_cm=height_cm,
        weight_kg=weight_kg,
        bmi=result.bmi,
        category=result.category,
        ideal_weight_min_kg=result.ideal_weight_min_kg,
        ideal_weight_max_kg=result.ideal_weight_max_kg,
    )
    db.session.add(record)
    return record


@profile_api.get("/profile")
def get_profile():
    profile = UserProfile.get_singleton()
    return ok({"profile": profile.to_dict() if profile else None})


@profile_api.put("/profile")
def upsert_profile():
    partial = request.args.get("partial") == "1"
    cleaned = validate_profile_payload(request.get_json(silent=True) or {}, partial=partial)
    if not cleaned:
        raise ValidationError("No valid fields provided.")

    profile = UserProfile.get_singleton()
    created = profile is None
    if created:
        if partial:
            raise ValidationError("No profile exists yet — send the full profile first.")
        profile = UserProfile(**cleaned)
        db.session.add(profile)
    else:
        body_changed = any(getattr(profile, f) != v for f, v in cleaned.items())
        for field, value in cleaned.items():
            setattr(profile, field, value)
        if not body_changed:
            db.session.commit()
            return ok({"profile": profile.to_dict(), "created": False})

    _record_bmi(profile.height_cm, profile.weight_kg)
    db.session.commit()
    return ok({"profile": profile.to_dict(), "created": created}, 201 if created else 200)


@profile_api.get("/targets")
def get_targets():
    profile = UserProfile.get_singleton()
    if profile is None:
        raise ValidationError("No profile yet — create your profile first.",
                              hint="PUT /api/profile with your details.")
    return ok({"targets": targets_for_profile(profile).to_dict()})


@profile_api.post("/bmi")
def compute_bmi():
    data = request.get_json(silent=True) or {}
    result = bmi_service.assess(data.get("height_cm"), data.get("weight_kg"))
    record = _record_bmi(data.get("height_cm"), data.get("weight_kg"))
    db.session.commit()
    return ok({"assessment": result.to_dict(), "record_id": record.id}, 201)


@profile_api.get("/bmi/history")
def bmi_history():
    rows = db.session.execute(
        db.select(BMIRecord).order_by(BMIRecord.ts.desc()).limit(50)
    ).scalars()
    return ok({"records": [r.to_dict() for r in rows]})


@profile_api.post("/water")
def log_water():
    data = request.get_json(silent=True) or {}
    delta = int(validate_range(data.get("glasses", 1), "glasses", -5, WATER_GLASSES_RANGE[1]))
    row = WaterLog.add_glasses(delta)
    db.session.commit()
    return ok({"water": row.to_dict()})


@profile_api.get("/water/today")
def water_today():
    row = db.session.execute(
        db.select(WaterLog).where(WaterLog.date == dt.datetime.utcnow().date())
    ).scalar_one_or_none()
    return ok({"water": row.to_dict() if row else {
        "date": dt.datetime.utcnow().date().isoformat(), "glasses": 0}})
