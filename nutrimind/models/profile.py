"""User profile model (single-profile deployment, extensible shape)."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nutrimind.extensions import db


class UserProfile(db.Model):
    """The single local user's profile (ARCHITECTURE.md §5).

    BMI and nutrition targets are *derived* (services), never stored here,
    so they can't go stale when height/weight change.
    """

    __tablename__ = "user_profile"
    __table_args__ = (
        CheckConstraint("age BETWEEN 1 AND 120", name="ck_profile_age"),
        CheckConstraint("height_cm BETWEEN 50 AND 272", name="ck_profile_height"),
        CheckConstraint("weight_kg BETWEEN 20 AND 350", name="ck_profile_weight"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(db.String(80))
    age: Mapped[int]
    gender: Mapped[str] = mapped_column(db.String(10))
    height_cm: Mapped[float]
    weight_kg: Mapped[float]
    activity_level: Mapped[str] = mapped_column(db.String(20))
    medical_conditions: Mapped[list] = mapped_column(db.JSON, default=list)
    allergies: Mapped[list] = mapped_column(db.JSON, default=list)
    country: Mapped[Optional[str]] = mapped_column(db.String(60))
    food_preference: Mapped[str] = mapped_column(db.String(20))
    weight_goal: Mapped[str] = mapped_column(db.String(10), default="maintain")
    daily_calorie_goal: Mapped[Optional[int]]
    created_at: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow,
                                                    onupdate=dt.datetime.utcnow)

    @classmethod
    def get_singleton(cls) -> "UserProfile | None":
        """Return the one profile row, or None before onboarding."""
        return db.session.execute(db.select(cls).limit(1)).scalar_one_or_none()

    def summary_for_prompt(self) -> str:
        """Compact one-line description injected into agent prompts (Phase 6)."""
        conditions = ", ".join(self.medical_conditions) or "none"
        allergies = ", ".join(self.allergies) or "none"
        return (
            f"{self.age}-year-old {self.gender}, {self.height_cm:.0f} cm, "
            f"{self.weight_kg:.0f} kg, activity: {self.activity_level}, "
            f"diet: {self.food_preference}, goal: {self.weight_goal}, "
            f"conditions: {conditions}, allergies: {allergies}"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "age": self.age,
            "gender": self.gender,
            "height_cm": self.height_cm,
            "weight_kg": self.weight_kg,
            "activity_level": self.activity_level,
            "medical_conditions": self.medical_conditions or [],
            "allergies": self.allergies or [],
            "country": self.country,
            "food_preference": self.food_preference,
            "weight_goal": self.weight_goal,
            "daily_calorie_goal": self.daily_calorie_goal,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
