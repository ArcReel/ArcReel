"""Shared library-asset construction and asset-image freshness reads for applying global-library assets to projects."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.project.asset_derivatives import derivative_artifact_key, derivative_sheet_relative_path
from lib.project.asset_types import DERIVATIVES_FIELD
from lib.project.project_manager import ProjectManager
from tests.integration.server.derivative_sheet_support import solid_png_bytes

_OWNER = "王"
#: Two derivatives of library character 王: names, change descriptions, and image solid colors.
_DERIVATIVES = (("战斗装", "黑甲", (120, 0, 0)), ("便装", "布衣", (0, 120, 0)))

#: All current conclusions read by :func:`character_sheet_statuses` after applying the library character stored by
#: :func:`store_character_with_derivatives`.
LIBRARY_SHEETS_ALL_CURRENT = dict.fromkeys(
    (_OWNER, *(name for name, _desc, _color in _DERIVATIVES)), ArtifactStatus.CURRENT
)


def store_character_with_derivatives(client: TestClient, pm: ProjectManager, *, owner_description: str) -> str:
    """Create character 王 with an asset image and two derivative images in source project source, store through ``/assets/from-project``, return library id."""

    pm.create_project("source")
    pm.create_project_metadata("source", "Source", "Anime", "narration")
    pm.add_character("source", _OWNER, owner_description)
    pm.install_asset_sheet_bytes(
        "character", "source", _OWNER, f"characters/{_OWNER}.png", solid_png_bytes((30, 30, 30))
    )
    source_dir = pm.get_project_path("source")
    table = {}
    for derivative_name, description, color in _DERIVATIVES:
        relative = derivative_sheet_relative_path(_OWNER, derivative_name)
        (source_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        (source_dir / relative).write_bytes(solid_png_bytes(color))
        table[derivative_name] = {"description": description, "character_sheet": relative}
    pm.update_asset_entry("character", "source", _OWNER, lambda entry: entry.__setitem__(DERIVATIVES_FIELD, table))
    response = client.post(
        "/api/v1/assets/from-project",
        json={"project_name": "source", "resource_type": "character", "resource_id": _OWNER},
    )
    assert response.status_code == 200, response.text
    return response.json()["asset"]["id"]


def character_sheet_statuses(pm: ProjectManager, project_name: str, owner: str) -> dict[str, ArtifactStatus]:
    """Current freshness of character images (keyed by character name) and each derivative image (keyed by derivative name)."""

    project_dir = pm.get_project_path(project_name)
    resolver = ArtifactCurrencyResolver(project_dir)
    entry = pm.load_project(project_name)["characters"][owner]
    statuses = {
        owner: resolver.compare(
            ArtifactKey.asset_sheet("character", owner), artifact_path=entry["character_sheet"]
        ).status
    }
    for derivative_name, derivative in entry[DERIVATIVES_FIELD].items():
        statuses[derivative_name] = resolver.compare(
            derivative_artifact_key(owner, derivative_name), artifact_path=derivative["character_sheet"]
        ).status
    return statuses
