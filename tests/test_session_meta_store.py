from pathlib import Path

from server.agent_runtime.session_store import SessionMetaStore


class TestSessionMetaStore:
    def test_session_lifecycle(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        store = SessionMetaStore(db_path)

        session = store.create(project_name="demo", title="Demo Session")
        assert session.project_name == "demo"
        assert session.status == "idle"

        sessions = store.list(project_name="demo")
        assert len(sessions) == 1
        assert sessions[0].id == session.id

        # Test status update
        updated = store.update_status(session.id, "running")
        assert updated

        running_session = store.get(session.id)
        assert running_session is not None
        assert running_session.status == "running"

        # Test SDK session ID update
        store.update_sdk_session_id(session.id, "sdk-abc123")
        with_sdk_id = store.get(session.id)
        assert with_sdk_id.sdk_session_id == "sdk-abc123"

        # Test title update
        updated = store.update_title(session.id, "Renamed Session")
        assert updated
        renamed_session = store.get(session.id)
        assert renamed_session is not None
        assert renamed_session.title == "Renamed Session"

        # Test delete
        deleted = store.delete(session.id)
        assert deleted
        assert store.get(session.id) is None

    def test_list_with_filters(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        store = SessionMetaStore(db_path)

        # Create sessions for different projects
        store.create(project_name="project_a", title="Session A1")
        store.create(project_name="project_a", title="Session A2")
        store.create(project_name="project_b", title="Session B1")

        # Filter by project
        sessions_a = store.list(project_name="project_a")
        assert len(sessions_a) == 2

        sessions_b = store.list(project_name="project_b")
        assert len(sessions_b) == 1

        # Filter by status
        store.update_status(sessions_a[0].id, "completed")
        completed = store.list(status="completed")
        assert len(completed) == 1

    def test_delete_nonexistent(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        store = SessionMetaStore(db_path)

        deleted = store.delete("nonexistent-id")
        assert not deleted

    def test_interrupt_running_sessions(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        store = SessionMetaStore(db_path)

        running = store.create(project_name="demo", title="Running")
        completed = store.create(project_name="demo", title="Completed")
        idle = store.create(project_name="demo", title="Idle")

        store.update_status(running.id, "running")
        store.update_status(completed.id, "completed")

        interrupted_count = store.interrupt_running_sessions()

        assert interrupted_count == 1
        assert store.get(running.id).status == "interrupted"
        assert store.get(completed.id).status == "completed"
        assert store.get(idle.id).status == "idle"
