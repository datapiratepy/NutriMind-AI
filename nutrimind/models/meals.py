"""Meal logging and saved meal plans."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nutrimind.extensions import db
from nutrimind.utils.time import utcnow


class MealLog(db.Model):
    """One logged meal with deterministic macro estimates (ARCHITECTURE.md §5)."""

    __tablename__ = "meal_logs"
    __table_args__ = (
        CheckConstraint(
            "meal_type IN ('breakfast','lunch','dinner','snack','other')",
            name="ck_meal_type",
        ),
        CheckConstraint("calories >= 0", name="ck_meal_calories"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    meal_type: Mapped[str] = mapped_column(db.String(12), default="other")
    raw_text: Mapped[Optional[str]] = mapped_column(db.Text)
    items: Mapped[list] = mapped_column(db.JSON, default=list)
    calories: Mapped[float] = mapped_column(default=0.0)
    protein_g: Mapped[float] = mapped_column(default=0.0)
    fat_g: Mapped[float] = mapped_column(default=0.0)
    carbs_g: Mapped[float] = mapped_column(default=0.0)
    fiber_g: Mapped[float] = mapped_column(default=0.0)
    quality_score: Mapped[Optional[int]]
    suggestions: Mapped[Optional[str]] = mapped_column(db.Text)

    @classmethod
    def since(cls, days: int, user_id: int) -> list["MealLog"]:
        """One user's logs from the last ``days`` days, oldest first."""
        cutoff = utcnow() - dt.timedelta(days=days)
        return list(db.session.execute(
            db.select(cls).where(cls.ts >= cutoff, cls.user_id == user_id)
            .order_by(cls.ts)
        ).scalars())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts.isoformat() if self.ts else None,
            "meal_type": self.meal_type,
            "raw_text": self.raw_text,
            "items": self.items or [],
            "calories": self.calories,
            "protein_g": self.protein_g,
            "fat_g": self.fat_g,
            "carbs_g": self.carbs_g,
            "fiber_g": self.fiber_g,
            "quality_score": self.quality_score,
            "suggestions": self.suggestions,
        }


class MealPlan(db.Model):
    """A saved, generated meal plan (created by the Meal Planner agent)."""

    __tablename__ = "meal_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    title: Mapped[str] = mapped_column(db.String(120))
    targets: Mapped[dict] = mapped_column(db.JSON, default=dict)
    plan: Mapped[dict] = mapped_column(db.JSON, default=dict)
    is_favorite: Mapped[bool] = mapped_column(default=False)

    def to_dict(self, *, include_plan: bool = True) -> dict:
        data = {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "title": self.title,
            "targets": self.targets or {},
            "is_favorite": self.is_favorite,
        }
        if include_plan:
            data["plan"] = self.plan or {}
        return data
