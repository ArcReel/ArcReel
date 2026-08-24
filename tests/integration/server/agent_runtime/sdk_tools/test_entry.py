from __future__ import annotations

import json
from pathlib import Path

from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.entry import create_project_tool, list_projects_tool, upload_source_tool


async def test_embedded_entry_tools_use_the_session_projects_root(tmp_path: Path) -> None:
    projects = ProjectManager(tmp_path / "projects")
    ctx = ToolContext("session", projects.projects_root, pm=projects)

    created = await create_project_tool(ctx).handler(
        {
            "name": "demo",
            "title": "Demo",
            "content_mode": "narration",
            "generation_mode": "storyboard",
        }
    )
    listed = await list_projects_tool(ctx).handler({})
    uploaded = await upload_source_tool(ctx).handler({"project": "demo", "filename": "novel.txt", "content": "hello"})

    assert json.loads(created["content"][0]["text"])["project"]["name"] == "demo"
    assert json.loads(listed["content"][0]["text"])["projects"][0]["name"] == "demo"
    assert json.loads(uploaded["content"][0]["text"])["source"]["path"] == "source/novel.txt"
    assert (projects.get_project_path("demo") / "source" / "novel.txt").read_text() == "hello"
