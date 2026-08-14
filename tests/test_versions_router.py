import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.api_errors import BadRequestError
from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    compose_video_artifact_basis,
)
from lib.speech_artifact_provenance import build_video_duration_basis
from lib.version_manager import VersionManager
from lib.video_artifact_facts import VideoArtifactCurrencyFacts
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import versions
from tests.auth_deps import AUTH_DEPENDENCIES

pytestmark = pytest.mark.unit


class _FakePM:
    def __init__(self):
        self.updated = []

    def get_project_path(self, project_name):
        from pathlib import Path

        return Path("/tmp") / project_name

    def _update_asset_sheet(self, asset_type, *args, on_commit=None):
        self.updated.append((asset_type, args))
        if on_commit is not None:
            on_commit(self.get_project_path(args[0]) / "project.json")

    def update_scene_asset(self, *args, **kwargs):
        self.updated.append(("storyboard", args, kwargs))

    def update_scene_asset_across_scripts(
        self, project_name, script_filenames, scene_id, asset_type, asset_path, *, on_commit=None
    ):
        for script_filename in script_filenames:
            self.update_scene_asset(project_name, script_filename, scene_id, asset_type, asset_path)
        if on_commit is not None:
            on_commit()


class _FakeVM:
    def __init__(self, project_path=None):
        self.project_path = project_path

    def get_versions(self, resource_type, resource_id):
        if resource_type == "bad":
            raise ValueError("bad type")
        return {
            "current_version": 1,
            "versions": [{"version": 1, "file": f"versions/{resource_type}/{resource_id}.png"}],
        }

    def get_version_created_at(self, resource_type, resource_id, version):
        return "2026-01-01T00:00:00+00:00"

    def get_version_metadata(self, resource_type, resource_id, version, key) -> str | None:
        raise AssertionError("version restore must not read legacy source_signature metadata")

    def restore_version(self, resource_type, resource_id, version, current_file, *, on_restore=None):
        if version == 404:
            raise FileNotFoundError("missing")
        if version == 400:
            raise ValueError("bad")
        if on_restore is not None:
            on_restore({"version": version})
        return {
            "restored_version": version,
            "current_version": version,
            "prompt": "p",
        }


class _GridPM:
    """记录剧本侧写回调用的 ProjectManager 替身，供 grids 还原用例断言「不碰剧本」。

    ``load_project`` 供还原的宫格写闸门取项目形态，默认返回一个允许宫格写入的项目。
    """

    def __init__(self, project_path, project=None):
        self.project_path = project_path
        self.update_calls = []
        self.project = (
            project
            if project is not None
            else {"content_mode": "narration", "generation_mode": "storyboard", "grid_storyboard": True}
        )

    def get_project_path(self, project_name):
        return self.project_path

    def load_project(self, project_name):
        return self.project

    def update_scene_asset(self, *args, **kwargs):
        self.update_calls.append((args, kwargs))

    def batch_update_scene_assets(self, *args, **kwargs):
        self.update_calls.append((args, kwargs))


def _typed_video_versions(project_path: Path, resource_type: str, resource_id: str) -> VersionManager:
    current_file, _relative = versions._resolve_resource_path(resource_type, resource_id, project_path)
    current_file.parent.mkdir(parents=True, exist_ok=True)
    current_file.write_bytes(b"typed-video")
    visual = ArtifactBasis.build(
        (
            "artifact-visual/video-reference"
            if resource_type == "reference_videos"
            else "artifact-visual/video-storyboard"
        ),
        kind_version=1,
        inputs=(
            {
                "unit_id": resource_id,
                "visual_shots": [{"shot_index": 0, "lines": ["Run."]}],
                "style": "cinematic",
                "canvas": {"aspect_ratio": "9:16"},
                "request_references": [],
            }
            if resource_type == "reference_videos"
            else {
                "resource_id": resource_id,
                "visual_prompt": {"action": "Run.", "camera_motion": "Static"},
                "canvas": {"aspect_ratio": "9:16"},
                "frames": [{"role": "storyboard", "sha256": "a" * 64}],
            }
        ),
    )
    speech = ArtifactBasis.build("artifact-speech/video", kind_version=1, inputs={"mode": "narrator_voiceover"})
    duration = build_video_duration_basis(4)
    currency = VideoArtifactCurrencyFacts(
        episode=1,
        request_duration_seconds=4,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4,),
        reference_image_limit=1 if resource_type == "reference_videos" else None,
        parent_version=0,
    )
    manager = VersionManager(project_path)
    manager.add_version(
        resource_type,
        resource_id,
        "typed video",
        source_file=current_file,
        execution_checkpoint_schema_version=3,
        execution_duration_seconds=4,
        execution_request_digest="d" * 64,
        artifact_video_currency=currency.to_dict(),
        execution_script_file="episode_1.json",
    )
    return manager


def _typed_audio_project(tmp_path: Path) -> tuple[object, Path, VersionManager]:
    from lib.project_manager import ProjectManager

    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "novel_text": "旁白", "generated_assets": {}}],
        },
        "episode_1.json",
        validate=False,
    )
    project_path = pm.get_project_path("demo")
    current = project_path / "audio" / "segment_E1S01.wav"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(b"audio-v1")
    basis = ArtifactBasisDescriptor.from_basis(
        ArtifactBasis.build(
            "narration-delivery/tts-audio",
            kind_version=1,
            inputs={
                "text": "旁白",
                "provider_id": "dashscope",
                "model_id": "qwen3-tts-flash",
                "voice": "Cherry",
                "speed": None,
            },
        )
    )
    manager = VersionManager(project_path)
    manager.add_version(
        "audio",
        "E1S01",
        "旁白",
        source_file=current,
        artifact_episode=1,
        artifact_audio_basis=basis.to_dict(),
        execution_script_file="episode_1.json",
        tts_actual_duration_seconds=3.0,
        tts_provider_id="dashscope",
        tts_model_id="qwen3-tts-flash",
        tts_voice="Cherry",
        tts_speed=None,
        tts_basis_digest=basis.digest,
    )
    return pm, project_path, manager


def _client(monkeypatch):
    fake_pm = _FakePM()
    monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    return TestClient(app), fake_pm


class TestVersionsRouter:
    def test_non_typed_restore_rolls_back_media_pointer_and_metadata_when_manifest_commit_fails(
        self,
        tmp_path,
        monkeypatch,
    ):
        from lib.project_manager import ProjectManager

        project_path = tmp_path / "demo"
        (project_path / "characters").mkdir(parents=True)
        project_path.joinpath("project.json").write_text(
            '{"schema_version":8,"title":"Demo","content_mode":"narration",'
            '"generation_mode":"storyboard","style":"Anime","aspect_ratio":"9:16",'
            '"episodes":[],"characters":{"Alice":{"description":"hero",'
            '"character_sheet":"characters/Alice.png"}},"scenes":{},"props":{}}',
            encoding="utf-8",
        )
        current = project_path / "characters" / "Alice.png"
        current.write_bytes(b"old")
        manager = VersionManager(project_path)
        manager.add_version("characters", "Alice", "old", source_file=current)
        current.write_bytes(b"new")
        manager.add_version("characters", "Alice", "new", source_file=current)

        project_before = (project_path / "project.json").read_bytes()
        versions_before = manager.versions_file.read_bytes()
        pm = ProjectManager(tmp_path)
        monkeypatch.setattr(versions, "get_project_manager", lambda: pm)
        monkeypatch.setattr(
            versions,
            "register_current_resource_artifact",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("manifest commit failed")),
        )

        with pytest.raises(RuntimeError, match="manifest commit failed"):
            manager.restore_version(
                "characters",
                "Alice",
                1,
                current,
                on_restore=lambda _record: versions._restore_non_typed_sidecars(
                    resource_type="characters",
                    project_name="demo",
                    resource_id="Alice",
                    file_path="characters/Alice.png",
                    project_path=project_path,
                ),
            )

        assert current.read_bytes() == b"new"
        assert manager.versions_file.read_bytes() == versions_before
        assert manager.get_current_version("characters", "Alice") == 2
        assert (project_path / "project.json").read_bytes() == project_before

    def test_storyboard_restore_duplicate_identity_rolls_back_every_formal_file(self, tmp_path, monkeypatch):
        from lib.project_manager import ProjectManager

        project_path = tmp_path / "demo"
        scripts_dir = project_path / "scripts"
        storyboards_dir = project_path / "storyboards"
        scripts_dir.mkdir(parents=True)
        storyboards_dir.mkdir()
        project = {
            "schema_version": 8,
            "title": "Demo",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "style": "Anime",
            "aspect_ratio": "9:16",
            "episodes": [
                {"episode": 1, "script_file": "scripts/episode_1.json"},
                {"episode": 2, "script_file": "scripts/episode_2.json"},
            ],
            "characters": {},
            "scenes": {},
            "props": {},
        }
        (project_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
        for episode in (1, 2):
            script = {
                "episode": episode,
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "DUP",
                        "novel_text": "旁白",
                        "image_prompt": "画面",
                        "generated_assets": {"storyboard_image": f"storyboards/old-{episode}.png"},
                    }
                ],
            }
            (scripts_dir / f"episode_{episode}.json").write_text(json.dumps(script), encoding="utf-8")

        current = storyboards_dir / "scene_DUP.png"
        current.write_bytes(b"old")
        manager = VersionManager(project_path)
        manager.add_version("storyboards", "DUP", "old", source_file=current)
        current.write_bytes(b"new")
        manager.add_version("storyboards", "DUP", "new", source_file=current)
        snapshots = {
            path: path.read_bytes()
            for path in (project_path / "project.json", *sorted(scripts_dir.glob("*.json")), manager.versions_file)
        }
        pm = ProjectManager(tmp_path)
        monkeypatch.setattr(versions, "get_project_manager", lambda: pm)

        with pytest.raises(ValueError, match="exactly one episode binding"):
            manager.restore_version(
                "storyboards",
                "DUP",
                1,
                current,
                on_restore=lambda _record: versions._restore_non_typed_sidecars(
                    resource_type="storyboards",
                    project_name="demo",
                    resource_id="DUP",
                    file_path="storyboards/scene_DUP.png",
                    project_path=project_path,
                ),
            )

        assert current.read_bytes() == b"new"
        assert manager.get_current_version("storyboards", "DUP") == 2
        assert all(path.read_bytes() == content for path, content in snapshots.items())
        assert not (project_path / ".arcreel_artifacts.json").exists()

    def test_storyboard_restore_rollback_holds_script_lock_against_concurrent_edit(self, tmp_path, monkeypatch):
        from lib.project_manager import ProjectManager

        pm = ProjectManager(tmp_path)
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.save_script(
            "demo",
            {
                "episode": 1,
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "novel_text": "旁白",
                        "image_prompt": "画面",
                        "generated_assets": {
                            "storyboard_image": "storyboards/old.png",
                            "video_clip": None,
                        },
                    }
                ],
            },
            "episode_1.json",
            validate=False,
        )
        project_path = pm.get_project_path("demo")
        registration_started = threading.Event()
        release_registration = threading.Event()
        edit_finished = threading.Event()

        def _fail_registration(*_args, **_kwargs):
            registration_started.set()
            assert release_registration.wait(timeout=5)
            raise RuntimeError("manifest commit failed")

        monkeypatch.setattr(versions, "get_project_manager", lambda: pm)
        monkeypatch.setattr(versions, "register_current_resource_artifact", _fail_registration)

        def _restore() -> None:
            versions._restore_non_typed_sidecars(
                resource_type="storyboards",
                project_name="demo",
                resource_id="E1S01",
                file_path="storyboards/scene_E1S01.png",
                project_path=project_path,
            )

        def _concurrent_edit() -> None:
            pm.update_scene_asset(
                "demo",
                "episode_1.json",
                "E1S01",
                "video_clip",
                "videos/concurrent.mp4",
            )
            edit_finished.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            restore_future = pool.submit(_restore)
            assert registration_started.wait(timeout=5)
            edit_future = pool.submit(_concurrent_edit)
            assert not edit_finished.wait(timeout=0.1)
            release_registration.set()
            with pytest.raises(RuntimeError, match="manifest commit failed"):
                restore_future.result(timeout=5)
            edit_future.result(timeout=5)

        script = pm.load_script("demo", "episode_1.json")
        assets = script["segments"][0]["generated_assets"]
        assert assets["storyboard_image"] == "storyboards/old.png"
        assert assets["video_clip"] == "videos/concurrent.mp4"

    def test_get_versions_and_restore(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        with client:
            get_resp = client.get("/api/v1/projects/demo/versions/characters/Alice")
            assert get_resp.status_code == 200
            assert get_resp.json()["current_version"] == 1

            restore_resp = client.post("/api/v1/projects/demo/versions/characters/Alice/restore/1")
            assert restore_resp.status_code == 200
            assert restore_resp.json()["current_version"] == 1
            assert any(item[0] == "character" for item in fake_pm.updated)

    def test_manual_video_is_presentable_without_claiming_it_is_restorable(self, monkeypatch):
        client, _ = _client(monkeypatch)

        class _ManualVideoVM(_FakeVM):
            def get_versions(self, resource_type, resource_id):
                return {
                    "current_version": 1,
                    "versions": [
                        {
                            "version": 1,
                            "file": f"versions/{resource_type}/{resource_id}.mp4",
                            "source": "manual_upload",
                        }
                    ],
                }

        monkeypatch.setattr(versions, "get_version_manager", lambda _project_name: _ManualVideoVM())
        with client:
            response = client.get("/api/v1/projects/demo/versions/videos/E1S01")

        assert response.status_code == 200
        record = response.json()["versions"][0]
        assert record["restorable"] is False
        assert record["presentation_available"] is True

    def test_get_and_restore_scenes(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        with client:
            get_resp = client.get("/api/v1/projects/demo/versions/scenes/庙宇")
            assert get_resp.status_code == 200

            restore_resp = client.post("/api/v1/projects/demo/versions/scenes/庙宇/restore/1")
            assert restore_resp.status_code == 200
            assert restore_resp.json()["file_path"] == "scenes/庙宇.png"
            assert any(item[0] == "scene" for item in fake_pm.updated)

    def test_get_and_restore_props(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        with client:
            get_resp = client.get("/api/v1/projects/demo/versions/props/玉佩")
            assert get_resp.status_code == 200

            restore_resp = client.post("/api/v1/projects/demo/versions/props/玉佩/restore/1")
            assert restore_resp.status_code == 200
            assert restore_resp.json()["file_path"] == "props/玉佩.png"
            assert any(item[0] == "prop" for item in fake_pm.updated)

    def test_get_and_restore_products(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        with client:
            get_resp = client.get("/api/v1/projects/demo/versions/products/保温杯")
            assert get_resp.status_code == 200

            restore_resp = client.post("/api/v1/projects/demo/versions/products/保温杯/restore/1")
            assert restore_resp.status_code == 200
            assert restore_resp.json()["file_path"] == "products/保温杯.png"
            assert any(item[0] == "product" for item in fake_pm.updated)

    @pytest.mark.parametrize("resource_type", ["videos", "reference_videos"])
    def test_typed_video_restore_uses_selection_finalization_guard(self, tmp_path, monkeypatch, resource_type):
        resource_id = "E1S01"
        project_path = tmp_path / "demo"
        project_path.mkdir()
        guard_active = False
        guard_calls = []

        class _PM:
            @staticmethod
            def get_project_path(_project_name):
                return project_path

        @asynccontextmanager
        async def _guard(**identity):
            nonlocal guard_active
            guard_calls.append(identity)
            guard_active = True
            try:
                yield
            finally:
                guard_active = False

        target = versions.TypedMediaRestoreTarget(
            episode=1,
            script_file="episode_1.json",
            basis=ArtifactBasisDescriptor.from_basis(build_video_duration_basis(4)),
            created_at=None,
        )

        def _restore(**_kwargs):
            assert guard_active
            return {"restored_version": 1, "current_version": 1, "prompt": "p"}

        monkeypatch.setattr(versions, "get_project_manager", _PM)
        monkeypatch.setattr(versions, "get_version_manager", lambda _project_name: _FakeVM(project_path))
        monkeypatch.setattr(versions, "get_typed_media_restore_target", lambda *_args, **_kwargs: target)
        monkeypatch.setattr(versions, "restore_typed_media_version", _restore)
        monkeypatch.setattr(versions, "generation_admission_lock", _guard)

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            response = client.post(f"/api/v1/projects/demo/versions/{resource_type}/{resource_id}/restore/1")

        assert response.status_code == 200
        assert guard_calls == [{"project_name": "demo", "script_file": "episode_1.json", "resource_id": resource_id}]
        assert not guard_active

    def test_audio_restore_is_enabled_for_typed_history(self, tmp_path, monkeypatch):
        pm, _project_path, manager = _typed_audio_project(tmp_path)
        monkeypatch.setattr(versions, "get_project_manager", lambda: pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: manager)
        monkeypatch.setattr(versions, "active_tts_resource_ids", AsyncMock(return_value=frozenset()))
        monkeypatch.setattr(versions, "active_narrated_video_resource_ids", AsyncMock(return_value=frozenset()))

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            response = client.post("/api/v1/projects/demo/versions/audio/E1S01/restore/1")

        assert response.status_code == 200
        assert response.json()["file_path"] == "audio/segment_E1S01.wav"

    def test_audio_restore_is_blocked_while_tts_is_active(self, tmp_path, monkeypatch):
        pm, project_path, manager = _typed_audio_project(tmp_path)
        before = (project_path / "audio" / "segment_E1S01.wav").read_bytes()
        monkeypatch.setattr(versions, "get_project_manager", lambda: pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: manager)
        monkeypatch.setattr(
            versions,
            "active_tts_resource_ids",
            AsyncMock(return_value=frozenset({"E1S01"})),
        )
        monkeypatch.setattr(versions, "active_narrated_video_resource_ids", AsyncMock(return_value=frozenset()))

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            response = client.post("/api/v1/projects/demo/versions/audio/E1S01/restore/1")

        assert response.status_code == 409
        assert (project_path / "audio" / "segment_E1S01.wav").read_bytes() == before

    def test_audio_restore_is_blocked_while_video_consumes_current_tts(self, tmp_path, monkeypatch):
        pm, project_path, manager = _typed_audio_project(tmp_path)
        before = (project_path / "audio" / "segment_E1S01.wav").read_bytes()
        monkeypatch.setattr(versions, "get_project_manager", lambda: pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: manager)
        monkeypatch.setattr(versions, "active_tts_resource_ids", AsyncMock(return_value=frozenset()))
        monkeypatch.setattr(
            versions,
            "active_narrated_video_resource_ids",
            AsyncMock(return_value=frozenset({"E1S01"})),
        )

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            response = client.post("/api/v1/projects/demo/versions/audio/E1S01/restore/1")

        assert response.status_code == 409
        assert (project_path / "audio" / "segment_E1S01.wav").read_bytes() == before

    def test_restore_error_mapping(self, monkeypatch):
        client, _ = _client(monkeypatch)
        with client:
            bad_type = client.get("/api/v1/projects/demo/versions/bad/Alice")
            assert bad_type.status_code == 400

            not_found = client.post("/api/v1/projects/demo/versions/characters/Alice/restore/404")
            assert not_found.status_code == 404

            bad_value = client.post("/api/v1/projects/demo/versions/characters/Alice/restore/400")
            assert bad_value.status_code == 400

            unsupported = client.post("/api/v1/projects/demo/versions/unknown/Alice/restore/1")
            assert unsupported.status_code == 400

    def test_grid_restore_resets_split_state_without_touching_scripts(self, tmp_path, monkeypatch):
        """grids 还原放行：只换回联合图并复位宫格记录的切分态；不同步任何剧本、
        frame_chain 原样保留，分镜图不被触碰。"""
        from lib.grid.models import GridGeneration
        from lib.grid_manager import GridManager

        grid = GridGeneration.create(
            episode=1,
            script_file="episode_1.json",
            scene_ids=["a", "b"],
            rows=2,
            cols=2,
            grid_size="grid_4",
            provider="p",
            model="m",
            video_aspect_ratio="16:9",
        )
        grid.status = "failed"
        grid.error_message = "boom"
        grid.split_at = "2026-01-01T00:00:00+00:00"
        frame_chain_before = [c.to_dict() for c in grid.frame_chain]
        GridManager(tmp_path).save(grid)
        (tmp_path / "grids" / f"{grid.id}.png").write_bytes(b"restored-bytes")

        fake_pm = _GridPM(tmp_path)
        monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        client = TestClient(app)

        with client:
            resp = client.post(f"/api/v1/projects/demo/versions/grids/{grid.id}/restore/1")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["file_path"] == f"grids/{grid.id}.png"
            # 还原后联合图文件的 mtime 指纹供前端 cache-bust
            assert f"grids/{grid.id}.png" in body["asset_fingerprints"]

        saved = GridManager(tmp_path).get(grid.id)
        assert saved is not None
        assert saved.status == "completed"
        assert saved.error_message is None
        assert saved.split_at is None
        assert [c.to_dict() for c in saved.frame_chain] == frame_chain_before
        # 冻结比例保持不变：还原换回的是历史联合图，其产出比例未随版本记录，
        # 改写成项目当前比例会把老图按新比例裁切。与手动上传（改写为当前比例）相反。
        assert saved.video_aspect_ratio == "16:9"
        # 不做分镜侧元数据同步
        assert fake_pm.update_calls == []

    def test_grid_restore_succeeds_when_the_grid_record_is_missing(self, tmp_path, monkeypatch):
        project = {
            "schema_version": 8,
            "title": "Demo",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
            "style": "Anime",
            "aspect_ratio": "9:16",
            "episodes": [],
            "characters": {},
            "scenes": {},
            "props": {},
        }
        (tmp_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
        fake_pm = _GridPM(tmp_path)
        monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/v1/projects/demo/versions/grids/grid_000000000000/restore/1")

        assert response.status_code == 200, response.text
        assert response.json()["file_path"] == "grids/grid_000000000000.png"

    @pytest.mark.parametrize(
        "project,expected_detail",
        [
            ({"content_mode": "ad", "generation_mode": "storyboard", "grid_storyboard": True}, "广告/短片项目"),
            (
                {"content_mode": "narration", "generation_mode": "storyboard", "grid_storyboard": False},
                "项目未启用分镜板",
            ),
        ],
    )
    def test_grid_restore_rejected_when_grid_writes_disabled(self, tmp_path, monkeypatch, project, expected_detail):
        """还原与重生成/切分/上传共用宫格写闸门：广告项目与关闭宫格开关的项目一律拒绝。

        还原同样会换掉联合图并复位宫格记录，闸门漏在这里就成了改写残留 grid 的绕行路径。
        """
        from lib.grid.models import GridGeneration
        from lib.grid_manager import GridManager

        grid = GridGeneration.create(
            episode=1,
            script_file="episode_1.json",
            scene_ids=["a", "b"],
            rows=2,
            cols=2,
            grid_size="grid_4",
            provider="p",
            model="m",
            video_aspect_ratio="9:16",
        )
        grid.split_at = "2026-01-01T00:00:00+00:00"
        GridManager(tmp_path).save(grid)
        (tmp_path / "grids" / f"{grid.id}.png").write_bytes(b"original-bytes")

        grid_pm = _GridPM(tmp_path, project=project)
        monkeypatch.setattr(versions, "get_project_manager", lambda: grid_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)

        with TestClient(app) as client:
            resp = client.post(f"/api/v1/projects/demo/versions/grids/{grid.id}/restore/1")

        assert resp.status_code == 400
        # 断言到具体文案：两道闸门各自触发，不被对方的 400 顶替
        assert expected_detail in resp.json()["detail"]
        # 拒绝即止：联合图未被替换，宫格记录的切分态原样保留
        assert (tmp_path / "grids" / f"{grid.id}.png").read_bytes() == b"original-bytes"
        saved = GridManager(tmp_path).get(grid.id)
        assert saved is not None
        assert saved.split_at == "2026-01-01T00:00:00+00:00"

    def test_non_grid_restore_unaffected_by_grid_gate(self, monkeypatch):
        """闸门只作用于 grids：其它资源类型的还原不因项目宫格配置被拦。"""
        client, fake_pm = _client(monkeypatch)
        with client:
            resp = client.post("/api/v1/projects/demo/versions/characters/Alice/restore/1")
        assert resp.status_code == 200

    def test_grid_restore_keeps_in_flight_status(self, tmp_path, monkeypatch):
        """生成在途时还原不把记录复位成 completed：记录一旦谎报空闲，
        切分/上传的在途闸门就会放行，用户可对着即将被 worker 覆写的联合图落格。
        切分态仍无条件作废——联合图内容确已换成历史版本。"""
        from lib.grid.models import GridGeneration
        from lib.grid_manager import GridManager

        grid = GridGeneration.create(
            episode=1,
            script_file="episode_1.json",
            scene_ids=["a", "b"],
            rows=2,
            cols=2,
            grid_size="grid_4",
            provider="p",
            model="m",
            video_aspect_ratio="9:16",
        )
        grid.status = "generating"
        grid.split_at = "2026-01-01T00:00:00+00:00"
        GridManager(tmp_path).save(grid)
        (tmp_path / "grids" / f"{grid.id}.png").write_bytes(b"restored-bytes")

        grid_pm = _GridPM(tmp_path)
        monkeypatch.setattr(versions, "get_project_manager", lambda: grid_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        client = TestClient(app)

        with client:
            resp = client.post(f"/api/v1/projects/demo/versions/grids/{grid.id}/restore/1")
            assert resp.status_code == 200, resp.text

        saved = GridManager(tmp_path).get(grid.id)
        assert saved is not None
        assert saved.status == "generating"
        assert saved.split_at is None

    def test_reference_video_restore_returns_thumbnail_fingerprint(self, tmp_path, monkeypatch):
        """reference_videos 还原放行：清缩略图并以 fingerprint=0 通知前端失效。"""
        from lib.project_manager import ProjectManager

        real_pm = ProjectManager(tmp_path)
        real_pm.create_project("demo")
        real_pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        real_pm.save_script(
            "demo",
            {
                "episode": 1,
                "title": "E1",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "video_units": [
                    {
                        "unit_id": "E1U1",
                        "generated_assets": {
                            "video_clip": "reference_videos/E1U1.mp4",
                            "video_uri": "https://stale",
                            "video_thumbnail": "reference_videos/thumbnails/E1U1.jpg",
                            "status": "completed",
                        },
                    }
                ],
            },
            "episode_1.json",
            validate=False,
        )
        project_path = real_pm.get_project_path("demo")
        thumb = project_path / "reference_videos" / "thumbnails" / "E1U1.jpg"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"jpg")
        vm = _typed_video_versions(project_path, "reference_videos", "E1U1")

        monkeypatch.setattr(versions, "get_project_manager", lambda: real_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: vm)

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/versions/reference_videos/E1U1/restore/1")
            assert resp.status_code == 200
            body = resp.json()
            assert body["file_path"] == "reference_videos/E1U1.mp4"
            assert body["asset_fingerprints"]["reference_videos/thumbnails/E1U1.jpg"] == 0

        # 缩略图文件被删除；unit 元数据清掉过期 video_uri / video_thumbnail
        assert not thumb.exists()
        script = real_pm.load_script("demo", "episode_1.json")
        ga = script["video_units"][0]["generated_assets"]
        assert ga["video_clip"] == "reference_videos/E1U1.mp4"
        assert "video_uri" not in ga
        assert "video_thumbnail" not in ga
        assert ga["status"] == "completed"

    def test_ad_reference_video_restore_preserves_inert_legacy_source_signature(self, tmp_path, monkeypatch):
        """还原只更新成片元数据；遗留来源签名既不读取版本档案，也不清理或覆盖。"""
        from lib.project_manager import ProjectManager

        real_pm = ProjectManager(tmp_path)
        real_pm.create_project("demo")
        real_pm.create_project_metadata("demo", "Demo", "Anime", "ad", extras={"generation_mode": "reference_video"})
        real_pm.save_script(
            "demo",
            {
                "episode": 1,
                "title": "E1",
                "content_mode": "ad",
                "video_units": [
                    {
                        "unit_id": "E1U1",
                        "shots": [{"text": "镜头1：产品特写"}],
                        "references": [],
                        "duration_seconds": 5,
                        "generated_assets": {
                            "video_clip": "reference_videos/E1U1.mp4",
                            "status": "completed",
                            "source_signature": "signature-of-current-product",
                        },
                    }
                ],
            },
            "episode_1.json",
            validate=False,
        )
        project_path = real_pm.get_project_path("demo")
        vm = _typed_video_versions(project_path, "reference_videos", "E1U1")

        monkeypatch.setattr(versions, "get_project_manager", lambda: real_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: vm)

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/versions/reference_videos/E1U1/restore/1")
            assert resp.status_code == 200

        script = real_pm.load_script("demo", "episode_1.json")
        ga = script["video_units"][0]["generated_assets"]
        assert ga["source_signature"] == "signature-of-current-product"

    def test_video_restore_clears_stale_uri_and_thumbnail_metadata(self, tmp_path, monkeypatch):
        """videos 还原同步剧本元数据：还原的是历史本地文件，过期 provider URI 与已删缩略图须清空。"""
        from lib.project_manager import ProjectManager

        real_pm = ProjectManager(tmp_path)
        real_pm.create_project("demo")
        real_pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        real_pm.save_script(
            "demo",
            {
                "episode": 1,
                "title": "E1",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "novel_text": "t",
                        "duration_seconds": 5,
                        "generated_assets": {
                            "storyboard_image": "storyboards/scene_E1S01.png",
                            "video_clip": "videos/scene_E1S01.mp4",
                            "video_uri": "https://stale-provider-uri",
                            "video_thumbnail": "thumbnails/scene_E1S01.jpg",
                            "status": "completed",
                        },
                    }
                ],
            },
            "episode_1.json",
            validate=False,
        )
        project_path = real_pm.get_project_path("demo")
        thumb = project_path / "thumbnails" / "scene_E1S01.jpg"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"jpg")
        vm = _typed_video_versions(project_path, "videos", "E1S01")

        monkeypatch.setattr(versions, "get_project_manager", lambda: real_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: vm)

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/versions/videos/E1S01/restore/1")
            assert resp.status_code == 200
            assert resp.json()["asset_fingerprints"]["thumbnails/scene_E1S01.jpg"] == 0

        assert not thumb.exists()
        ga = real_pm.load_script("demo", "episode_1.json")["segments"][0]["generated_assets"]
        assert ga["video_clip"] == "videos/scene_E1S01.mp4"
        assert ga["video_uri"] is None
        assert ga["video_thumbnail"] is None
        assert ga["status"] == "completed"

    def test_resolve_resource_path_rejects_traversal(self):
        """resource_id 拼出的绝对路径若逃出项目目录，必须 400（路径遍历防护）。

        正常路由的 path 参数不会含 `/`，故直接对 helper 断言这道收口防护。
        """
        project_path = Path(tempfile.gettempdir()) / "demo"

        with pytest.raises(BadRequestError) as exc:
            versions._resolve_resource_path(
                "characters",
                "../../../../etc/passwd",
                project_path,
            )
        assert exc.value.status_code == 400

    def test_resolve_resource_path_accepts_normal_id(self):
        project_path = Path(tempfile.gettempdir()) / "demo"

        current_file, relative = versions._resolve_resource_path(
            "characters",
            "Alice",
            project_path,
        )
        assert relative == "characters/Alice.png"
        # helper 经 safe_join 返回 realpath 规范化后的绝对路径（symlink 已展开），
        # 故拿同一入参 base 的 realpath 拼接断言。
        expected = Path(os.path.realpath(project_path)) / "characters" / "Alice.png"
        assert current_file == expected

    def test_storyboard_restore_syncs_scripts_with_error_tolerance(self, tmp_path, monkeypatch):
        from lib.project_manager import ProjectManager

        pm = ProjectManager(tmp_path)
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        project = pm.load_project("demo")
        project["schema_version"] = 7
        project["episodes"] = [
            {"episode": episode, "script_file": f"scripts/episode_{episode}.json"} for episode in (1, 2, 3)
        ]
        pm.save_project("demo", project)
        project_path = pm.get_project_path("demo")
        scripts_dir = project_path / "scripts"
        scripts = {
            "episode_1.json": {
                "episode": 1,
                "content_mode": "narration",
                "segments": [{"segment_id": "OTHER", "generated_assets": {"storyboard_image": None}}],
            },
            "episode_2.json": {"episode": 2, "content_mode": "narration", "segments": None},
            "episode_3.json": {
                "episode": 3,
                "content_mode": "narration",
                "segments": [{"segment_id": "E1S01", "generated_assets": {"storyboard_image": None}}],
            },
        }
        for name, payload in scripts.items():
            (scripts_dir / name).write_text(json.dumps(payload), encoding="utf-8")

        monkeypatch.setattr(versions, "get_project_manager", lambda: pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/versions/storyboards/E1S01/restore/1")
            assert resp.status_code == 200
            assert resp.json()["file_path"] == "storyboards/scene_E1S01.png"

        untouched = json.loads((scripts_dir / "episode_1.json").read_text(encoding="utf-8"))
        malformed = json.loads((scripts_dir / "episode_2.json").read_text(encoding="utf-8"))
        updated = pm.load_script("demo", "episode_3.json")
        assert untouched["segments"][0]["generated_assets"]["storyboard_image"] is None
        assert malformed["segments"] is None
        assert updated["segments"][0]["generated_assets"]["storyboard_image"] == "storyboards/scene_E1S01.png"

    def test_storyboard_restore_unexpected_error_surfaces_as_5xx(self, tmp_path, monkeypatch):
        """跨集同步遇未预期异常时不再被 except Exception 吞掉，让 router 层 5xx 暴露问题。"""
        project_path = tmp_path / "demo"
        scripts_dir = project_path / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "x.json").write_text("{}", encoding="utf-8")

        class _CrashingPM:
            def __init__(self, path):
                self.project_path = path

            def get_project_path(self, project_name):
                return self.project_path

            def update_scene_asset_across_scripts(self, *args, **kwargs):
                raise RuntimeError("unexpected crash")

        fake_pm = _CrashingPM(project_path)
        monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/api/v1/projects/demo/versions/storyboards/E1S01/restore/1")
            assert resp.status_code == 500

    def test_orphaned_storyboard_version_restore_succeeds_without_a_script_binding(self, tmp_path, monkeypatch):
        from lib.project_manager import ProjectManager

        pm = ProjectManager(tmp_path)
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        monkeypatch.setattr(versions, "get_project_manager", lambda: pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/v1/projects/demo/versions/storyboards/ORPHAN/restore/1")

        assert response.status_code == 200, response.text
        assert response.json()["file_path"] == "storyboards/scene_ORPHAN.png"

    def test_storyboard_restore_transient_oserror_does_not_5xx(self, tmp_path, monkeypatch):
        """跨集同步 sibling 集遇到 transient IO 错误(OSError)不应让主集 restore 5xx——
        restore 主集已成功,housekeeping 性质的 sibling 同步应降级跳过 + warning。
        """
        from lib.project_manager import ProjectManager

        class _TransientIOFailPM(ProjectManager):
            """只在 sibling 集读取边界注入 transient IO failure。"""

            def __init__(self, projects_root):
                super().__init__(projects_root)
                self.calls: list[str] = []

            def _read_script_unlocked(self, project_name, filename):
                normalized = self.normalize_script_filename(filename)
                self.calls.append(normalized)
                if normalized == "episode_2.json":
                    raise OSError("transient flock timeout")
                return super()._read_script_unlocked(project_name, normalized)

        pm = _TransientIOFailPM(tmp_path)
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        project = pm.load_project("demo")
        project["schema_version"] = 7
        project["episodes"] = [
            {"episode": episode, "script_file": f"scripts/episode_{episode}.json"} for episode in (1, 2)
        ]
        pm.save_project("demo", project)
        scripts_dir = pm.get_project_path("demo") / "scripts"
        for episode in (1, 2):
            payload = {
                "episode": episode,
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01" if episode == 1 else "E2S01",
                        "generated_assets": {"storyboard_image": None},
                    }
                ],
            }
            (scripts_dir / f"episode_{episode}.json").write_text(json.dumps(payload), encoding="utf-8")

        monkeypatch.setattr(versions, "get_project_manager", lambda: pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/versions/storyboards/E1S01/restore/1")
            # transient IO 降级 + warning,主集 restore 仍 200
            assert resp.status_code == 200
            assert resp.json()["file_path"] == "storyboards/scene_E1S01.png"
        assert set(pm.calls) == {"episode_1.json", "episode_2.json"}
        updated = pm.load_script("demo", "episode_1.json")
        assert updated["segments"][0]["generated_assets"]["storyboard_image"] == "storyboards/scene_E1S01.png"

    def test_restore_returns_asset_fingerprints(self, monkeypatch, tmp_path):
        """版本还原应返回受影响文件的 fingerprint"""
        fake_pm = _FakePM()
        fake_pm.get_project_path = lambda name: tmp_path

        (tmp_path / "storyboards").mkdir()
        (tmp_path / "storyboards" / "scene_E1S01.png").write_bytes(b"restored")

        monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda name: _FakeVM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/versions/storyboards/E1S01/restore/1")
            assert resp.status_code == 200
            data = resp.json()
            assert "asset_fingerprints" in data
            assert "storyboards/scene_E1S01.png" in data["asset_fingerprints"]
            assert isinstance(data["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)

    def test_get_versions_unexpected_error_maps_to_500(self, monkeypatch):
        fake_pm = _FakePM()
        monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            versions,
            "get_version_manager",
            lambda project_name: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/projects/demo/versions/characters/Alice")
            assert resp.status_code == 500
            # 内部异常细节不得泄露给客户端，仅落服务端日志
            assert "boom" not in resp.text

    def test_restore_version_unexpected_error_maps_to_500(self, monkeypatch):
        fake_pm = _FakePM()
        monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            versions,
            "get_version_manager",
            lambda project_name: (_ for _ in ()).throw(RuntimeError("RESTORE_LEAK_SECRET")),
        )

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/api/v1/projects/demo/versions/characters/Alice/restore/1")
            assert resp.status_code == 500
            # 内部异常细节不得泄露给客户端，仅落服务端日志
            assert "RESTORE_LEAK_SECRET" not in resp.text
            assert "boom" not in resp.json()["detail"]
