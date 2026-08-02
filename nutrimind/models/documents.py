"""Knowledge-base document registry (mirrored into ChromaDB at ingestion)."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nutrimind.extensions import db
from nutrimind.utils.time import utcnow

STATUSES = ("pending", "processing", "indexed", "failed")


class Document(db.Model):
    """One uploaded/seeded PDF and its indexing state (ARCHITECTURE.md §4.1)."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','indexed','failed')",
            name="ck_document_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(db.String(255))
    # 255, not 64: uploads fit in 64 ("instance/uploads/<32 hex>.pdf" = 53), but
    # seeded files store a repo-relative path ("knowledge_base/<name>.pdf") that
    # can exceed it. SQLite ignores VARCHAR limits, so this only surfaces as an
    # error once the data moves to Postgres.
    stored_name: Mapped[str] = mapped_column(db.String(255), unique=True)
    sha256: Mapped[str] = mapped_column(db.String(64), index=True)
    pages: Mapped[int] = mapped_column(default=0)
    chunk_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(db.String(12), default="pending")
    error: Mapped[Optional[str]] = mapped_column(db.Text)
    condition_tags: Mapped[list] = mapped_column(db.JSON, default=list)
    uploaded_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)

    def mark_indexed(self, *, pages: int, chunk_count: int) -> None:
        """Transition to 'indexed' after a successful ingestion run."""
        self.pages = pages
        self.chunk_count = chunk_count
        self.status = "indexed"
        self.error = None

    def mark_failed(self, error: str) -> None:
        """Transition to 'failed', keeping a truncated error for the UI."""
        self.status = "failed"
        self.error = error[:500]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "pages": self.pages,
            "chunk_count": self.chunk_count,
            "status": self.status,
            "error": self.error,
            "condition_tags": self.condition_tags or [],
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }
