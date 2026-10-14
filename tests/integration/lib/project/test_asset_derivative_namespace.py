"""同一本体下的衍生名判重不区分大小写：衍生图落在 ``characters/derivatives/{本体}/{衍生}.png``。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.project.asset_rename import AssetRenameConflictError
from lib.project.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import characters
from tests.auth_deps import AUTH_DEPENDENCIES


@pytest.fixture
def alice_pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path))
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.add_character("demo", "Alice", "主角")
    return manager


def _set_derivatives(pm: ProjectManager, derivatives: dict[str, str]) -> None:
    def _mutate(project: dict) -> None:
        project["characters"]["Alice"]["derivatives"] = {
            name: {"description": description, "character_sheet": ""} for name, description in derivatives.items()
        }

    pm.update_project("demo", _mutate)


def _descriptions(pm: ProjectManager) -> dict[str, str]:
    table = pm.load_project("demo")["characters"]["Alice"]["derivatives"]
    return {name: entry["description"] for name, entry in table.items()}


def test_adding_a_derivative_differing_only_in_case_is_rejected(alice_pm: ProjectManager, monkeypatch) -> None:
    _set_derivatives(alice_pm, {"Coat": "大衣"})
    monkeypatch.setattr(characters, "get_project_manager", lambda: alice_pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(characters.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/projects/demo/characters/Alice/derivatives", json={"name": "coat", "description": "另一件"}
        )

    assert response.status_code == 422, response.text
    assert "Coat" in response.json()["detail"]
    assert _descriptions(alice_pm) == {"Coat": "大衣"}


def test_renaming_a_derivative_to_a_sibling_name_differing_only_in_case_is_rejected(alice_pm: ProjectManager) -> None:
    _set_derivatives(alice_pm, {"Coat": "大衣", "Dress": "长裙"})

    with pytest.raises(AssetRenameConflictError):
        alice_pm.rename_asset_derivative("character", "demo", "Alice", "Dress", "COAT")

    assert _descriptions(alice_pm) == {"Coat": "大衣", "Dress": "长裙"}


def test_a_derivative_can_change_only_the_case_of_its_own_name(alice_pm: ProjectManager) -> None:
    _set_derivatives(alice_pm, {"Coat": "大衣"})

    alice_pm.rename_asset_derivative("character", "demo", "Alice", "Coat", "coat")

    assert _descriptions(alice_pm) == {"coat": "大衣"}


def test_upserting_a_new_derivative_differing_only_in_case_is_rejected(alice_pm: ProjectManager) -> None:
    _set_derivatives(alice_pm, {"Coat": "大衣"})

    with pytest.raises(ValueError, match="Coat"):
        alice_pm.upsert_assets("demo", "characters", {"Alice": {"derivatives": {"coat": {"description": "另一件"}}}})

    assert _descriptions(alice_pm) == {"Coat": "大衣"}


def test_derivatives_already_differing_only_in_case_each_stay_updatable(alice_pm: ProjectManager) -> None:
    _set_derivatives(alice_pm, {"Coat": "大衣", "coat": "旧版登记的另一件"})

    alice_pm.upsert_assets("demo", "characters", {"Alice": {"derivatives": {"coat": {"description": "改过"}}}})
    alice_pm.upsert_assets("demo", "characters", {"Alice": {"derivatives": {"Coat": {"description": "也改过"}}}})

    assert _descriptions(alice_pm) == {"Coat": "也改过", "coat": "改过"}
