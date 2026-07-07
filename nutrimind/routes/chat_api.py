"""Chat API — the multi-agent entry point with SSE streaming.

Endpoint
    POST /api/chat
    body: {"message": str, "session_id"?: str, "stream"?: bool (default true)}

Streaming (Server-Sent Events, ``text/event-stream``) — event sequence:

    event: status    data: {"message": "Analyzing your request…"}
    event: routing   data: {"intent", "agent", "reason", "method"}
    event: status    data: {"message": "Searching the knowledge base…"}
    event: token     data: {"text": "..."}          (many)
    event: final     data: {"message_id", "session_id", "text", "meta": {...}}
    event: error     data: {"code", "message", "hint"?}   (on failure)

``meta`` carries the full UI contract: agent, grounded, response_source,
embedding_provider, llm_mode, retrieved_chunks, citations, tools_used,
generation_ms, tokens, routing. Non-streaming mode returns one JSON object:
{"reply", "session_id", "message_id", "meta"}.

Both user and assistant turns persist to ``chat_messages`` with agent,
grounded flag, citations and token count.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Iterator

from flask import Blueprint, Response, current_app, g, request, stream_with_context

from nutrimind.agents import get_coordinator
from nutrimind.agents.base_agent import AgentRequest
from nutrimind.agents.tools import Toolbox
from nutrimind.exceptions import NutriMindError, ValidationError
from nutrimind.extensions import db
from nutrimind.models import ChatMessage, UserProfile
from nutrimind.routes import ok
from nutrimind.services.llm import get_llm_client
from nutrimind.services.rag_service import get_rag_service
from nutrimind.utils.decorators import rate_limit
from nutrimind.utils.validators import sanitize_text

logger = logging.getLogger(__name__)

chat_api = Blueprint("chat_api", __name__, url_prefix="/api")

_HISTORY_TURNS = 6  # prior turns forwarded to conversational agents


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_request(message: str, session_id: str) -> AgentRequest:
    settings = current_app.config["NUTRIMIND_SETTINGS"]
    history = [{"role": m.role, "content": m.content}
               for m in ChatMessage.recent(session_id, limit=_HISTORY_TURNS)
               if m.role in ("user", "assistant")]
    return AgentRequest(
        message=message,
        toolbox=Toolbox(get_rag_service(settings)),
        llm=get_llm_client(settings),
        profile=UserProfile.get_singleton(),
        history=history,
    )


def _persist_turn(session_id: str, message: str, final_event: dict) -> int:
    """Store the user + assistant messages; returns the assistant row id."""
    meta = final_event["meta"]
    db.session.add(ChatMessage(session_id=session_id, role="user",
                               content=message))
    assistant = ChatMessage(
        session_id=session_id, role="assistant",
        content=final_event["text"],
        agent=meta["agent"], rag_used=meta["grounded"],
        sources=meta["citations"], tokens_used=meta["tokens"]["total"] or 0)
    db.session.add(assistant)
    db.session.commit()
    return assistant.id


@chat_api.post("/chat")
@rate_limit(max_calls=20, per_seconds=60)
def chat():
    data = request.get_json(silent=True) or {}
    message = sanitize_text(data.get("message"), max_chars=2000, field="message")
    session_id = str(data.get("session_id") or uuid.uuid4())[:36]
    stream = bool(data.get("stream", True))

    agent_request = _build_request(message, session_id)
    coordinator = get_coordinator()

    if not stream:
        final = None
        for event in coordinator.handle(agent_request):
            if event["type"] == "final":
                final = event
        message_id = _persist_turn(session_id, message, final)
        return ok({"reply": final["text"], "session_id": session_id,
                   "message_id": message_id, "meta": final["meta"]})

    request_id = getattr(g, "request_id", "-")

    def event_stream() -> Iterator[str]:
        try:
            for event in coordinator.handle(agent_request):
                kind = event.pop("type")
                if kind == "final":
                    message_id = _persist_turn(session_id, message, {
                        "type": "final", **event})
                    yield _sse("final", {"message_id": message_id,
                                         "session_id": session_id,
                                         "text": event["text"],
                                         "meta": event["meta"]})
                else:
                    yield _sse(kind, event)
        except NutriMindError as exc:
            logger.warning("chat failed: %s", exc.user_message)
            payload = exc.to_payload()
            payload["request_id"] = request_id
            yield _sse("error", payload)
        except Exception:  # noqa: BLE001 — stream must terminate cleanly
            logger.exception("chat stream crashed")
            yield _sse("error", {"code": "internal_error",
                                 "message": "The assistant hit an unexpected "
                                            "error. Details were logged.",
                                 "request_id": request_id})

    return Response(stream_with_context(event_stream()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@chat_api.get("/chat/sessions")
def chat_sessions():
    """Recent conversation sessions with a preview (dashboard + history)."""
    rows = db.session.execute(db.select(ChatMessage).order_by(
        ChatMessage.created_at.desc()).limit(300)).scalars()
    sessions: dict[str, dict] = {}
    for message in rows:
        entry = sessions.setdefault(message.session_id, {
            "session_id": message.session_id, "messages": 0,
            "preview": "", "last_at": message.created_at.isoformat()})
        entry["messages"] += 1
        if message.role == "user":
            entry["preview"] = message.content[:80]  # oldest user msg wins (desc scan)
    return ok({"sessions": list(sessions.values())[:20]})


@chat_api.get("/chat/history")
def chat_history():
    """Messages for a session (newest last) — used by the chat page reload."""
    session_id = sanitize_text(request.args.get("session_id"), max_chars=36,
                               field="session_id")
    limit = min(int(request.args.get("limit", 50)), 200)
    if limit < 1:
        raise ValidationError("'limit' must be positive.")
    return ok({"messages": [m.to_dict()
                            for m in ChatMessage.recent(session_id, limit=limit)]})
