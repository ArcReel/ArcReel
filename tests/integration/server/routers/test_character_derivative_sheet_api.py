"""Character derivative-image HTTP: enqueue, exposed staleness, version lists/rollback, deletion cascades.

Use real ``ProjectManager`` and manifests: endpoint predicates rely on disk files/version snapshots/manifest
entries. Faking any layer no longer proves real paths work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.artifact_manifest import ProjectArtifactManifestAdapter
from lib.config.resolver import ProviderModel
from lib.project.asset_derivatives import (
    derivative_artifact_key,
    derivative_sheet_dir,
    derivative_sheet_relative_path,
    derivative_version_dir,
)
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import characters, generate, versions
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import use_generation_queue
from tests.integration.server.derivative_sheet_support import (
    RESULT_IMAGE_RGB,
    read_project_json,
    run_derivative_generation,
    seed_derivative_project,
    solid_png_bytes,
    write_two_tone_png,
)

_SHEET_PATH = derivative_sheet_relative_path("阿岚", "战斗装")
_ARTIFACT_KEY = derivative_artifact_key("阿岚", "战斗装")
_VERSION_DIR = derivative_version_dir("阿岚")


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_task(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "deduped": False}


class _FakeConfigResolver:
    """i2i slot resolution returns configured providers only; capability gates have separate tests."""

    def __init__(self, _session_factory: Any) -> None:
        pass

    async def resolve_image_backend(self, project, payload=None, *, generation_type=None) -> ProviderModel:
        del project, payload, generation_type
        return ProviderModel("dashscope", "qwen-image-2.0")


def _client(monkeypatch, pm, *, queue: _FakeQueue | None = None) -> TestClient:
    for module in (characters, generate, versions):
        monkeypatch.setattr(module, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generate, "ConfigResolver", _FakeConfigResolver)
    use_generation_queue(monkeypatch, queue or _FakeQueue())
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    for module in (characters, generate, versions):
        app.include_router(module.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


class TestDerivativeGenerationEnqueue:
    def test_enqueues_a_derivative_task_addressed_by_the_compound_id(self, tmp_path, monkeypatch):
        pm, _project_path = seed_derivative_project(tmp_path)
        queue = _FakeQueue()
        with _client(monkeypatch, pm, queue=queue) as client:
            response = client.post("/api/v1/projects/demo/generate/character/阿岚/derivatives/战斗装")

        assert response.status_code == 200, response.text
        assert response.json()["task_id"] == "task-1"
        assert queue.calls[0]["task_type"] == "character_derivative"
        assert queue.calls[0]["resource_id"] == "阿岚/战斗装"
        assert queue.calls[0]["media_type"] == "image"

    @pytest.mark.parametrize(
        ("locale", "fragment"),
        [("zh", "资产图"), ("en", "asset sheet"), ("vi", "hình tài sản")],
    )
    def test_ontology_without_a_sheet_is_refused_in_every_locale(self, tmp_path, monkeypatch, locale, fragment):
        pm, _project_path = seed_derivative_project(tmp_path, with_owner_sheet=False)
        queue = _FakeQueue()
        with _client(monkeypatch, pm, queue=queue) as client:
            response = client.post(
                "/api/v1/projects/demo/generate/character/阿岚/derivatives/战斗装",
                headers={"Accept-Language": locale},
            )

        assert response.status_code == 400, response.text
        assert fragment in response.json()["detail"]
        assert queue.calls == []

    def test_unknown_derivative_is_404(self, tmp_path, monkeypatch):
        pm, _project_path = seed_derivative_project(tmp_path)
        with _client(monkeypatch, pm) as client:
            response = client.post("/api/v1/projects/demo/generate/character/阿岚/derivatives/无此衍生")
        assert response.status_code == 404


class TestDerivativeStatusEndpoint:
    def test_reports_the_sheet_and_flips_to_stale_when_the_ontology_changes(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        run_derivative_generation(pm, project_path, monkeypatch)

        with _client(monkeypatch, pm) as client:
            fresh = client.get("/api/v1/projects/demo/characters/阿岚/derivatives")
            write_two_tone_png(project_path / "characters/阿岚.png", left=(10, 10, 10), right=(240, 240, 240))
            stale = client.get("/api/v1/projects/demo/characters/阿岚/derivatives")
            (project_path / _SHEET_PATH).unlink()
            missing = client.get("/api/v1/projects/demo/characters/阿岚/derivatives")

        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["derivatives"]["战斗装"] == {
            "description": "换上黑色重甲",
            "character_sheet": _SHEET_PATH,
            "stale": False,
            "artifact_status": "current",
        }
        assert stale.json()["derivatives"]["战斗装"]["stale"] is True
        assert missing.json()["derivatives"]["战斗装"]["artifact_status"] == "missing"

    def test_an_ungenerated_derivative_has_no_sheet_and_is_not_stale(self, tmp_path, monkeypatch):
        pm, _project_path = seed_derivative_project(tmp_path)
        with _client(monkeypatch, pm) as client:
            response = client.get("/api/v1/projects/demo/characters/阿岚/derivatives")
        assert response.json()["derivatives"]["战斗装"] == {
            "description": "换上黑色重甲",
            "character_sheet": "",
            "stale": False,
            "artifact_status": "missing",
        }


class TestDerivativeVersionEndpoints:
    def test_two_generations_list_two_versions_and_restore_brings_the_first_back(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        first = run_derivative_generation(pm, project_path, monkeypatch)
        second = run_derivative_generation(pm, project_path, monkeypatch, result_rgb=(200, 20, 180))
        assert (project_path / _SHEET_PATH).read_bytes() == second

        with _client(monkeypatch, pm) as client:
            listed = client.get("/api/v1/projects/demo/versions/character-derivative/阿岚/战斗装")
            restored = client.post("/api/v1/projects/demo/versions/character-derivative/阿岚/战斗装/restore/1")

        assert listed.status_code == 200, listed.text
        assert listed.json()["resource_id"] == "阿岚/战斗装"
        assert len(listed.json()["versions"]) == 2
        assert restored.status_code == 200, restored.text
        assert (project_path / _SHEET_PATH).read_bytes() == first
        derivative = read_project_json(project_path)["characters"]["阿岚"]["derivatives"]["战斗装"]
        assert derivative["character_sheet"] == _SHEET_PATH


class TestDerivativeDeleteCascade:
    """Removing registrations leaves images unreachable; delete images/version snapshots/manifest entries together."""

    def _assert_purged(self, project_path: Path) -> None:
        assert not (project_path / _SHEET_PATH).exists()
        assert not (project_path / _VERSION_DIR).exists()
        assert ProjectArtifactManifestAdapter(project_path).get_entry(_ARTIFACT_KEY) is None

    def test_deleting_the_derivative_purges_its_image_versions_and_claim(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        run_derivative_generation(pm, project_path, monkeypatch)
        assert (project_path / _VERSION_DIR).is_dir()

        with _client(monkeypatch, pm) as client:
            response = client.delete("/api/v1/projects/demo/characters/阿岚/derivatives/战斗装")

        assert response.status_code == 200, response.text
        self._assert_purged(project_path)
        assert read_project_json(project_path)["characters"]["阿岚"]["derivatives"] == {}

    def test_deleting_the_character_purges_every_derivative_it_owned(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        run_derivative_generation(pm, project_path, monkeypatch)

        with _client(monkeypatch, pm) as client:
            response = client.delete("/api/v1/projects/demo/characters/阿岚")

        assert response.status_code == 200, response.text
        self._assert_purged(project_path)
        assert read_project_json(project_path)["characters"] == {}
        # Base images and derivatives enter the same deletion record.
        assert not (project_path / "characters/阿岚.png").exists()

    def test_purging_one_derivative_leaves_its_sibling_untouched(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        run_derivative_generation(pm, project_path, monkeypatch)

        def _add_sibling(entry: dict[str, Any]) -> None:
            entry["derivatives"]["便装"] = {"description": "布衣", "character_sheet": ""}

        pm.update_asset_entry("character", "demo", "阿岚", _add_sibling)
        sibling_sheet = project_path / derivative_sheet_relative_path("阿岚", "便装")
        sibling_sheet.write_bytes(solid_png_bytes(RESULT_IMAGE_RGB))

        with _client(monkeypatch, pm) as client:
            response = client.delete("/api/v1/projects/demo/characters/阿岚/derivatives/战斗装")

        assert response.status_code == 200, response.text
        assert not (project_path / _SHEET_PATH).exists()
        assert sibling_sheet.is_file()


class TestDerivativeRenameCascade:
    """All three derivative coordinates contain names; renames move images/history/manifest keys together.

    More than files at new paths, state must still report ``stale: False``; only realigned images/claims/
    canonical state under new IDs can yield this answer.
    """

    def _assert_relocated(self, project_path: Path, digest: str, *, owner: str, derivative: str, sheet: bytes) -> None:
        new_sheet = project_path / derivative_sheet_relative_path(owner, derivative)
        assert new_sheet.read_bytes() == sheet
        assert not (project_path / _SHEET_PATH).exists()
        entry = ProjectArtifactManifestAdapter(project_path).get_entry(derivative_artifact_key(owner, derivative))
        assert entry is not None
        assert entry.artifact_path == derivative_sheet_relative_path(owner, derivative)
        # Basis digests are immutable evidence: renames move keys/paths without rebuilding bases.
        assert entry.basis_digest == digest
        assert ProjectArtifactManifestAdapter(project_path).get_entry(_ARTIFACT_KEY) is None

    def _digest(self, project_path: Path) -> str:
        entry = ProjectArtifactManifestAdapter(project_path).get_entry(_ARTIFACT_KEY)
        assert entry is not None
        return entry.basis_digest

    def test_renaming_the_derivative_moves_its_image_versions_and_claim(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        sheet = run_derivative_generation(pm, project_path, monkeypatch)
        digest = self._digest(project_path)

        with _client(monkeypatch, pm) as client:
            renamed = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives/战斗装/rename",
                json={"new_name": "夜行装"},
            )
            listed = client.get("/api/v1/projects/demo/versions/character-derivative/阿岚/夜行装")
            status = client.get("/api/v1/projects/demo/characters/阿岚/derivatives")

        assert renamed.status_code == 200, renamed.text
        self._assert_relocated(project_path, digest, owner="阿岚", derivative="夜行装", sheet=sheet)
        assert [version["version"] for version in listed.json()["versions"]] == [1]
        assert (project_path / listed.json()["versions"][0]["file"]).is_file()
        # Names enter basis digests, so renames become stale like base-image renames; rekeying moves keys
        # and paths without rebuilding immutable evidence. Reporting stale rather than absent registration proves
        # manifest entries exist under new keys, pointing to new paths.
        assert status.json()["derivatives"]["夜行装"] == {
            "description": "换上黑色重甲",
            "character_sheet": derivative_sheet_relative_path("阿岚", "夜行装"),
            "stale": True,
            "artifact_status": "stale",
        }

    def test_renaming_the_character_carries_its_derivatives_along(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        sheet = run_derivative_generation(pm, project_path, monkeypatch)
        digest = self._digest(project_path)

        with _client(monkeypatch, pm) as client:
            renamed = client.post("/api/v1/projects/demo/characters/阿岚/rename", json={"new_name": "阿柳"})
            listed = client.get("/api/v1/projects/demo/versions/character-derivative/阿柳/战斗装")
            status = client.get("/api/v1/projects/demo/characters/阿柳/derivatives")

        assert renamed.status_code == 200, renamed.text
        self._assert_relocated(project_path, digest, owner="阿柳", derivative="战斗装", sheet=sheet)
        # Both old-base storage directories become obsolete.
        assert not (project_path / derivative_sheet_dir("阿岚")).exists()
        assert not (project_path / _VERSION_DIR).exists()
        assert [version["version"] for version in listed.json()["versions"]] == [1]
        assert (project_path / listed.json()["versions"][0]["file"]).is_file()
        assert status.json()["derivatives"]["战斗装"]["character_sheet"] == derivative_sheet_relative_path(
            "阿柳", "战斗装"
        )

    def test_regenerating_after_a_rename_continues_the_moved_history(self, tmp_path, monkeypatch):
        """Generate again after renaming: versions reach 2 rather than restart at 1, clearing staleness.

        This directly proves all three coordinates moved: histories continue, new images use new paths,
        and new-key claims realign with canonical state.
        """
        pm, project_path = seed_derivative_project(tmp_path)
        run_derivative_generation(pm, project_path, monkeypatch)

        with _client(monkeypatch, pm) as client:
            renamed = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives/战斗装/rename",
                json={"new_name": "夜行装"},
            )
        assert renamed.status_code == 200, renamed.text

        again = run_derivative_generation(pm, project_path, monkeypatch, derivative="夜行装", result_rgb=(5, 150, 250))

        with _client(monkeypatch, pm) as client:
            listed = client.get("/api/v1/projects/demo/versions/character-derivative/阿岚/夜行装")
            status = client.get("/api/v1/projects/demo/characters/阿岚/derivatives")

        assert [version["version"] for version in listed.json()["versions"]] == [1, 2]
        assert (project_path / derivative_sheet_relative_path("阿岚", "夜行装")).read_bytes() == again
        assert status.json()["derivatives"]["夜行装"]["stale"] is False

    def test_a_renamed_derivative_can_still_be_restored_to_its_earlier_version(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        first = run_derivative_generation(pm, project_path, monkeypatch)
        run_derivative_generation(pm, project_path, monkeypatch, result_rgb=(200, 20, 180))

        with _client(monkeypatch, pm) as client:
            renamed = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives/战斗装/rename",
                json={"new_name": "夜行装"},
            )
            restored = client.post("/api/v1/projects/demo/versions/character-derivative/阿岚/夜行装/restore/1")

        assert renamed.status_code == 200, renamed.text
        assert restored.status_code == 200, restored.text
        assert (project_path / derivative_sheet_relative_path("阿岚", "夜行装")).read_bytes() == first

    def test_renaming_onto_an_occupied_image_is_refused_and_changes_nothing(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        sheet = run_derivative_generation(pm, project_path, monkeypatch)
        orphan = project_path / derivative_sheet_relative_path("阿岚", "夜行装")
        orphan.write_bytes(solid_png_bytes((1, 2, 3)))

        with _client(monkeypatch, pm) as client:
            response = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives/战斗装/rename",
                json={"new_name": "夜行装"},
            )

        assert response.status_code == 409, response.text
        assert (project_path / _SHEET_PATH).read_bytes() == sheet
        assert orphan.read_bytes() == solid_png_bytes((1, 2, 3))
        assert "战斗装" in read_project_json(project_path)["characters"]["阿岚"]["derivatives"]
        assert ProjectArtifactManifestAdapter(project_path).get_entry(_ARTIFACT_KEY) is not None

    def test_renaming_the_character_leaves_a_sibling_derivative_addressable(self, tmp_path, monkeypatch):
        pm, project_path = seed_derivative_project(tmp_path)
        run_derivative_generation(pm, project_path, monkeypatch)

        def _add_sibling(entry: dict[str, Any]) -> None:
            entry["derivatives"]["便装"] = {"description": "布衣", "character_sheet": ""}

        pm.update_asset_entry("character", "demo", "阿岚", _add_sibling)
        # Intermediate files persisted before fields were written must not retain old names after base renames.
        (project_path / derivative_sheet_relative_path("阿岚", "便装")).write_bytes(solid_png_bytes((9, 9, 9)))

        with _client(monkeypatch, pm) as client:
            renamed = client.post("/api/v1/projects/demo/characters/阿岚/rename", json={"new_name": "阿柳"})

        assert renamed.status_code == 200, renamed.text
        assert (project_path / derivative_sheet_relative_path("阿柳", "便装")).read_bytes() == solid_png_bytes(
            (9, 9, 9)
        )
        assert not (project_path / derivative_sheet_dir("阿岚")).exists()
