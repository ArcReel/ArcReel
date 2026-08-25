from __future__ import annotations

import json
from pathlib import Path

from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.entry import create_project_tool, list_projects_tool, upload_source_tool


async def test_embedded_entry_tools_use_the_session_projects_root(tmp_path: Path) -> None:
    projects = ProjectManager(tmp_path / "projects")
    ctx = ToolContext("demo", projects.projects_root, pm=projects)

    created = await create_project_tool(ctx).handler(
        {
            "name": "demo",
            "title": "Demo",
            "content_mode": "narration",
            "generation_mode": "storyboard",
        }
    )
    listed = await list_projects_tool(ctx).handler({})
    upload_tool = upload_source_tool(ctx)
    uploaded = await upload_tool.handler({"filename": "novel.txt", "content": "hello"})

    assert json.loads(created["content"][0]["text"])["project"]["name"] == "demo"
    assert json.loads(listed["content"][0]["text"])["projects"][0]["name"] == "demo"
    assert json.loads(uploaded["content"][0]["text"])["source"]["path"] == "source/novel.txt"
    assert isinstance(upload_tool.input_schema, dict)
    assert "project" not in upload_tool.input_schema["properties"]
    assert (projects.get_project_path("demo") / "source" / "novel.txt").read_text() == "hello"


async def test_upload_source_closes_temporary_file_before_loading(tmp_path: Path, monkeypatch) -> None:
    from server import tool_runtime

    projects = ProjectManager(tmp_path / "projects")
    ctx = ToolContext("demo", projects.projects_root, pm=projects)
    await create_project_tool(ctx).handler(
        {
            "name": "demo",
            "title": "Demo",
            "content_mode": "narration",
            "generation_mode": "storyboard",
        }
    )
    tracked: dict[str, object] = {}
    original_temp = tool_runtime.tempfile.NamedTemporaryFile
    original_load = tool_runtime.SourceLoader.load

    def tracked_temp(*args, **kwargs):
        handle = original_temp(*args, **kwargs)
        tracked["handle"] = handle
        tracked["path"] = Path(handle.name)
        return handle

    def checked_load(*args, **kwargs):
        assert tracked["handle"].closed  # type: ignore[union-attr]
        return original_load(*args, **kwargs)

    monkeypatch.setattr(tool_runtime.tempfile, "NamedTemporaryFile", tracked_temp)
    monkeypatch.setattr(tool_runtime.SourceLoader, "load", staticmethod(checked_load))

    uploaded = await upload_source_tool(ctx).handler({"filename": "novel.txt", "content": "hello"})

    assert json.loads(uploaded["content"][0]["text"])["source"]["path"] == "source/novel.txt"
    assert not tracked["path"].exists()  # type: ignore[union-attr]


async def test_upload_source_cleans_temporary_file_when_write_fails(tmp_path: Path, monkeypatch) -> None:
    from server import tool_runtime

    projects = ProjectManager(tmp_path / "projects")
    ctx = ToolContext("demo", projects.projects_root, pm=projects)
    await create_project_tool(ctx).handler(
        {
            "name": "demo",
            "title": "Demo",
            "content_mode": "narration",
            "generation_mode": "storyboard",
        }
    )
    tracked: dict[str, Path] = {}
    original_temp = tool_runtime.tempfile.NamedTemporaryFile

    class FailingWrite:
        def __init__(self, *args, **kwargs):
            self.handle = original_temp(*args, **kwargs)
            self.name = self.handle.name
            tracked["path"] = Path(self.name)

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, _content: bytes) -> None:
            raise OSError("disk full")

        def flush(self) -> None:
            self.handle.flush()

    monkeypatch.setattr(tool_runtime.tempfile, "NamedTemporaryFile", FailingWrite)

    uploaded = await upload_source_tool(ctx).handler({"filename": "novel.txt", "content": "hello"})

    assert uploaded.get("is_error") is True
    assert not tracked["path"].exists()
