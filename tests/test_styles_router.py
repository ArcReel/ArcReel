"""用户自定义风格库路由测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools import update_custom_style as update_custom_style_tool_module
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.update_custom_style import update_custom_style_tool
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import styles
from tests.auth_deps import AUTH_DEPENDENCIES


@pytest.fixture
async def _styles_env(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("source")
    manager.create_project_metadata("source", "韩剧项目", "", "drama")
    manager.create_project("target")
    manager.create_project_metadata("target", "新项目", "", "drama")

    monkeypatch.setattr(styles, "async_session_factory", factory)
    monkeypatch.setattr(styles, "get_project_manager", lambda: manager)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(styles.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

    yield {"client": TestClient(app), "manager": manager, "session_factory": factory}
    await engine.dispose()


def _set_source_style(manager: ProjectManager, description: str, *, with_image: bool = True) -> None:
    if with_image:
        (manager.get_project_path("source") / "style_reference.png").write_bytes(b"png-data")

    def _mutate(project: dict) -> None:
        project["style"] = ""
        project["style_description"] = description
        project.pop("style_template_id", None)
        if with_image:
            project["style_image"] = "style_reference.png"

    manager.update_project("source", _mutate)


class TestCustomStylesRouter:
    @pytest.mark.unit
    def test_save_list_and_apply_style_snapshot(self, _styles_env):
        client = _styles_env["client"]
        manager = _styles_env["manager"]
        _set_source_style(manager, "soft light, muted palette")

        saved = client.post("/api/v1/styles/from-project", json={"project_name": "source"})
        assert saved.status_code == 200, saved.text
        style = saved.json()["style"]
        assert style["name"] == "韩剧项目 · 风格"
        assert style["description"] == "soft light, muted palette"
        assert style["image_path"].startswith("_global_assets/style/")
        assert manager.load_project("source")["style_preset_id"] == style["id"]

        listed = client.get("/api/v1/styles")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [style["id"]]

        applied = client.post(
            f"/api/v1/styles/{style['id']}/apply",
            json={"project_name": "target"},
        )
        assert applied.status_code == 200, applied.text
        target = manager.load_project("target")
        assert target["style_description"] == "soft light, muted palette"
        assert target["style_preset_id"] == style["id"]
        assert target["style"] == ""
        assert (manager.get_project_path("target") / target["style_image"]).read_bytes() == b"png-data"

    @pytest.mark.unit
    def test_repeated_save_updates_same_card_instead_of_duplicating(self, _styles_env):
        client = _styles_env["client"]
        manager = _styles_env["manager"]
        _set_source_style(manager, "first", with_image=False)

        first = client.post("/api/v1/styles/from-project", json={"project_name": "source"})
        assert first.status_code == 200
        first_id = first.json()["style"]["id"]

        _set_source_style(manager, "second", with_image=False)
        second = client.post("/api/v1/styles/from-project", json={"project_name": "source"})
        assert second.status_code == 200
        assert second.json()["style"]["id"] == first_id
        assert second.json()["style"]["description"] == "second"
        assert len(client.get("/api/v1/styles").json()["items"]) == 1

    @pytest.mark.unit
    def test_edit_style_updates_library_without_mutating_applied_project_snapshot(self, _styles_env):
        client = _styles_env["client"]
        manager = _styles_env["manager"]
        _set_source_style(manager, "original prompt")

        saved = client.post("/api/v1/styles/from-project", json={"project_name": "source"}).json()["style"]
        applied = client.post(
            f"/api/v1/styles/{saved['id']}/apply",
            json={"project_name": "target"},
        )
        assert applied.status_code == 200
        old_library_image = manager.projects_root / saved["image_path"]
        assert old_library_image.exists()

        edited = client.patch(
            f"/api/v1/styles/{saved['id']}",
            data={
                "name": "暖调纪实",
                "description": "warm documentary light",
                "remove_image": "false",
            },
            files={"image": ("replacement.webp", b"replacement-image", "image/webp")},
        )
        assert edited.status_code == 200, edited.text
        style = edited.json()["style"]
        assert style["name"] == "暖调纪实"
        assert style["description"] == "warm documentary light"
        assert style["image_path"].endswith(".webp")
        assert (manager.projects_root / style["image_path"]).read_bytes() == b"replacement-image"
        assert not old_library_image.exists()

        target = manager.load_project("target")
        assert target["style_description"] == "original prompt"
        assert (manager.get_project_path("target") / target["style_image"]).read_bytes() == b"png-data"

        removed = client.patch(
            f"/api/v1/styles/{saved['id']}",
            data={
                "name": "暖调纪实",
                "description": "text-only style",
                "remove_image": "true",
            },
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["style"]["image_path"] is None
        assert not (manager.projects_root / style["image_path"]).exists()

    @pytest.mark.unit
    def test_edit_style_rejects_empty_content_and_duplicate_name(self, _styles_env):
        client = _styles_env["client"]
        manager = _styles_env["manager"]
        _set_source_style(manager, "first", with_image=False)
        first = client.post(
            "/api/v1/styles/from-project",
            json={"project_name": "source", "name": "First"},
        ).json()["style"]

        def _unlink(project: dict) -> None:
            project.pop("style_preset_id", None)
            project["style_description"] = "second"

        manager.update_project("source", _unlink)
        second = client.post(
            "/api/v1/styles/from-project",
            json={"project_name": "source", "name": "Second"},
        ).json()["style"]

        duplicate = client.patch(
            f"/api/v1/styles/{first['id']}",
            data={"name": second["name"], "description": "still valid", "remove_image": "false"},
        )
        assert duplicate.status_code == 409

        empty = client.patch(
            f"/api/v1/styles/{first['id']}",
            data={"name": first["name"], "description": "", "remove_image": "true"},
        )
        assert empty.status_code == 400

    @pytest.mark.unit
    def test_template_or_empty_style_cannot_be_saved(self, _styles_env):
        client = _styles_env["client"]
        manager = _styles_env["manager"]

        def _template(project: dict) -> None:
            project["style_template_id"] = "live_kdrama"
            project["style"] = "preset"

        manager.update_project("source", _template)
        assert client.post("/api/v1/styles/from-project", json={"project_name": "source"}).status_code == 400

        def _empty(project: dict) -> None:
            project.pop("style_template_id", None)
            project["style"] = ""
            project.pop("style_description", None)
            project.pop("style_image", None)

        manager.update_project("source", _empty)
        assert client.post("/api/v1/styles/from-project", json={"project_name": "source"}).status_code == 400

    @pytest.mark.unit
    async def test_agent_tool_uses_same_edit_operation(self, _styles_env, monkeypatch):
        client = _styles_env["client"]
        manager = _styles_env["manager"]
        _set_source_style(manager, "before")
        saved = client.post("/api/v1/styles/from-project", json={"project_name": "source"}).json()["style"]
        monkeypatch.setattr(
            update_custom_style_tool_module,
            "async_session_factory",
            _styles_env["session_factory"],
        )
        ctx = ToolContext(project_name="source", projects_root=manager.projects_root, pm=manager)

        result = await update_custom_style_tool(ctx).handler(
            {
                "style_id": saved["id"],
                "name": "Agent 调整风格",
                "description": "after",
                "use_current_project_image": True,
            }
        )

        assert result.get("is_error") is not True
        assert result["style"]["name"] == "Agent 调整风格"
        assert result["style"]["description"] == "after"
        assert manager.load_project("source")["style_description"] == "before"
