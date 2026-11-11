"""文件视图和 Agent 不能删除仍在视频结算回退边界内的历史版本。"""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from lib.artifacts.version_manager import VersionManager
from lib.project import project_trash
from lib.project.file_view import move_file_to_trash
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import file_view
from server.services.currency.video_artifact_currency import VideoArtifactCommitter
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import use_project_manager
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


@pytest.mark.parametrize("entry", ["file_view", "agent"])
@pytest.mark.parametrize("resource_type", ["videos", "reference_videos"])
async def test_deletion_preserves_the_video_compensation_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, resource_type: str
) -> None:
    manager = ProjectManager(tmp_path)
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo")
    use_project_manager(monkeypatch, manager)
    project = manager.get_project_path("demo")
    current = project / resource_type / "scene_E1S01.mp4"
    current.parent.mkdir(exist_ok=True)
    current.write_bytes(b"previous selection")
    versions = VersionManager(project)
    previous = versions.add_version(resource_type, "E1S01", "old", source_file=current)
    target = versions.get_versions(resource_type, "E1S01")["versions"][0]["file"]
    committer = VideoArtifactCommitter(
        project_manager=manager,
        project_name="demo",
        project_path=project,
        versions=versions,
        resource_type=resource_type,
        resource_id="E1S01",
        prompt="new",
    )
    await committer.prepare_selection(current, 4, {"execution_script_file": "episode_1.json"})
    try:
        current.write_bytes(b"paid result")
        rejected = versions.add_version(resource_type, "E1S01", "new", source_file=current)
        if entry == "file_view":
            app = FastAPI()
            app.include_router(file_view.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
            register_error_handlers(app)
            override_auth(app)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete("/api/v1/projects/demo/file-view", params={"path": target})
            assert response.status_code == 409, response.text
            assert "所属资源正在更新" in response.json()["detail"]
        else:
            result = await run_declared_tool(
                "delete_project_file", ToolHarness("demo", tmp_path, manager), {"path": target}
            )
            assert result.problem is not None
            assert result.problem.code == "version_delete_in_use"

        assert project_trash.list_records(project) == []
        assert versions.reject_current_version(resource_type, "E1S01", rejected_version=rejected, current_file=current)
        assert versions.get_current_version(resource_type, "E1S01") == previous
        assert current.read_bytes() == b"previous selection"
        assert (project / target).read_bytes() == b"previous selection"
        assert [record["version"] for record in versions.get_versions(resource_type, "E1S01")["versions"]] == [
            previous,
            rejected,
        ]
    finally:
        await committer.release_admission_guard()

    # 结算结束后，保留的付费结果仍可作为历史版本移进回收站。
    paid_snapshot = versions.get_versions(resource_type, "E1S01")["versions"][-1]["file"]
    record = move_file_to_trash(project, paid_snapshot)
    assert record.object["version"] == rejected
    assert current.read_bytes() == b"previous selection"
