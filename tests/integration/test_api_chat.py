"""Integration tests: the real /api/chat (SSE + JSON), history, system endpoints."""

from __future__ import annotations

import json

from nutrimind.models import ChatMessage


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    """Parse SSE text into (event, data) tuples."""
    events = []
    current_event = None
    for line in raw.splitlines():
        if line.startswith("event: "):
            current_event = line[len("event: "):]
        elif line.startswith("data: ") and current_event:
            events.append((current_event, json.loads(line[len("data: "):])))
            current_event = None
    return events


# -- non-streaming ------------------------------------------------------------

def test_chat_non_streaming_full_metadata(client):
    response = client.post("/api/chat", json={
        "message": "how much protein is in paneer?", "stream": False})
    assert response.status_code == 200
    body = response.get_json()
    assert body["reply"]
    meta = body["meta"]
    assert meta["agent"] == "knowledge_agent"
    assert meta["routing"]["method"] == "rules"
    assert meta["routing"]["reason"]
    assert meta["response_source"] in ("grounded", "general_knowledge")
    assert meta["embedding_provider"] in ("hash", "local", "watsonx")
    assert meta["llm_mode"] == "demo"
    assert "generation_ms" in meta and "tokens" in meta
    assert isinstance(meta["citations"], list)


def test_chat_persists_history_with_agent_metadata(client):
    response = client.post("/api/chat", json={
        "message": "hello", "session_id": "test-session-1", "stream": False})
    assert response.status_code == 200

    with client.application.app_context():
        rows = ChatMessage.recent("test-session-1", limit=10)
        assert [r.role for r in rows] == ["user", "assistant"]
        assistant = rows[1]
        assert assistant.agent == "coordinator"
        assert assistant.rag_used is False
        assert assistant.sources == []
        assert assistant.created_at is not None


def test_chat_history_endpoint(client):
    client.post("/api/chat", json={"message": "hello",
                                   "session_id": "hist-1", "stream": False})
    body = client.get("/api/chat/history?session_id=hist-1").get_json()
    assert len(body["messages"]) == 2
    assert body["messages"][1]["agent"] == "coordinator"


def test_session_continuity(client):
    first = client.post("/api/chat", json={
        "message": "hello", "stream": False}).get_json()
    session_id = first["session_id"]
    client.post("/api/chat", json={"message": "what is my bmi",
                                   "session_id": session_id, "stream": False})
    with client.application.app_context():
        assert len(ChatMessage.recent(session_id, limit=10)) == 4


def test_chat_validation_errors(client):
    assert client.post("/api/chat", json={}).status_code == 400
    assert client.post("/api/chat", json={"message": "   "}).status_code == 400
    long_message = "x" * 3000
    assert client.post("/api/chat",
                       json={"message": long_message}).status_code == 400


# -- streaming ----------------------------------------------------------------

def test_chat_streaming_event_sequence(client):
    response = client.post("/api/chat", json={
        "message": "what foods are rich in iron?"})
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"

    events = _parse_sse(response.get_data(as_text=True))
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "status"                      # progress before tokens
    assert "routing" in kinds and "token" in kinds
    assert kinds.index("routing") < kinds.index("token")
    assert kinds[-1] == "final"

    routing = next(data for kind, data in events if kind == "routing")
    assert routing["agent"] == "knowledge_agent" and routing["reason"]

    final = next(data for kind, data in events if kind == "final")
    assert final["session_id"] and final["message_id"]
    assert final["meta"]["agent"] == "knowledge_agent"
    tokens_text = "".join(d["text"] for k, d in events if k == "token")
    assert tokens_text == final["text"]              # stream matches final


def test_chat_streaming_persists_history(client):
    response = client.post("/api/chat", json={
        "message": "hello", "session_id": "stream-hist"})
    events = _parse_sse(response.get_data(as_text=True))
    assert events[-1][0] == "final"
    with client.application.app_context():
        assert len(ChatMessage.recent("stream-hist", limit=10)) == 2


# -- system endpoints keep reporting correctly ---------------------------------

def test_health_reports_demo_mode(client):
    body = client.get("/api/health").get_json()
    assert body["status"] == "ok" and body["mode"] == "demo"
    assert body["database"] == "ok"


def test_system_info_diagnostics(client):
    body = client.get("/api/system/info").get_json()
    assert body["app"]["name"] == "NutriMind AI"
    assert body["mode"]["demo_active"] is True
    assert body["ibm"]["chat_model"].startswith("ibm/")
    chroma = body["storage"]["chroma"]
    assert chroma["provider"] in ("hash", "local", "watsonx")
    assert chroma["collection"].startswith("kb_")


def test_unknown_api_route_is_json_404(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_request_id_header_present(client):
    assert len(client.get("/api/health").headers.get("X-Request-ID", "")) == 8


def test_chat_sessions_listing(client):
    client.post("/api/chat", json={"message": "hello",
                                   "session_id": "sess-a", "stream": False})
    client.post("/api/chat", json={"message": "what is my bmi",
                                   "session_id": "sess-a", "stream": False})
    body = client.get("/api/chat/sessions").get_json()
    assert len(body["sessions"]) == 1
    session = body["sessions"][0]
    assert session["session_id"] == "sess-a"
    assert session["messages"] == 4
    assert session["preview"]
