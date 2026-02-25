import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.factories import make_session_meta
from webui.server.routers import assistant


class _FakeService:
    def __init__(self):
        self.sessions = {"session-1": make_session_meta(id="session-1")}

    async def create_session(self, project_name, title):
        if project_name == "missing":
            raise FileNotFoundError(project_name)
        return make_session_meta(id="session-new", project_name=project_name, title=title or "")

    def list_sessions(self, **kwargs):
        return [make_session_meta(id="session-1", project_name=kwargs.get("project_name") or "demo")]

    def get_session(self, session_id):
        if session_id == "error":
            raise RuntimeError("boom")
        return self.sessions.get(session_id)

    def update_session_title(self, session_id, title):
        if title == "bad":
            raise ValueError("bad title")
        if session_id not in self.sessions:
            return None
        session = make_session_meta(id=session_id, title=title)
        self.sessions[session_id] = session
        return session

    async def delete_session(self, session_id):
        return session_id in self.sessions

    async def get_snapshot(self, session_id):
        if session_id == "missing":
            raise FileNotFoundError(session_id)
        return {"session_id": session_id, "status": "running", "turns": [], "pending_questions": []}

    async def send_message(self, session_id, content):
        if session_id == "missing":
            raise FileNotFoundError(session_id)
        if content == "bad":
            raise ValueError("bad")
        return {"status": "accepted", "session_id": session_id}

    async def interrupt_session(self, session_id):
        if session_id == "missing":
            raise FileNotFoundError(session_id)
        if session_id == "bad":
            raise ValueError("bad")
        return {"status": "accepted", "session_id": session_id, "session_status": "interrupted"}

    async def answer_user_question(self, session_id, question_id, answers):
        if session_id == "missing":
            raise FileNotFoundError(session_id)
        if question_id == "bad":
            raise ValueError("bad question")
        return {"status": "accepted", "session_id": session_id, "question_id": question_id, "answers": answers}

    async def stream_events(self, session_id):
        yield "event: snapshot\ndata: {}\n\n"
        await asyncio.sleep(0)

    def list_available_skills(self, project_name=None):
        if project_name == "missing":
            raise FileNotFoundError(project_name)
        return [{"name": "skill-a"}]


def _client(monkeypatch):
    fake = _FakeService()
    monkeypatch.setattr(assistant, "get_assistant_service", lambda: fake)
    app = FastAPI()
    app.include_router(assistant.router, prefix="/api/v1/assistant")
    return TestClient(app)


class TestAssistantRouterFull:
    def test_full_endpoints_and_errors(self, monkeypatch):
        with _client(monkeypatch) as client:
            create_ok = client.post("/api/v1/assistant/sessions", json={"project_name": "demo", "title": "T"})
            assert create_ok.status_code == 200
            assert create_ok.json()["id"] == "session-new"

            create_missing = client.post("/api/v1/assistant/sessions", json={"project_name": "missing", "title": "T"})
            assert create_missing.status_code == 404

            listed = client.get("/api/v1/assistant/sessions?project_name=demo")
            assert listed.status_code == 200
            assert listed.json()["sessions"][0]["id"] == "session-1"

            get_ok = client.get("/api/v1/assistant/sessions/session-1")
            assert get_ok.status_code == 200

            get_missing = client.get("/api/v1/assistant/sessions/missing")
            assert get_missing.status_code == 404

            update_ok = client.patch("/api/v1/assistant/sessions/session-1", json={"title": "new"})
            assert update_ok.status_code == 200
            assert update_ok.json()["session"]["title"] == "new"

            update_bad = client.patch("/api/v1/assistant/sessions/session-1", json={"title": "bad"})
            assert update_bad.status_code == 400

            update_missing = client.patch("/api/v1/assistant/sessions/no", json={"title": "x"})
            assert update_missing.status_code == 404

            delete_ok = client.delete("/api/v1/assistant/sessions/session-1")
            assert delete_ok.status_code == 200

            delete_missing = client.delete("/api/v1/assistant/sessions/no")
            assert delete_missing.status_code == 404

            messages = client.get("/api/v1/assistant/sessions/session-1/messages")
            assert messages.status_code == 410

            snapshot_ok = client.get("/api/v1/assistant/sessions/session-1/snapshot")
            assert snapshot_ok.status_code == 200

            snapshot_missing = client.get("/api/v1/assistant/sessions/missing/snapshot")
            assert snapshot_missing.status_code == 404

            send_ok = client.post("/api/v1/assistant/sessions/session-1/messages", json={"content": "hello"})
            assert send_ok.status_code == 200

            send_missing = client.post("/api/v1/assistant/sessions/missing/messages", json={"content": "hello"})
            assert send_missing.status_code == 404

            send_bad = client.post("/api/v1/assistant/sessions/session-1/messages", json={"content": "bad"})
            assert send_bad.status_code == 400

            interrupt_ok = client.post("/api/v1/assistant/sessions/session-1/interrupt")
            assert interrupt_ok.status_code == 200

            interrupt_missing = client.post("/api/v1/assistant/sessions/missing/interrupt")
            assert interrupt_missing.status_code == 404

            interrupt_bad = client.post("/api/v1/assistant/sessions/bad/interrupt")
            assert interrupt_bad.status_code == 400

            answer_empty = client.post(
                "/api/v1/assistant/sessions/session-1/questions/q1/answer",
                json={"answers": {}},
            )
            assert answer_empty.status_code == 400

            answer_ok = client.post(
                "/api/v1/assistant/sessions/session-1/questions/q1/answer",
                json={"answers": {"Q": "A"}},
            )
            assert answer_ok.status_code == 200

            answer_missing = client.post(
                "/api/v1/assistant/sessions/missing/questions/q1/answer",
                json={"answers": {"Q": "A"}},
            )
            assert answer_missing.status_code == 404

            answer_bad = client.post(
                "/api/v1/assistant/sessions/session-1/questions/bad/answer",
                json={"answers": {"Q": "A"}},
            )
            assert answer_bad.status_code == 400

            stream_missing = client.get("/api/v1/assistant/sessions/no/stream")
            assert stream_missing.status_code == 404

            stream_ok = client.get("/api/v1/assistant/sessions/session-1/stream")
            assert stream_ok.status_code == 200
            assert "text/event-stream" in stream_ok.headers["content-type"]

            skills_ok = client.get("/api/v1/assistant/skills")
            assert skills_ok.status_code == 200
            assert skills_ok.json()["skills"][0]["name"] == "skill-a"

            skills_missing = client.get("/api/v1/assistant/skills?project_name=missing")
            assert skills_missing.status_code == 404
