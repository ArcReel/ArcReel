"""资产图上传即成品：清单按上传本身认领，时效不随描述与画风变化。"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.artifacts.generation_input import InputRefused, project_input_observation, storyboard_image_input
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.infra.api_errors import BadRequestError
from lib.project.project_manager import ProjectManager
from lib.workflow.workflow_state import WorkflowStateService
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import files, versions
from server.services.tasks import formal_image_commit, generation_tasks
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.integration.server.derivative_sheet_support import solid_png_bytes
from tests.integration.server.services.tasks.generation_tasks_support import fake_resolve_ctx

_KEY = ArtifactKey.asset_sheet("character", "Alice")


def _client(monkeypatch, tmp_path: Path, *, description: str) -> tuple[TestClient, ProjectManager, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_character("demo", "Alice", description)
    monkeypatch.setattr(files, "get_project_manager", lambda: pm)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(files.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app), pm, pm.get_project_path("demo")


def _upload(client: TestClient, content: bytes) -> str:
    response = client.post(
        "/api/v1/projects/demo/upload/character?name=Alice",
        files={"file": ("alice.png", content, "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()["path"]


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


def _serve_generation(monkeypatch, pm: ProjectManager, generator: _SheetGenerator) -> None:
    resolve_ctx = fake_resolve_ctx(generator)
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", resolve_ctx)
    monkeypatch.setattr(formal_image_commit, "resolve_generation_context", resolve_ctx)


def _status(project_dir: Path, sheet_path: str) -> ArtifactStatus:
    return ArtifactCurrencyResolver(project_dir).compare(_KEY, artifact_path=sheet_path).status


def test_sheet_uploaded_without_description_is_claimed_and_usable_as_a_reference(tmp_path, monkeypatch):
    client, pm, project_dir = _client(monkeypatch, tmp_path, description="")
    with client:
        sheet_path = _upload(client, solid_png_bytes((200, 10, 10)))

    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(_KEY)
    assert entry is not None
    assert entry.artifact_path == sheet_path
    assert _status(project_dir, sheet_path) is ArtifactStatus.CURRENT
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


def test_uploaded_sheet_stays_current_when_description_and_project_style_change(tmp_path, monkeypatch):
    client, pm, project_dir = _client(monkeypatch, tmp_path, description="银发少女")
    with client:
        sheet_path = _upload(client, solid_png_bytes((10, 200, 10)))

    pm.update_asset_entry("character", "demo", "Alice", lambda entry: entry.update(description="黑发少年"))
    assert _status(project_dir, sheet_path) is ArtifactStatus.CURRENT

    def _restyle(project: dict) -> None:
        project["style"] = "Photographic"
        project["style_description"] = "胶片颗粒"

    pm.update_project("demo", _restyle)
    assert _status(project_dir, sheet_path) is ArtifactStatus.CURRENT


async def test_generating_after_upload_returns_the_sheet_to_its_generation_basis(tmp_path, monkeypatch):
    client, pm, project_dir = _client(monkeypatch, tmp_path, description="银发少女")
    with client:
        _upload(client, solid_png_bytes((10, 10, 200)))
    _serve_generation(monkeypatch, pm, _SheetGenerator(project_dir, solid_png_bytes((90, 90, 90))))

    result = await generation_tasks.execute_character_task("demo", "Alice", {})

    sheet_path = result["file_path"]
    assert pm.load_project("demo")["characters"]["Alice"]["character_sheet"] == sheet_path
    assert _status(project_dir, sheet_path) is ArtifactStatus.CURRENT
    pm.update_asset_entry("character", "demo", "Alice", lambda entry: entry.update(description="黑发少年"))
    assert _status(project_dir, sheet_path) is ArtifactStatus.STALE


async def test_generating_a_sheet_without_description_is_still_refused_after_an_upload(tmp_path, monkeypatch):
    client, pm, project_dir = _client(monkeypatch, tmp_path, description="")
    with client:
        sheet_path = _upload(client, solid_png_bytes((120, 30, 30)))
    _serve_generation(monkeypatch, pm, _SheetGenerator(project_dir, solid_png_bytes((90, 90, 90))))

    with pytest.raises(BadRequestError) as excinfo:
        await generation_tasks.execute_character_task("demo", "Alice", {})

    assert excinfo.value.key == "asset_description_required"
    assert _status(project_dir, sheet_path) is ArtifactStatus.CURRENT


@pytest.mark.parametrize("description", ["", "银发少女"])
def test_externally_replaced_upload_is_not_judged_by_the_upload(tmp_path, monkeypatch, description):
    client, _pm, project_dir = _client(monkeypatch, tmp_path, description=description)
    with client:
        sheet_path = _upload(client, solid_png_bytes((30, 120, 30)))

    (project_dir / sheet_path).write_bytes(solid_png_bytes((1, 2, 3)))

    assert _status(project_dir, sheet_path) is ArtifactStatus.STALE


async def test_restoring_the_uploaded_version_claims_it_by_the_upload_again(tmp_path, monkeypatch):
    client, pm, project_dir = _client(monkeypatch, tmp_path, description="银发少女")
    uploaded = solid_png_bytes((200, 200, 10))
    with client:
        _upload(client, uploaded)
    _serve_generation(monkeypatch, pm, _SheetGenerator(project_dir, solid_png_bytes((90, 90, 90))))
    await generation_tasks.execute_character_task("demo", "Alice", {})
    monkeypatch.setattr(versions, "get_project_manager", lambda: pm)
    [upload_version] = [
        record["version"]
        for record in VersionManager(project_dir).get_versions("characters", "Alice")["versions"]
        if record.get("source") == MANUAL_UPLOAD_VERSION_SOURCE
    ]

    restored = await versions.restore_resource_version("demo", "characters", "Alice", upload_version)

    sheet_path = restored["file_path"]
    assert (project_dir / sheet_path).read_bytes() == uploaded
    pm.update_asset_entry("character", "demo", "Alice", lambda entry: entry.update(description="黑发少年"))
    assert _status(project_dir, sheet_path) is ArtifactStatus.CURRENT


def test_uploaded_sheet_stays_current_after_the_asset_is_renamed(tmp_path, monkeypatch):
    client, pm, project_dir = _client(monkeypatch, tmp_path, description="银发少女")
    with client:
        _upload(client, solid_png_bytes((70, 20, 140)))

    pm.rename_asset("demo", "characters", "Alice", "Alicia")

    sheet_path = pm.load_project("demo")["characters"]["Alicia"]["character_sheet"]
    comparison = ArtifactCurrencyResolver(project_dir).compare(
        ArtifactKey.asset_sheet("character", "Alicia"), artifact_path=sheet_path
    )
    assert comparison.status is ArtifactStatus.CURRENT
