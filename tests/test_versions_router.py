import os
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.api_errors import BadRequestError
from lib.script_editor import ScriptEditError
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

    def _update_asset_sheet(self, asset_type, *args):
        self.updated.append((asset_type, args))

    def update_scene_asset(self, *args, **kwargs):
        self.updated.append(("storyboard", args, kwargs))


class _FakeVM:
    def __init__(self, project_path=None, version_metadata=None):
        self.project_path = project_path
        # 版本档案里的补充元数据（如 source_signature），缺省无档案
        self.version_metadata = version_metadata or {}

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
        return self.version_metadata.get(key)

    def restore_version(self, resource_type, resource_id, version, current_file):
        if version == 404:
            raise FileNotFoundError("missing")
        if version == 400:
            raise ValueError("bad")
        return {
            "restored_version": version,
            "current_version": version,
            "prompt": "p",
        }


class _GridPM:
    """记录剧本侧写回调用的 ProjectManager 替身，供 grids 还原用例断言「不碰剧本」。"""

    def __init__(self, project_path):
        self.project_path = project_path
        self.update_calls = []

    def get_project_path(self, project_name):
        return self.project_path

    def update_scene_asset(self, *args, **kwargs):
        self.update_calls.append((args, kwargs))

    def batch_update_scene_assets(self, *args, **kwargs):
        self.update_calls.append((args, kwargs))


class _StoryboardSyncPM:
    def __init__(self, project_path):
        self.project_path = project_path
        self.update_calls = []

    def get_project_path(self, project_name):
        return self.project_path

    def update_scene_asset(self, project_name, script_filename, scene_id, asset_type, asset_path):
        self.update_calls.append(script_filename)
        if script_filename == "a.json":
            # KeyError = 该集脚本不引用此 scene_id,正常跳过(非脏数据)
            raise KeyError("missing scene")
        if script_filename == "b.json":
            # ScriptEditError = 该集脚本脏(分镜数组键损坏),路由层精确捕获 + warning 后跳过
            raise ScriptEditError("segments 必须是列表，当前为 NoneType")


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
        # 不做分镜侧元数据同步
        assert fake_pm.update_calls == []

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
        )
        grid.status = "generating"
        grid.split_at = "2026-01-01T00:00:00+00:00"
        GridManager(tmp_path).save(grid)
        (tmp_path / "grids" / f"{grid.id}.png").write_bytes(b"restored-bytes")

        monkeypatch.setattr(versions, "get_project_manager", lambda: _GridPM(tmp_path))
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

        monkeypatch.setattr(versions, "get_project_manager", lambda: real_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

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

    def test_ad_reference_video_restore_writes_archived_source_signature(self, tmp_path, monkeypatch):
        """还原 ad 参考单元时把该版本档案里的来源签名写回，读时 stale 按旧产物基准判定。"""
        from lib.project_manager import ProjectManager

        real_pm = ProjectManager(tmp_path)
        real_pm.create_project("demo")
        real_pm.create_project_metadata("demo", "Demo", "Anime", "ad")
        real_pm.save_script(
            "demo",
            {
                "episode": 1,
                "title": "E1",
                "content_mode": "ad",
                "shots": [],
                "reference_units": [
                    {
                        "unit_id": "E1U1",
                        "shot_ids": ["E1S1"],
                        "references": [],
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

        signed_vm = _FakeVM(version_metadata={"source_signature": "signature-of-restored-version"})
        monkeypatch.setattr(versions, "get_project_manager", lambda: real_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: signed_vm)

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/versions/reference_videos/E1U1/restore/1")
            assert resp.status_code == 200

        script = real_pm.load_script("demo", "episode_1.json")
        ga = script["reference_units"][0]["generated_assets"]
        assert ga["source_signature"] == "signature-of-restored-version"

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

        monkeypatch.setattr(versions, "get_project_manager", lambda: real_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

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
        project_path = tmp_path / "demo"
        scripts_dir = project_path / "scripts"
        scripts_dir.mkdir(parents=True)
        for name in ("a.json", "b.json", "c.json"):
            (scripts_dir / name).write_text("{}", encoding="utf-8")

        fake_pm = _StoryboardSyncPM(project_path)
        monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(versions, "get_version_manager", lambda project_name: _FakeVM())

        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(versions.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/versions/storyboards/E1S01/restore/1")
            assert resp.status_code == 200
            assert resp.json()["file_path"] == "storyboards/scene_E1S01.png"

        assert sorted(fake_pm.update_calls) == ["a.json", "b.json", "c.json"]

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

            def update_scene_asset(self, *args, **kwargs):
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

    def test_storyboard_restore_transient_oserror_does_not_5xx(self, tmp_path, monkeypatch):
        """跨集同步 sibling 集遇到 transient IO 错误(OSError)不应让主集 restore 5xx——
        restore 主集已成功,housekeeping 性质的 sibling 同步应降级跳过 + warning。
        """
        project_path = tmp_path / "demo"
        scripts_dir = project_path / "scripts"
        scripts_dir.mkdir(parents=True)
        for name in ("a.json", "b.json"):
            (scripts_dir / name).write_text("{}", encoding="utf-8")

        class _TransientIOFailPM:
            """模拟 sibling 集 IO 失败(flock 超时 / EBUSY 等),主集 a.json 同步正常。"""

            def __init__(self, path):
                self.project_path = path
                self.calls: list[str] = []

            def get_project_path(self, project_name):
                return self.project_path

            def update_scene_asset(self, project_name, script_filename, scene_id, asset_type, asset_path):
                self.calls.append(script_filename)
                if script_filename == "b.json":
                    raise OSError("transient flock timeout")

        fake_pm = _TransientIOFailPM(project_path)
        monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
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
        # 两个 sibling 集都被尝试过(b.json 抛 OSError 后 continue,不阻塞)
        assert sorted(fake_pm.calls) == ["a.json", "b.json"]

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
