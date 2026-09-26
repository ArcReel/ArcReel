"""从全局资产库应用到项目的用例共享的库资产构造与资产图时效读取。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.project.asset_derivatives import derivative_artifact_key, derivative_sheet_relative_path
from lib.project.asset_types import DERIVATIVES_FIELD
from lib.project.project_manager import ProjectManager
from tests.integration.server.derivative_sheet_support import solid_png_bytes

_OWNER = "王"
#: 库角色「王」带的两个衍生：名、变化描述与图的纯色。
_DERIVATIVES = (("战斗装", "黑甲", (120, 0, 0)), ("便装", "布衣", (0, 120, 0)))

#: 应用 :func:`store_character_with_derivatives` 存入的库角色后，按 :func:`character_sheet_statuses`
#: 读出的全部 current 结论。
LIBRARY_SHEETS_ALL_CURRENT = dict.fromkeys(
    (_OWNER, *(name for name, _desc, _color in _DERIVATIVES)), ArtifactStatus.CURRENT
)


def store_character_with_derivatives(client: TestClient, pm: ProjectManager, *, owner_description: str) -> str:
    """在源项目 source 里造一个带资产图和两个衍生资产图的角色「王」，经 ``/assets/from-project`` 存入资产库，返回库资产 id。"""

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
    """角色资产图（键为角色名）与它每个衍生资产图（键为衍生名）当前的时效。"""

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
