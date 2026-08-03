"""BMI history and daily water intake."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nutrimind.extensions import db
from nutrimind.utils.time import utcnow, utctoday


class BMIRecord(db.Model):
    """A point-in-time BMI computation, kept for the trend chart."""

    __tablename__ = "bmi_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    height_cm: Mapped[float]
    weight_kg: Mapped[float]
    bmi: Mapped[float]
    category: Mapped[str] = mapped_column(db.String(20))
    ideal_weight_min_kg: Mapped[float]
    ideal_weight_max_kg: Mapped[float]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts.isoformat() if self.ts else None,
            "height_cm": self.height_cm,
            "weight_kg": self.weight_kg,
            "bmi": self.bmi,
            "category": self.category,
            "ideal_weight_min_kg": self.ideal_weight_min_kg,
            "ideal_weight_max_kg": self.ideal_weight_max_kg,
        }


class WaterLog(db.Model):
    """Glasses of water per calendar day (one row per day)."""

    __tablename__ = "water_logs"
    __table_args__ = (
        CheckConstraint("glasses >= 0 AND glasses <= 30", name="ck_water_glasses"),
        # One row per user per day. This was previously unique on `date` alone,
        # which is the clearest evidence that single-user was a schema invariant
        # and not merely a convention: the second account to log water on any
        # given day would have collided at the database level.
        db.UniqueConstraint("user_id", "date", name="uq_water_user_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    glasses: Mapped[int] = mapped_column(default=0)

    @classmethod
    def add_glasses(cls, amount: int, user_id: int, *,
                    on_date: dt.date | None = None) -> "WaterLog":
        """Upsert one user's row for a day, clamping the total to the valid range."""
        day = on_date or utctoday()  # UTC day, matching MealLog.ts
        row = db.session.execute(
            db.select(cls).where(cls.date == day, cls.user_id == user_id)
        ).scalar_one_or_none()
        if row is None:
            row = cls(date=day, glasses=0, user_id=user_id)
            db.session.add(row)
        row.glasses = max(0, min(30, row.glasses + amount))
        return row

    @classmethod
    def for_day(cls, day: dt.date, user_id: int) -> "WaterLog | None":
        return db.session.execute(
            db.select(cls).where(cls.date == day, cls.user_id == user_id)
        ).scalar_one_or_none()

    def to_dict(self) -> dict:
        return {"date": self.date.isoformat(), "glasses": self.glasses}
