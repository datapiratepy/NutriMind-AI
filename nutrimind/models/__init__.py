"""SQLAlchemy models package.

Importing this package registers every model with the shared ``db`` metadata
so ``db.create_all()`` sees the full schema.
"""

from nutrimind.models.chat import ChatMessage
from nutrimind.models.documents import Document
from nutrimind.models.meals import MealLog, MealPlan
from nutrimind.models.profile import UserProfile
from nutrimind.models.tracking import BMIRecord, WaterLog

__all__ = [
    "BMIRecord",
    "ChatMessage",
    "Document",
    "MealLog",
    "MealPlan",
    "UserProfile",
    "WaterLog",
]
