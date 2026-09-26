"""从全局资产库应用到项目的资产图即成品：清单按图本身认领，时效不随描述与画风变化。"""

import asyncio
import json
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.artifacts.generation_input import InputRefused, project_input_observation, storyboard_image_input
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.project.asset_derivatives import derivative_artifact_key, derivative_sheet_relative_path
from lib.project.asset_types import DERIVATIVES_FIELD
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from lib.workflow.workflow_state import WorkflowStateService
from server.routers import assets, versions
from server.services.currency.upload_finalize import install_manual_asset_sheet_upload
from server.services.tasks import formal_image_commit, generation_tasks
from tests.integration.server.asset_library_support import (
    LIBRARY_SHEETS_ALL_CURRENT,
    character_sheet_statuses,
    store_character_with_derivatives,
)
from tests.integration.server.derivative_sheet_support import run_derivative_generation, solid_png_bytes
from tests.integration.server.services.tasks.generation_tasks_support import fake_resolve_ctx

_KEY = ArtifactKey.asset_sheet("character", "Alice")


@pytest.fixture
def library(assets_env) -> tuple[TestClient, ProjectManager]:
    """资产库路由客户端，与已建好的目标项目 demo。"""

    pm = assets_env["pm"]
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    return assets_env["client"], pm


def _library_asset(client: TestClient, *, name: str, description: str, content: bytes) -> str:
    response = client.post(
        "/api/v1/assets",
        data={"type": "character", "name": name, "description": description},
        files={"image": (f"{name}.png", content, "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()["asset"]["id"]


def _apply(client: TestClient, asset_id: str, *, policy: str = "skip") -> list[dict]:
    response = client.post(
        "/api/v1/assets/apply-to-project",
        json={"asset_ids": [asset_id], "target_project": "demo", "conflict_policy": policy},
    )
    assert response.status_code == 200, response.text
    return response.json()["succeeded"]


def _status(project_dir: Path, key: ArtifactKey, sheet_path: str) -> ArtifactStatus:
    return ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=sheet_path).status


def test_sheet_applied_without_description_is_claimed_and_usable_as_a_reference(library):
    client, pm = library
    asset_id = _library_asset(client, name="Alice", description="", content=solid_png_bytes((200, 10, 10)))

    _apply(client, asset_id)

    project_dir = pm.get_project_path("demo")
    sheet_path = pm.load_project("demo")["characters"]["Alice"]["character_sheet"]
    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(_KEY)
    assert entry is not None
    assert entry.artifact_path == sheet_path
    assert _status(project_dir, _KEY, sheet_path) is ArtifactStatus.CURRENT
    status = WorkflowStateService(pm).get_status("demo")
    assert status.artifacts["asset_sheets"]["character"]["current_ids"] == ["Alice"]

    script = {
        "episode": 1,
        "content_mode": "narration",
        "segments": [
            {
                "segment_id": "E1S01",
                "duration_seconds": 4,
                "characters_in_segment": ["Alice"],
                "image_prompt": "Alice 站在门口",
            }
        ],
    }
    assembled = storyboard_image_input(
        pm.load_project("demo"),
        script,
        episode=1,
        resource_id="E1S01",
        observation=project_input_observation(project_dir),
    )
    assert not isinstance(assembled, InputRefused)
    assert [reference.artifact_path for reference in assembled.references] == [sheet_path]


def _restyle(project: dict) -> None:
    project["style"] = "Photographic"
    project["style_description"] = "胶片颗粒"


def test_applied_sheet_stays_current_when_description_and_project_style_change(library):
    client, pm = library
    asset_id = _library_asset(client, name="Alice", description="银发少女", content=solid_png_bytes((10, 200, 10)))
    _apply(client, asset_id)
    project_dir = pm.get_project_path("demo")
    sheet_path = pm.load_project("demo")["characters"]["Alice"]["character_sheet"]

    pm.update_asset_entry("character", "demo", "Alice", lambda entry: entry.update(description="黑发少年"))
    assert _status(project_dir, _KEY, sheet_path) is ArtifactStatus.CURRENT

    pm.update_project("demo", _restyle)
    assert _status(project_dir, _KEY, sheet_path) is ArtifactStatus.CURRENT


_ALL_CURRENT = LIBRARY_SHEETS_ALL_CURRENT


def _rewrite_descriptions(entry: dict) -> None:
    entry["description"] = "黑发老者"
    for derivative in entry[DERIVATIVES_FIELD].values():
        derivative["description"] = "改写后的衍生"


def _project_character_with_uploaded_sheet(pm: ProjectManager) -> None:
    pm.add_character("demo", "王", "项目里的王")
    install_manual_asset_sheet_upload(
        project_manager=pm,
        project_name="demo",
        asset_type="character",
        name="王",
        sheet_path="characters/王.png",
        content=solid_png_bytes((5, 5, 200)),
        original_filename="王.png",
    )


@pytest.mark.parametrize("owner_description", ["", "白衣少年"])
def test_round_trip_derivative_sheets_stay_current_when_descriptions_and_style_change(library, owner_description):
    client, pm = library
    asset_id = store_character_with_derivatives(client, pm, owner_description=owner_description)

    _apply(client, asset_id)
    assert character_sheet_statuses(pm, "demo", "王") == _ALL_CURRENT

    pm.update_asset_entry("character", "demo", "王", _rewrite_descriptions)
    pm.update_project("demo", _restyle)
    assert character_sheet_statuses(pm, "demo", "王") == _ALL_CURRENT


class _SheetGenerator:
    """按生产口径经正式图活化回调提交一张生成的资产图。"""

    def __init__(self, project_dir: Path, content: bytes) -> None:
        self.versions = VersionManager(project_dir)
        self._project_dir = project_dir
        self._content = content

    async def generate_image_async(self, *, resource_type, resource_id, commit_formal_output, **_kwargs):
        current = self._project_dir / resource_type / f"{resource_id}.png"
        staged = self._project_dir / resource_type / f".{resource_id}.staged.png"
        staged.write_bytes(self._content)
        return current, commit_formal_output(staged, current, {})


async def test_regenerating_the_owner_sheet_returns_it_to_its_generation_basis(library, monkeypatch):
    client, pm = library
    _apply(client, store_character_with_derivatives(client, pm, owner_description="白衣少年"))
    project_dir = pm.get_project_path("demo")
    resolve_ctx = fake_resolve_ctx(_SheetGenerator(project_dir, solid_png_bytes((90, 90, 90))))
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", resolve_ctx)
    monkeypatch.setattr(formal_image_commit, "resolve_generation_context", resolve_ctx)

    await generation_tasks.execute_character_task("demo", "王", {})
    assert character_sheet_statuses(pm, "demo", "王") == _ALL_CURRENT

    pm.update_asset_entry("character", "demo", "王", lambda entry: entry.update(description="黑发老者"))
    assert character_sheet_statuses(pm, "demo", "王") == {**_ALL_CURRENT, "王": ArtifactStatus.STALE}


def test_regenerating_a_derivative_sheet_returns_it_to_its_generation_basis(library, monkeypatch):
    client, pm = library
    _apply(client, store_character_with_derivatives(client, pm, owner_description="白衣少年"))

    run_derivative_generation(pm, pm.get_project_path("demo"), monkeypatch, owner="王", derivative="战斗装")
    assert character_sheet_statuses(pm, "demo", "王") == _ALL_CURRENT

    def _rewrite(entry: dict) -> None:
        entry[DERIVATIVES_FIELD]["战斗装"]["description"] = "银甲"
        entry[DERIVATIVES_FIELD]["便装"]["description"] = "长衫"

    pm.update_asset_entry("character", "demo", "王", _rewrite)
    assert character_sheet_statuses(pm, "demo", "王") == {**_ALL_CURRENT, "战斗装": ArtifactStatus.STALE}


def test_a_failed_apply_rolls_back_files_metadata_versions_and_claims(library, monkeypatch):
    client, pm = library
    asset_id = store_character_with_derivatives(client, pm, owner_description="白衣少年")
    _project_character_with_uploaded_sheet(pm)
    project_dir = pm.get_project_path("demo")
    adapter = ProjectArtifactManifestAdapter(project_dir)

    def _durable_state() -> dict:
        return {
            "project": pm.load_project("demo"),
            "sheet": (project_dir / "characters/王.png").read_bytes(),
            "versions": (project_dir / "versions/versions.json").read_bytes(),
            "snapshots": sorted(path for path in (project_dir / "versions").rglob("*") if path.is_file()),
            "claim": adapter.get_entry(ArtifactKey.asset_sheet("character", "王")),
        }

    before = _durable_state()

    def _fail_claim_commit(*_args, **_kwargs):
        raise RuntimeError("injected claim failure")

    monkeypatch.setattr(assets, "register_artifact_entries_atomically", _fail_claim_commit)
    with pytest.raises(RuntimeError, match="injected claim failure"):
        _apply(client, asset_id, policy="overwrite")

    assert _durable_state() == before
    assert not (project_dir / derivative_sheet_relative_path("王", "战斗装")).exists()
    assert adapter.get_entry(derivative_artifact_key("王", "战斗装")) is None


def test_overwrite_keeps_the_existing_history_and_selects_the_applied_sheet(library):
    client, pm = library
    asset_id = store_character_with_derivatives(client, pm, owner_description="白衣少年")
    _project_character_with_uploaded_sheet(pm)

    _apply(client, asset_id, policy="overwrite")

    history = VersionManager(pm.get_project_path("demo")).get_versions("characters", "王")
    assert [record["source"] for record in history["versions"]] == [MANUAL_UPLOAD_VERSION_SOURCE] * 2
    assert history["current_version"] == 2
    pm.update_asset_entry("character", "demo", "王", _rewrite_descriptions)
    pm.update_project("demo", _restyle)
    assert character_sheet_statuses(pm, "demo", "王") == _ALL_CURRENT


def test_rename_claims_the_applied_sheets_under_the_renamed_owner(library):
    client, pm = library
    asset_id = store_character_with_derivatives(client, pm, owner_description="白衣少年")
    pm.add_character("demo", "王", "项目里的王")

    assert _apply(client, asset_id, policy="rename") == [{"id": asset_id, "name": "王 (2)"}]

    pm.update_asset_entry("character", "demo", "王 (2)", _rewrite_descriptions)
    pm.update_project("demo", _restyle)
    assert character_sheet_statuses(pm, "demo", "王 (2)") == {
        "王 (2)": ArtifactStatus.CURRENT,
        "战斗装": ArtifactStatus.CURRENT,
        "便装": ArtifactStatus.CURRENT,
    }


def test_skip_leaves_the_existing_sheet_and_history_untouched(library):
    client, pm = library
    asset_id = store_character_with_derivatives(client, pm, owner_description="白衣少年")
    _project_character_with_uploaded_sheet(pm)
    project_dir = pm.get_project_path("demo")
    versions_before = (project_dir / "versions/versions.json").read_bytes()
    sheet_before = (project_dir / "characters/王.png").read_bytes()

    response = client.post(
        "/api/v1/assets/apply-to-project",
        json={"asset_ids": [asset_id], "target_project": "demo", "conflict_policy": "skip"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["skipped"] == [{"id": asset_id, "name": "王"}]
    assert (project_dir / "versions/versions.json").read_bytes() == versions_before
    assert (project_dir / "characters/王.png").read_bytes() == sheet_before
    assert not pm.load_project("demo")["characters"]["王"].get(DERIVATIVES_FIELD)


def test_overwrite_onto_an_nfd_key_records_the_history_under_the_existing_key(library):
    client, pm = library
    owner_nfc, owner_nfd = unicodedata.normalize("NFC", "Café"), unicodedata.normalize("NFD", "Café")
    derivative_nfc, derivative_nfd = unicodedata.normalize("NFC", "Robé"), unicodedata.normalize("NFD", "Robé")
    pm.create_project("source")
    pm.create_project_metadata("source", "Source", "Anime", "narration")
    pm.add_character("source", owner_nfc, "白衣少年")
    pm.install_asset_sheet_bytes(
        "character", "source", owner_nfc, f"characters/{owner_nfc}.png", solid_png_bytes((30, 30, 30))
    )
    relative = derivative_sheet_relative_path(owner_nfc, derivative_nfc)
    (pm.get_project_path("source") / relative).parent.mkdir(parents=True, exist_ok=True)
    (pm.get_project_path("source") / relative).write_bytes(solid_png_bytes((120, 0, 0)))
    pm.update_asset_entry(
        "character",
        "source",
        owner_nfc,
        lambda entry: entry.__setitem__(
            DERIVATIVES_FIELD, {derivative_nfc: {"description": "黑甲", "character_sheet": relative}}
        ),
    )
    stored = client.post(
        "/api/v1/assets/from-project",
        json={"project_name": "source", "resource_type": "character", "resource_id": owner_nfc},
    )
    assert stored.status_code == 200, stored.text

    def _seed_nfd(project: dict) -> None:
        project["characters"][owner_nfd] = {
            "description": "项目里的",
            DERIVATIVES_FIELD: {derivative_nfd: {"description": "旧甲", "character_sheet": ""}},
        }

    pm.update_project("demo", _seed_nfd)

    _apply(client, stored.json()["asset"]["id"], policy="overwrite")

    history = json.loads((pm.get_project_path("demo") / "versions/versions.json").read_text(encoding="utf-8"))
    assert list(history["characters"]) == [owner_nfd]
    assert list(history[CHARACTER_DERIVATIVE_RESOURCE_TYPE]) == [f"{owner_nfd}/{derivative_nfd}"]
    pm.update_asset_entry("character", "demo", owner_nfd, _rewrite_descriptions)
    pm.update_project("demo", _restyle)
    assert character_sheet_statuses(pm, "demo", owner_nfd) == {
        owner_nfd: ArtifactStatus.CURRENT,
        derivative_nfd: ArtifactStatus.CURRENT,
    }


async def test_restoring_the_applied_derivative_version_claims_it_by_the_image_again(library, monkeypatch):
    client, pm = library
    _apply(client, store_character_with_derivatives(client, pm, owner_description="白衣少年"))
    project_dir = pm.get_project_path("demo")
    applied = (project_dir / derivative_sheet_relative_path("王", "战斗装")).read_bytes()
    await asyncio.to_thread(run_derivative_generation, pm, project_dir, monkeypatch, owner="王", derivative="战斗装")
    monkeypatch.setattr(versions, "get_project_manager", lambda: pm)

    restored = await versions.restore_derivative_version("demo", "王", "战斗装", 1)

    assert (project_dir / restored["file_path"]).read_bytes() == applied
    pm.update_asset_entry("character", "demo", "王", _rewrite_descriptions)
    assert character_sheet_statuses(pm, "demo", "王") == _ALL_CURRENT
