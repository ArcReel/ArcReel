import asyncio
import hashlib
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactKey,
    ArtifactManifest,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_version_provenance import parse_typed_media_version_target
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.artifacts.visual_artifact_provenance import (
    build_uploaded_video_basis,
)
from lib.project.resource_paths import resource_relative_path
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import versions
from server.services.currency import typed_media_restore
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import add_typed_video_version
from tests.fakes import use_project_manager


@pytest.fixture(params=["videos", "reference_videos"])
def uploaded_video_history(tmp_path, monkeypatch, request):
    from lib.project.project_manager import ProjectManager

    resource_type = request.param
    reference = resource_type == "reference_videos"
    items_key, id_key, unit_id = (
        ("video_units", "unit_id", "E1U1") if reference else ("segments", "segment_id", "E1S01")
    )
    mode = "reference_video" if reference else "storyboard"
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration", extras={"generation_mode": mode})
    directory = pm.get_project_path("demo")
    relative_path = resource_relative_path(resource_type, unit_id)
    current = directory / relative_path
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "content_mode": "narration",
            "generation_mode": mode,
            items_key: [{id_key: unit_id, "duration_seconds": 4, "generated_assets": {"video_clip": relative_path}}],
        },
        "episode_1.json",
        validate=False,
    )
    vm = VersionManager(directory)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(b"uploaded")
    assert vm.add_version(resource_type, unit_id, "", source_file=current, source=MANUAL_UPLOAD_VERSION_SOURCE) == 1
    assert add_typed_video_version(directory, resource_type, unit_id, content=b"generated") == 2

    use_project_manager(monkeypatch, pm)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    endpoint = f"/api/v1/projects/demo/versions/{resource_type}/{unit_id}"
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post(f"{endpoint}/restore/2").status_code == 200
        yield client, pm, vm, resource_type, unit_id, current


@pytest.mark.parametrize("record_shape", ["upload", "backfilled", "missing_digest"])
def test_web_video_history_switches_between_upload_and_generation(uploaded_video_history, record_shape):
    client, pm, vm, resource_type, unit_id, current = uploaded_video_history
    directory = vm.project_path
    endpoint = f"/api/v1/projects/demo/versions/{resource_type}/{unit_id}"
    history = vm.sqlite.resources([(resource_type, unit_id)])
    by_version = {row["version"]: row for row in history[resource_type][unit_id]["versions"]}
    upload, generated = by_version[1], by_version[2]
    generated_basis = parse_typed_media_version_target(resource_type, generated).basis
    if record_shape == "backfilled":
        upload.update(
            artifact_video_currency=generated["artifact_video_currency"],
            execution_checkpoint_schema_version=generated["execution_checkpoint_schema_version"],
            execution_duration_seconds=generated["execution_duration_seconds"],
            execution_request_digest=generated["execution_request_digest"],
            execution_script_file="episode_9.json",
            provenance_backfilled_at="2026-11-24T00:00:00Z",
        )
    elif record_shape == "missing_digest":
        upload.pop("content_digest")
    vm.sqlite.save(history)

    listing = client.get(endpoint)
    assert listing.status_code == 200
    records = {record["version"]: record for record in listing.json()["versions"]}
    assert records[1]["restorable"] is True
    assert records[1]["presentation_available"] is True
    assert records[2]["restorable"] is True
    adapter = ProjectArtifactManifestAdapter(directory)
    key = ArtifactKey.episode_video(1, unit_id)
    assert adapter.get_entry(key).basis_digest == generated_basis.digest

    response = client.post(f"{endpoint}/restore/1")
    assert response.status_code == 200, response.json()
    assert current.read_bytes() == b"uploaded"
    assert vm.get_current_version(resource_type, unit_id) == 1
    expected_upload_basis = build_uploaded_video_basis(content_digest=hashlib.sha256(b"uploaded").hexdigest())
    assert adapter.get_entry(key).basis_digest == expected_upload_basis.digest
    saved_upload = vm.sqlite.current_record((resource_type, unit_id))
    assert saved_upload == upload

    with pm.locked_script("demo", "episode_1.json", validate=False) as script:
        if resource_type == "reference_videos":
            script["video_units"][0]["text"] = "改写参考生视频提示词。"
        else:
            script["segments"][0]["video_prompt"] = {"action": "改写分镜视频提示词。", "camera_motion": "Static"}
    artifact_path = current.relative_to(directory).as_posix()
    comparison = ArtifactCurrencyResolver(directory).compare(key, artifact_path=artifact_path)
    assert comparison.status is ArtifactStatus.CURRENT, comparison

    response = client.post(f"{endpoint}/restore/2")
    assert response.status_code == 200, response.json()
    assert current.read_bytes() == b"generated"
    assert vm.get_current_version(resource_type, unit_id) == 2
    assert adapter.get_entry(key).basis_digest == generated_basis.digest


@pytest.mark.parametrize(
    ("failure", "status_code"),
    [("corrupted", 409), ("unmanaged", 400), ("missing_owner", 400), ("duplicate_owner", 400), ("manifest", 500)],
)
def test_web_upload_video_restore_failure_preserves_formal_state(
    uploaded_video_history, monkeypatch, failure, status_code
):
    client, pm, vm, resource_type, unit_id, current = uploaded_video_history
    directory = vm.project_path
    script_path = directory / "scripts/episode_1.json"
    items_key = "video_units" if resource_type == "reference_videos" else "segments"
    snapshot = vm.get_versions(resource_type, unit_id)["versions"][0]["file"]
    if failure == "corrupted":
        (directory / snapshot).write_bytes(b"tampered")
    elif failure == "unmanaged":
        history = vm.sqlite.resources([(resource_type, unit_id)])
        history[resource_type][unit_id]["versions"][0]["file"] = current.relative_to(directory).as_posix()
        vm.sqlite.save(history)
    elif failure == "missing_owner":
        with pm.locked_script("demo", "episode_1.json", validate=False) as script:
            script[items_key] = []
    elif failure == "duplicate_owner":
        script = pm.load_script_readonly("demo", "episode_1.json")
        pm.save_script("demo", {**script, "episode": 2}, "episode_2.json", validate=False)
    else:
        register = ArtifactManifest.register_descriptor

        def fail_after_manifest_write(manifest, key, *, artifact_path, basis):
            register(manifest, key, artifact_path=artifact_path, basis=basis)
            raise RuntimeError("injected manifest failure after registration")

        monkeypatch.setattr(ArtifactManifest, "register_descriptor", fail_after_manifest_write)

    tracked_files = [script_path, directory / "project.json", current, directory / MANIFEST_FILENAME]
    history_before = vm.sqlite.snapshot()
    before = [path.read_bytes() for path in tracked_files]
    endpoint = f"/api/v1/projects/demo/versions/{resource_type}/{unit_id}/restore/1"
    response = client.post(endpoint)
    assert response.status_code == status_code, response.json()
    assert [path.read_bytes() for path in tracked_files] == before
    assert vm.sqlite.snapshot() == history_before


def test_web_upload_video_restore_rechecks_owner_after_admission(uploaded_video_history, monkeypatch):
    client, pm, vm, resource_type, unit_id, current = uploaded_video_history
    directory = vm.project_path
    owner = pm.load_script_readonly("demo", "episode_1.json")
    admission = typed_media_restore.generation_admission_lock
    tracked_files = [
        directory / "scripts/episode_1.json",
        directory / "scripts/episode_2.json",
        directory / "project.json",
        current,
        directory / MANIFEST_FILENAME,
    ]
    before_restore: list[bytes] = []
    history_before = vm.sqlite.snapshot()

    def add_duplicate():
        pm.save_script("demo", {**owner, "episode": 2}, "episode_2.json", validate=False)
        before_restore.extend(path.read_bytes() for path in tracked_files)

    @asynccontextmanager
    async def add_duplicate_after_admission(**identity):
        async with admission(**identity):
            await asyncio.to_thread(add_duplicate)
            yield

    monkeypatch.setattr(typed_media_restore, "generation_admission_lock", add_duplicate_after_admission)
    response = client.post(f"/api/v1/projects/demo/versions/{resource_type}/{unit_id}/restore/1")

    assert response.status_code == 400, response.json()
    assert [path.read_bytes() for path in tracked_files] == before_restore
    assert vm.sqlite.snapshot() == history_before
