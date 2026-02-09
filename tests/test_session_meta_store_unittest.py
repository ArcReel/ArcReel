import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from webui.server.agent_runtime.session_store import SessionMetaStore


class TestSessionMetaStore(unittest.TestCase):
    def test_session_lifecycle(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sessions.db"
            store = SessionMetaStore(db_path)

            session = store.create(project_name="demo", title="Demo Session")
            self.assertEqual(session.project_name, "demo")
            self.assertEqual(session.status, "idle")
            self.assertIsNotNone(session.transcript_path)

            sessions = store.list(project_name="demo")
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].id, session.id)

            # Test status update
            updated = store.update_status(session.id, "running")
            self.assertTrue(updated)

            running_session = store.get(session.id)
            self.assertIsNotNone(running_session)
            self.assertEqual(running_session.status, "running")

            # Test SDK session ID update
            store.update_sdk_session_id(session.id, "sdk-abc123")
            with_sdk_id = store.get(session.id)
            self.assertEqual(with_sdk_id.sdk_session_id, "sdk-abc123")

            # Test title update
            updated = store.update_title(session.id, "Renamed Session")
            self.assertTrue(updated)
            renamed_session = store.get(session.id)
            self.assertIsNotNone(renamed_session)
            self.assertEqual(renamed_session.title, "Renamed Session")

            # Test delete
            deleted = store.delete(session.id)
            self.assertTrue(deleted)
            self.assertIsNone(store.get(session.id))

    def test_list_with_filters(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sessions.db"
            store = SessionMetaStore(db_path)

            # Create sessions for different projects
            store.create(project_name="project_a", title="Session A1")
            store.create(project_name="project_a", title="Session A2")
            store.create(project_name="project_b", title="Session B1")

            # Filter by project
            sessions_a = store.list(project_name="project_a")
            self.assertEqual(len(sessions_a), 2)

            sessions_b = store.list(project_name="project_b")
            self.assertEqual(len(sessions_b), 1)

            # Filter by status
            store.update_status(sessions_a[0].id, "completed")
            completed = store.list(status="completed")
            self.assertEqual(len(completed), 1)

    def test_delete_nonexistent(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sessions.db"
            store = SessionMetaStore(db_path)

            deleted = store.delete("nonexistent-id")
            self.assertFalse(deleted)


if __name__ == "__main__":
    unittest.main()
