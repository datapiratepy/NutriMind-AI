"""Chat history with agent attribution, RAG flag and citations."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nutrimind.extensions import db


class ChatMessage(db.Model):
    """One chat turn (ARCHITECTURE.md §5 + carry-forward UI requirements).

    ``agent``, ``rag_used`` and ``sources`` exist so the UI can always show
    *which* specialized agent answered and *what* it retrieved — the visible
    agentic-AI evidence evaluators look for.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="ck_chat_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(db.String(36), index=True)
    role: Mapped[str] = mapped_column(db.String(10))
    agent: Mapped[Optional[str]] = mapped_column(db.String(40))
    content: Mapped[str] = mapped_column(db.Text)
    rag_used: Mapped[bool] = mapped_column(default=False)
    sources: Mapped[list] = mapped_column(db.JSON, default=list)
    tokens_used: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow, index=True)

    @classmethod
    def recent(cls, session_id: str, limit: int = 20) -> list["ChatMessage"]:
        """Last ``limit`` messages of a session in chronological order."""
        rows = list(db.session.execute(
            db.select(cls).where(cls.session_id == session_id)
            .order_by(cls.created_at.desc(), cls.id.desc()).limit(limit)
        ).scalars())
        return list(reversed(rows))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "agent": self.agent,
            "content": self.content,
            "rag_used": self.rag_used,
            "sources": self.sources or [],
            "tokens_used": self.tokens_used,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
