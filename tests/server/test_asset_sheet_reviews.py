from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifact_activation import register_current_resource_artifact, resolve_current_artifact_target
from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.confirm_asset_sheets import confirm_asset_sheets_tool
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import asset_sheet_reviews
from server.services.asset_sheet_reviews import (
    AssetSheetReviewError,
    AssetSheetSelection,
    confirm_asset_sheets_current,
)

pytestmark = pytest.mark.unit


def _project(root: Path, *, description: str = "old courtyard") -> tuple[ProjectManager, Path]:
    pm = ProjectManager(root)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "pastoral", "narration")
    assert pm._add_asset(
        "scene",
        "demo",
        "Courtyard",
        {"description": description, "scene_sheet": ""},
    )
    project_dir = pm.get_project_path("demo")

    def _register(_target) -> None:
        register_current_resource_artifact(
            project_dir,
            resource_type="scenes",
            resource_id="Courtyard",
        )

    pm.install_asset_sheet_bytes(
        "scene",
        "demo",
        "Courtyard",
        "scenes/Courtyard.png",
        b"reviewed-image",
        on_commit=_register,
    )
    pm.update_project(
        "demo",
        lambda project: project["scenes"]["Courtyard"].update(description="current courtyard"),
    )
    return pm, project_dir


def test_accept_existing_sheet_updates_only_the_current_basis(tmp_path):
    pm, project_dir = _project(tmp_path)
    key = ArtifactKey.asset_sheet("scene", "Courtyard")
    adapter = ProjectArtifactManifestAdapter(project_dir)
    old_claim = adapter.get_entry(key)
    expected = resolve_current_artifact_target(project_dir, key)
    original_bytes = (project_dir / "scenes" / "Courtyard.png").read_bytes()

    assert old_claim is not None
    assert expected is not None
    assert old_claim != expected

    result = confirm_asset_sheets_current("demo", manager=pm)

    assert result["changed"] is True
    assert result["confirmed"] == [{"asset_type": "scene", "name": "Courtyard", "sheet_path": "scenes/Courtyard.png"}]
    assert adapter.get_entry(key) == expected
    assert (project_dir / "scenes" / "Courtyard.png").read_bytes() == original_bytes


def test_selected_missing_sheet_fails_without_partial_manifest_update(tmp_path):
    pm, project_dir = _project(tmp_path)
    key = ArtifactKey.asset_sheet("scene", "Courtyard")
    adapter = ProjectArtifactManifestAdapter(project_dir)
    before = adapter.get_entry(key)

    with pytest.raises(AssetSheetReviewError, match="asset sheet not found"):
        confirm_asset_sheets_current(
            "demo",
            selections=[AssetSheetSelection("scene", "Missing")],
            manager=pm,
        )

    assert adapter.get_entry(key) == before


def test_web_and_agent_use_the_same_confirmation_service(tmp_path, monkeypatch):
    web_pm, web_dir = _project(tmp_path / "web")
    agent_pm, agent_dir = _project(tmp_path / "agent")
    monkeypatch.setattr(asset_sheet_reviews, "get_project_manager", lambda: web_pm)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(asset_sheet_reviews.router, prefix="/api/v1")
    with TestClient(app) as client:
        response = client.post("/api/v1/projects/demo/asset-sheets/confirm-current", json={})

    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "agent", pm=agent_pm)
    agent_result = asyncio.run(confirm_asset_sheets_tool(ctx).handler({}))

    key = ArtifactKey.asset_sheet("scene", "Courtyard")
    assert response.status_code == 200, response.text
    assert response.json()["confirmed_count"] == 1
    assert agent_result.get("is_error") is not True
    assert ProjectArtifactManifestAdapter(web_dir).get_entry(key) == resolve_current_artifact_target(web_dir, key)
    assert ProjectArtifactManifestAdapter(agent_dir).get_entry(key) == resolve_current_artifact_target(agent_dir, key)
