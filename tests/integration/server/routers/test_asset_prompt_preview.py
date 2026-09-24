"""项目资产预览按草稿渲染，与执行取同一份生成输入，保持项目与认证边界。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers._asset_router_factory import build_asset_router
from server.services.admission import prompt_preview
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.integration.server.services.tasks.generation_tasks_support import FakeGenerator, fake_resolve_ctx

_OWNER_SHEET = "characters/阿岚.png"
_ORIGINALS = ("characters/refs/阿岚.png", "products/refs/茶杯_1.png", "products/refs/茶杯_2.png")


def _write_image(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), (40, 80, 120)).save(path, format="PNG")


@pytest.fixture
def preview_client(tmp_path):
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    project = manager.create_project_metadata("demo", style="水墨")
    project["style_description"] = "淡墨留白"
    for bucket, name in (("characters", "阿岚"), ("scenes", "庭院"), ("props", "宝剑"), ("products", "茶杯")):
        project[bucket] = {name: {"description": "已保存描述"}}
    project["characters"]["阿岚"].update({"reference_image": _ORIGINALS[0], "character_sheet": _OWNER_SHEET})
    project["characters"]["阿岚"]["derivatives"] = {"战斗装": {"description": "旧变化"}}
    project["products"]["茶杯"]["reference_images"] = list(_ORIGINALS[1:])
    manager.save_project("demo", project)
    project_dir = manager.get_project_path("demo")
    for relative in (_OWNER_SHEET, *_ORIGINALS):
        _write_image(project_dir / relative)
    ProjectArtifactManifestAdapter(project_dir).put_entry(
        ArtifactKey.asset_sheet("character", "阿岚"),
        ArtifactManifestEntry(
            artifact_path=_OWNER_SHEET,
            basis_digest=ArtifactBasis.build("test/registered", kind_version=1, inputs={}).digest,
        ),
    )
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


def _preview(client, path: str, description: str) -> dict:
    response = client.post(
        f"/projects/demo/{path}/prompt-preview", json={"description": description}, headers={"Accept-Language": "zh"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_draft_preview_includes_style_without_saving(preview_client):
    client, manager, _app = preview_client
    result = _preview(client, "characters/阿岚", "草稿银袍")

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
    result = _preview(client, path, "新外观")

    assert "新外观" in result["text"]
    assert result["warnings"] == []


@pytest.mark.parametrize("path", ["products/茶杯", "characters/阿岚/derivatives/战斗装"])
def test_product_and_derivative_keep_execution_style_policy(preview_client, path):
    client, _manager, _app = preview_client
    result = _preview(client, path, "  银色衣裳  ")

    assert "银色衣裳" in result["text"]
    assert "水墨" not in result["text"]
    assert "淡墨留白" not in result["text"]


def test_draft_description_fills_a_stored_blank(preview_client):
    client, manager, _app = preview_client
    project = manager.load_project("demo")
    project["scenes"]["庭院"]["description"] = ""
    manager.save_project("demo", project)

    assert "草稿庭院" in _preview(client, "scenes/庭院", "草稿庭院")["text"]


@pytest.mark.parametrize("entry", [None, "invalid"])
def test_malformed_asset_entry_makes_preview_unavailable(preview_client, entry):
    client, manager, _app = preview_client
    project = manager.load_project("demo")
    project["scenes"]["庭院"] = entry
    project_file = manager.get_project_path("demo") / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    result = _preview(client, "scenes/庭院", "草稿庭院")

    assert result["text"] is None
    assert result["unavailable"] == "提示词无法渲染，请检查其格式"


@pytest.mark.parametrize(
    ("path", "description", "unavailable"),
    [
        ("characters/不存在", "描述", "资产不存在或尚未填写描述"),
        ("characters/阿岚/derivatives/不存在", "描述", "资产不存在或尚未填写描述"),
        ("characters/阿岚", " ", "资产「阿岚」还没有填写描述，无法生成资产图"),
        ("characters/阿岚/derivatives/战斗装", " ", "衍生「战斗装」还没有填写外观变化，无法生成资产图"),
    ],
)
def test_unavailable_is_localized(preview_client, path, description, unavailable):
    client, _manager, _app = preview_client
    result = _preview(client, path, description)

    assert result["text"] is None
    assert result["unavailable"] == unavailable


def test_missing_original_makes_the_preview_unavailable(preview_client):
    client, manager, _app = preview_client
    (manager.get_project_path("demo") / _ORIGINALS[2]).unlink()

    result = _preview(client, "products/茶杯", "新外观")

    assert result["text"] is None
    assert result["unavailable"] == "声明的原图读不到：product: 茶杯；请重新上传原图，或清除原图字段"


@pytest.mark.parametrize("remove", ["file", "registration"])
def test_derivative_preview_is_unavailable_without_a_usable_owner_sheet(preview_client, remove):
    client, manager, _app = preview_client
    project_dir = manager.get_project_path("demo")
    if remove == "file":
        (project_dir / _OWNER_SHEET).unlink()
    else:
        ProjectArtifactManifestAdapter(project_dir).delete_entry(ArtifactKey.asset_sheet("character", "阿岚"))

    result = _preview(client, "characters/阿岚/derivatives/战斗装", "新外观")

    assert result["text"] is None
    assert result["unavailable"] == "角色「阿岚」还没有资产图，请先生成本体资产图再生成衍生"


def test_references_beyond_the_provider_limit_are_reported(preview_client, monkeypatch):
    client, _manager, _app = preview_client
    monkeypatch.setattr(
        prompt_preview, "resolve_generation_context", fake_resolve_ctx(FakeGenerator(), image_max_reference_images=1)
    )

    result = _preview(client, "products/茶杯", "新外观")

    assert "新外观" in result["text"]
    assert len(result["warnings"]) == 1
    assert "上限 1" in result["warnings"][0]


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
