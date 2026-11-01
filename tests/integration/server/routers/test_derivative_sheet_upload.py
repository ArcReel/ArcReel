"""衍生资产图上传即成品：上传替换当前版本，依据只由图字节决定，切回生成版本后回到按生成输入判定。

用真 ``ProjectManager``、真版本记录与真产物清单：判据落在盘上的图、版本快照与清单条目上。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactStatus, ProjectArtifactManifestAdapter
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.project.asset_derivatives import derivative_artifact_key, derivative_sheet_relative_path
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import characters, derivative_uploads, versions
from server.services.currency.asset_regeneration_impact import asset_regeneration_impact
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import use_project_manager
from tests.integration.server.derivative_sheet_support import (
    read_project_json,
    run_derivative_generation,
    seed_derivative_project,
    solid_png_bytes,
    write_two_tone_png,
)

_SHEET_PATH = derivative_sheet_relative_path("阿岚", "战斗装")
_KEY = derivative_artifact_key("阿岚", "战斗装")
_UPLOAD_URL = "/api/v1/projects/demo/characters/阿岚/derivatives/战斗装/upload"
_STATUS_URL = "/api/v1/projects/demo/characters/阿岚/derivatives"


def _client(monkeypatch, pm: ProjectManager) -> TestClient:
    use_project_manager(monkeypatch, pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    for module in (characters, derivative_uploads, versions):
        app.include_router(module.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


def _upload(client: TestClient, content: bytes, filename: str = "armor.png", url: str = _UPLOAD_URL):
    return client.post(url, files={"file": (filename, BytesIO(content), "application/octet-stream")})


def _status(project_path: Path) -> ArtifactStatus:
    return ArtifactCurrencyResolver(project_path).compare(_KEY, artifact_path=_SHEET_PATH).status


def _regenerate_owner_sheet(project_path: Path) -> None:
    """本体资产图换了字节：生成的衍生图以它为唯一输入，按生成输入判定的会随之过期。"""
    write_two_tone_png(project_path / "characters/阿岚.png", left=(10, 10, 10), right=(240, 240, 240))


def _rewrite_derivative_description(pm: ProjectManager) -> None:
    pm.update_asset_entry(
        "character",
        "demo",
        "阿岚",
        lambda entry: entry["derivatives"]["战斗装"].update(description="换上银色轻甲"),
    )


def _history(project_path: Path) -> dict:
    return VersionManager(project_path).get_versions(CHARACTER_DERIVATIVE_RESOURCE_TYPE, "阿岚/战斗装")


class TestUploadReplacesTheDerivativeSheet:
    def test_upload_selects_a_manual_upload_version_and_keeps_the_description(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        uploaded = solid_png_bytes((200, 120, 10))
        with _client(monkeypatch, pm) as client:
            response = _upload(client, uploaded)
            listed = client.get(_STATUS_URL)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["path"] == _SHEET_PATH
        assert body["version"] == 1
        assert _SHEET_PATH in body["asset_fingerprints"]
        assert (project_path / _SHEET_PATH).read_bytes() == uploaded
        derivative = read_project_json(project_path)["characters"]["阿岚"]["derivatives"]["战斗装"]
        assert derivative == {"description": "换上黑色重甲", "character_sheet": _SHEET_PATH}
        history = _history(project_path)
        assert history["current_version"] == 1
        assert history["versions"][0]["source"] == MANUAL_UPLOAD_VERSION_SOURCE
        assert history["versions"][0]["original_filename"] == "armor.png"
        entry = ProjectArtifactManifestAdapter(project_path).get_entry(_KEY)
        assert entry is not None
        assert entry.artifact_path == _SHEET_PATH
        assert listed.json()["derivatives"]["战斗装"]["artifact_status"] == "current"

    def test_upload_does_not_need_the_owner_sheet_or_a_description(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path, description="", with_owner_sheet=False)
        with _client(monkeypatch, pm) as client:
            response = _upload(client, solid_png_bytes((10, 10, 200)))

        assert response.status_code == 200, response.text
        assert _status(project_path) is ArtifactStatus.CURRENT

    def test_a_jpeg_upload_lands_as_png_at_the_canonical_path(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        buffer = BytesIO()
        Image.new("RGB", (40, 30), (90, 30, 160)).save(buffer, format="JPEG")
        with _client(monkeypatch, pm) as client:
            response = _upload(client, buffer.getvalue(), filename="armor.jpg")

        assert response.status_code == 200, response.text
        with Image.open(project_path / _SHEET_PATH) as stored:
            assert stored.format == "PNG"
            assert stored.size == (40, 30)

    @pytest.mark.parametrize(
        ("url", "filename", "content", "status_code"),
        [
            (_UPLOAD_URL.replace("战斗装", "无此衍生"), "armor.png", solid_png_bytes((1, 2, 3)), 404),
            (_UPLOAD_URL.replace("阿岚", "无此角色"), "armor.png", solid_png_bytes((1, 2, 3)), 404),
            (_UPLOAD_URL, "armor.gif", solid_png_bytes((1, 2, 3)), 400),
            (_UPLOAD_URL, "armor.png", b"not an image", 400),
        ],
    )
    def test_rejected_uploads_leave_no_sheet_and_no_version(
        self, tmp_path, monkeypatch, url, filename, content, status_code
    ):
        pm, project_path = seed_derivative_project(tmp_path)
        with _client(monkeypatch, pm) as client:
            response = _upload(client, content, filename=filename, url=url)

        assert response.status_code == status_code, response.text
        assert not (project_path / _SHEET_PATH).exists()
        assert _history(project_path)["versions"] == []
        assert list((project_path / "characters").rglob(".*.tmp*")) == []


class TestUploadedDerivativeCurrency:
    def test_uploaded_sheet_stays_current_when_the_owner_sheet_or_description_changes(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        with _client(monkeypatch, pm) as client:
            assert _upload(client, solid_png_bytes((200, 120, 10))).status_code == 200

            _regenerate_owner_sheet(project_path)
            _rewrite_derivative_description(pm)
            listed = client.get(_STATUS_URL)

        assert _status(project_path) is ArtifactStatus.CURRENT
        assert listed.json()["derivatives"]["战斗装"]["stale"] is False

    def test_regenerating_the_owner_counts_generated_derivatives_but_not_uploaded_ones(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        run_derivative_generation(pm, project_path, monkeypatch)
        assert asset_regeneration_impact(pm, "demo", "character", "阿岚").derivatives == 1

        with _client(monkeypatch, pm) as client:
            assert _upload(client, solid_png_bytes((200, 120, 10))).status_code == 200

        assert asset_regeneration_impact(pm, "demo", "character", "阿岚").derivatives == 0

    def test_restoring_the_generated_version_returns_to_its_generation_basis(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        generated = run_derivative_generation(pm, project_path, monkeypatch)
        uploaded = solid_png_bytes((200, 120, 10))
        with _client(monkeypatch, pm) as client:
            assert _upload(client, uploaded).status_code == 200
            assert _history(project_path)["current_version"] == 2

            restored = client.post("/api/v1/projects/demo/versions/character-derivative/阿岚/战斗装/restore/1")
            assert restored.status_code == 200, restored.text
            assert (project_path / _SHEET_PATH).read_bytes() == generated
            assert _status(project_path) is ArtifactStatus.CURRENT
            _regenerate_owner_sheet(project_path)
            assert _status(project_path) is ArtifactStatus.STALE

            reclaimed = client.post("/api/v1/projects/demo/versions/character-derivative/阿岚/战斗装/restore/2")

        assert reclaimed.status_code == 200, reclaimed.text
        assert (project_path / _SHEET_PATH).read_bytes() == uploaded
        assert _status(project_path) is ArtifactStatus.CURRENT

    def test_generating_after_an_upload_returns_to_the_generation_basis(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        with _client(monkeypatch, pm) as client:
            assert _upload(client, solid_png_bytes((200, 120, 10))).status_code == 200

        run_derivative_generation(pm, project_path, monkeypatch)
        assert _status(project_path) is ArtifactStatus.CURRENT
        _rewrite_derivative_description(pm)
        assert _status(project_path) is ArtifactStatus.STALE

    def test_an_externally_replaced_upload_falls_back_to_the_generation_basis(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        with _client(monkeypatch, pm) as client:
            assert _upload(client, solid_png_bytes((200, 120, 10))).status_code == 200

        (project_path / _SHEET_PATH).write_bytes(solid_png_bytes((5, 5, 5)))
        assert _status(project_path) is ArtifactStatus.STALE
