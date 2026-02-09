"""Unit tests for SessionManager project cwd scoping."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from webui.server.agent_runtime.session_manager import SessionManager
from webui.server.agent_runtime.session_store import SessionMetaStore


class _FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class TestSessionManagerProjectScope(unittest.TestCase):
    def test_build_options_uses_project_directory_as_cwd(self):
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            project_dir = tmppath / "projects" / "demo"
            project_dir.mkdir(parents=True)
            store = SessionMetaStore(tmppath / "sessions.db")
            manager = SessionManager(
                project_root=tmppath,
                data_dir=tmppath,
                meta_store=store,
            )

            with patch("webui.server.agent_runtime.session_manager.SDK_AVAILABLE", True):
                with patch(
                    "webui.server.agent_runtime.session_manager.ClaudeAgentOptions",
                    _FakeOptions,
                ):
                    options = manager._build_options("demo")

            self.assertEqual(options.kwargs["cwd"], str(project_dir.resolve()))

    def test_build_options_raises_when_project_missing(self):
        with TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "projects").mkdir(parents=True, exist_ok=True)
            store = SessionMetaStore(tmppath / "sessions.db")
            manager = SessionManager(
                project_root=tmppath,
                data_dir=tmppath,
                meta_store=store,
            )

            with patch("webui.server.agent_runtime.session_manager.SDK_AVAILABLE", True):
                with patch(
                    "webui.server.agent_runtime.session_manager.ClaudeAgentOptions",
                    _FakeOptions,
                ):
                    with self.assertRaises(FileNotFoundError):
                        manager._build_options("missing-project")


if __name__ == "__main__":
    unittest.main()
