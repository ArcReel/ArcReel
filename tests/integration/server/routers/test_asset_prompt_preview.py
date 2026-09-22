"""项目资产预览按草稿渲染，保持项目与认证边界。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers._asset_router_factory import build_asset_router
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


@pytest.fixture
def preview_client(tmp_path):
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    project = manager.create_project_metadata("demo", style="水墨")
    project["style_description"] = "淡墨留白"
    for bucket, name in (("characters", "阿岚"), ("scenes", "庭院"), ("props", "宝剑"), ("products", "茶杯")):
        project[bucket] = {name: {"description": "已保存描述"}}
    project["characters"]["阿岚"]["derivatives"] = {"战斗装": {"description": "旧变化"}}
    manager.save_project("demo", project)
    app = FastAPI()
    register_error_handlers(app)
    for asset_type in ("character", "scene", "prop", "product"):
        app.include_router(
            build_asset_router(asset_type=asset_type, pm_getter=lambda: manager),
            dependencies=AUTH_DEPENDENCIES,
        )
    override_auth(app)
    with TestClient(app) as client:
        yield client, manager, app


def test_draft_preview_includes_style_without_saving(preview_client):
    client, manager, _app = preview_client
    response = client.post("/projects/demo/characters/阿岚/prompt-preview", json={"description": "草稿银袍"})

    assert response.status_code == 200
    result = response.json()
    assert result["unavailable"] is None
    assert "草稿银袍" in result["text"]
    assert "水墨" in result["text"]
    assert "淡墨留白" in result["text"]
    assert "已保存描述" not in result["text"]
    assert manager.load_project("demo")["characters"]["阿岚"]["description"] == "已保存描述"


@pytest.mark.parametrize(
    "path",
    ["scenes/庭院", "props/宝剑", "products/茶杯", "characters/阿岚/derivatives/战斗装"],
)
def test_all_asset_routes_render_draft(preview_client, path):
    client, _manager, _app = preview_client
    response = client.post(f"/projects/demo/{path}/prompt-preview", json={"description": "新外观"})

    assert response.status_code == 200
    assert "新外观" in response.json()["text"]
    assert response.json()["warnings"] == []


@pytest.mark.parametrize(
    ("path", "description"),
    [("characters/不存在", "描述"), ("characters/阿岚/derivatives/不存在", "描述"), ("characters/阿岚", " ")],
)
def test_unavailable_is_localized(preview_client, path, description):
    client, _manager, _app = preview_client
    response = client.post(
        f"/projects/demo/{path}/prompt-preview", json={"description": description}, headers={"Accept-Language": "zh"}
    )

    assert response.status_code == 200
    assert response.json()["text"] is None
    assert response.json()["unavailable"] == "资产不存在或尚未填写描述"


@pytest.mark.parametrize("payload", [{}, {"description": None}, {"description": 42}])
def test_description_is_required_and_must_be_text(preview_client, payload):
    client, _manager, _app = preview_client
    response = client.post("/projects/demo/characters/阿岚/prompt-preview", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize("path", ["characters/阿岚", "characters/阿岚/derivatives/战斗装"])
def test_preview_requires_authentication(preview_client, path):
    client, _manager, app = preview_client
    app.dependency_overrides.clear()
    response = client.post(f"/projects/demo/{path}/prompt-preview", json={"description": "描述"})

    assert response.status_code == 401


def test_missing_project_is_not_an_asset_unavailable_result(preview_client):
    client, _manager, _app = preview_client
    response = client.post("/projects/missing/characters/阿岚/prompt-preview", json={"description": "描述"})

    assert response.status_code == 404
    assert response.json()["detail"] == "项目 'missing' 不存在或未初始化"
