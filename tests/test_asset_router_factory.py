"""统一资产工厂单测：覆盖 character 的 extra 字段透传与 409 冲突响应。

scenes/props 的 CRUD 行为由 test_scenes_router / test_props_router 覆盖；本文件聚焦
factory 引入的新能力（character extras + extra='allow' 创建语义）。
"""

import unicodedata

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import characters
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.conftest import make_translator

# 兜底 500 的默认 locale 文案：测试未覆盖 get_translator，端点回落到 DEFAULT_LOCALE("zh")，
# 与 make_translator() 默认 locale 一致。
_INTERNAL_ERROR_DETAIL = make_translator()("internal_server_error")

_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


class _FakePM:
    def __init__(self):
        self.projects = {"demo": {"characters": {}, "scenes": {}, "props": {}, "products": {}}}

    def _add_asset(self, asset_type, project_name, name, entry):
        from lib.asset_types import ASSET_SPECS, ensure_project_asset_name_available

        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        bucket = self.projects[project_name].setdefault(ASSET_SPECS[asset_type].bucket_key, {})
        if name in bucket:
            return False
        ensure_project_asset_name_available(self.projects[project_name], name)
        bucket[name] = entry
        return True

    def load_project(self, project_name):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        return self.projects[project_name]

    def rename_asset(self, project_name, table, old_name, new_name, *, dry_run=False):
        from lib.asset_rename import AssetRenameConflictError, AssetRenameNotFoundError, AssetRenameReport
        from lib.asset_types import resolve_asset_key

        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        bucket = self.projects[project_name].setdefault(table, {})
        old_key = resolve_asset_key(bucket, old_name)
        if old_key is None:
            raise AssetRenameNotFoundError(old_name)
        conflict_key = resolve_asset_key(bucket, new_name)
        if conflict_key is not None and conflict_key != old_key:
            raise AssetRenameConflictError(conflict_key)
        if not dry_run:
            bucket[new_name] = bucket.pop(old_key)
        return AssetRenameReport(
            table=table,
            old_name=old_key,
            new_name=new_name,
            episodes=2,
            references=5,
            files=3,
            dry_run=dry_run,
        )

    def save_project(self, project_name, project):
        self.projects[project_name] = project

    def update_project(self, project_name, mutate_fn):
        project = self.load_project(project_name)
        mutate_fn(project)
        self.save_project(project_name, project)


def _client(monkeypatch):
    fake_pm = _FakePM()
    monkeypatch.setattr(characters, "get_project_manager", lambda: fake_pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(characters.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app), fake_pm


class TestAssetRouterFactory:
    @pytest.mark.unit
    def test_character_post_passes_extra_voice_style(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/characters",
                json={"name": "Bob", "description": "hero", "voice_style": "calm"},
            )
            assert resp.status_code == 200
            entry = fake_pm.projects["demo"]["characters"]["Bob"]
            assert entry["voice_style"] == "calm"
            assert entry["character_sheet"] == ""
            # reference_image 是 character 的 extra 字段，create 时未传则默认 ""
            assert entry["reference_image"] == ""

    @pytest.mark.unit
    def test_character_post_with_reference_audio_stamps_voice_updated_at(self, monkeypatch):
        """创建即携带 reference_audio 时同样须机械戳 voice_updated_at，与 PATCH/上传/
        入库导入等其它写点同一口径，否则该角色的存量片段横幅永远感知不到这次设置。"""
        client, fake_pm = _client(monkeypatch)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/characters",
                json={"name": "Bob", "description": "hero", "reference_audio": "characters/refs_audio/Bob.wav"},
            )
            assert resp.status_code == 200
            entry = fake_pm.projects["demo"]["characters"]["Bob"]
            assert entry["reference_audio"] == "characters/refs_audio/Bob.wav"
            assert isinstance(entry.get("voice_updated_at"), str) and entry["voice_updated_at"]

    @pytest.mark.unit
    def test_character_post_without_reference_audio_does_not_stamp(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/characters",
                json={"name": "Bob", "description": "hero"},
            )
            assert resp.status_code == 200
            entry = fake_pm.projects["demo"]["characters"]["Bob"]
            assert "voice_updated_at" not in entry

    @pytest.mark.unit
    def test_character_post_rejects_dismissed_at(self, monkeypatch):
        """新建角色尚无 voice_updated_at，PATCH 侧的等值校验在创建时恒不成立；创建
        端点直接拒绝携带该字段，防止绕过 PATCH 校验写入远未来时间戳。"""
        client, fake_pm = _client(monkeypatch)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/characters",
                json={"name": "Bob", "description": "hero", "voice_notice_dismissed_at": "9999-12-31T00:00:00+00:00"},
            )
            assert resp.status_code == 422
            assert "Bob" not in fake_pm.projects["demo"]["characters"]

    @pytest.mark.unit
    def test_character_post_400_on_path_unsafe_name(self, monkeypatch):
        """名字含路径分隔符须在 HTTP 边界拒绝：这类名字会让生成（嵌套文件路径）
        与后续单段路由（PATCH/DELETE/{name}）全部失效。"""
        client, fake_pm = _client(monkeypatch)
        with client:
            for bad_name in ("李白/诗人", "a\\b", ".."):
                resp = client.post(
                    "/api/v1/projects/demo/characters",
                    json={"name": bad_name, "description": "x"},
                )
                assert resp.status_code == 400, bad_name
                assert bad_name not in fake_pm.projects["demo"]["characters"]

    @pytest.mark.unit
    def test_character_post_409_on_duplicate(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"]["Alice"] = {
            "description": "old",
            "character_sheet": "",
            "voice_style": "",
            "reference_image": "",
        }
        with client:
            resp = client.post(
                "/api/v1/projects/demo/characters",
                json={"name": "Alice", "description": "dup", "voice_style": ""},
            )
            assert resp.status_code == 409

    @pytest.mark.unit
    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    def test_character_post_409_on_cross_type_duplicate_is_localized(self, monkeypatch, locale):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["scenes"]["Shared"] = {"description": "scene", "scene_sheet": ""}
        with client:
            resp = client.post(
                "/api/v1/projects/demo/characters",
                json={"name": "Shared", "description": "character"},
                headers={"Accept-Language": locale},
            )
        assert resp.status_code == 409
        assert "Shared" in resp.json()["detail"]
        assert fake_pm.projects["demo"]["characters"] == {}

    @pytest.mark.unit
    def test_character_patch_accepts_extra_fields(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"]["Alice"] = {
            "description": "old",
            "character_sheet": "",
            "voice_style": "",
            "reference_image": "",
        }
        with client:
            resp = client.patch(
                "/api/v1/projects/demo/characters/Alice",
                json={
                    "description": "new",
                    "voice_style": "strong",
                    "character_sheet": "characters/Alice.png",
                    "reference_image": "characters/refs/Alice.png",
                },
            )
            assert resp.status_code == 200
            entry = fake_pm.projects["demo"]["characters"]["Alice"]
            assert entry["voice_style"] == "strong"
            assert entry["reference_image"] == "characters/refs/Alice.png"

    @pytest.mark.unit
    def test_character_patch_rejects_non_string_value(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"]["Alice"] = {
            "description": "old",
            "character_sheet": "",
            "voice_style": "",
            "reference_image": "",
        }
        with client:
            resp = client.patch(
                "/api/v1/projects/demo/characters/Alice",
                json={"reference_image": {"foo": "bar"}},
            )
            assert resp.status_code == 422
            # entry 未被污染
            assert fake_pm.projects["demo"]["characters"]["Alice"]["reference_image"] == ""

    @pytest.mark.unit
    def test_character_patch_reference_audio_stamps_voice_updated_at(self, monkeypatch):
        """reference_audio 本应只经 update_character_reference_audio 写入，但该字段仍在通用
        PATCH 可写集合内；经这条路径改声音时也须补戳 voice_updated_at，否则存量过渡横幅
        感知不到变化。"""
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"]["Alice"] = {
            "description": "old",
            "character_sheet": "",
            "voice_style": "",
            "reference_image": "",
            "reference_audio": "",
        }
        with client:
            resp = client.patch(
                "/api/v1/projects/demo/characters/Alice",
                json={"reference_audio": "characters/refs_audio/Alice.wav"},
            )
            assert resp.status_code == 200
            entry = fake_pm.projects["demo"]["characters"]["Alice"]
            assert entry["reference_audio"] == "characters/refs_audio/Alice.wav"
            assert isinstance(entry.get("voice_updated_at"), str) and entry["voice_updated_at"]

    @pytest.mark.unit
    def test_character_patch_reference_audio_unchanged_does_not_stamp(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"]["Alice"] = {
            "description": "old",
            "character_sheet": "",
            "voice_style": "",
            "reference_image": "",
            "reference_audio": "characters/refs_audio/Alice.wav",
        }
        with client:
            resp = client.patch(
                "/api/v1/projects/demo/characters/Alice",
                json={"reference_audio": "characters/refs_audio/Alice.wav", "description": "new"},
            )
            assert resp.status_code == 200
            entry = fake_pm.projects["demo"]["characters"]["Alice"]
            assert "voice_updated_at" not in entry

    @pytest.mark.unit
    def test_character_patch_accepts_dismissed_at_matching_current_voice_updated_at(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"]["Alice"] = {
            "description": "old",
            "character_sheet": "",
            "voice_style": "",
            "reference_image": "",
            "reference_audio": "characters/refs_audio/Alice.wav",
            "voice_updated_at": "2026-01-01T00:00:00+00:00",
        }
        with client:
            resp = client.patch(
                "/api/v1/projects/demo/characters/Alice",
                json={"voice_notice_dismissed_at": "2026-01-01T00:00:00+00:00"},
            )
            assert resp.status_code == 200
            entry = fake_pm.projects["demo"]["characters"]["Alice"]
            assert entry["voice_notice_dismissed_at"] == "2026-01-01T00:00:00+00:00"

    @pytest.mark.unit
    def test_character_patch_rejects_dismissed_at_not_matching_voice_updated_at(self, monkeypatch):
        """voice_notice_dismissed_at 只能确认到角色当前实际的 voice_updated_at，不接受任意
        字符串——否则客户端可写入远未来时间戳，永久压制存量过渡横幅。"""
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"]["Alice"] = {
            "description": "old",
            "character_sheet": "",
            "voice_style": "",
            "reference_image": "",
            "reference_audio": "characters/refs_audio/Alice.wav",
            "voice_updated_at": "2026-01-01T00:00:00+00:00",
        }
        with client:
            resp = client.patch(
                "/api/v1/projects/demo/characters/Alice",
                json={"voice_notice_dismissed_at": "9999-12-31T00:00:00+00:00"},
            )
            assert resp.status_code == 422
            # entry 未被污染
            assert "voice_notice_dismissed_at" not in fake_pm.projects["demo"]["characters"]["Alice"]

    @pytest.mark.unit
    def test_unknown_asset_type_raises(self):
        from server.routers._asset_router_factory import build_asset_router

        try:
            build_asset_router(asset_type="unknown", pm_getter=lambda: None)
        except ValueError as e:
            assert "unknown" in str(e)
        else:
            raise AssertionError("should have raised ValueError")


class TestAssetRouterNoLeak:
    """末端 catch-all：未预期异常返回通用 500，内部异常细节不泄露给客户端。

    把 add/update/delete 各自 try 块里最早调用的 pm_getter（get_project_manager）
    monkeypatch 成抛带哨兵串的 RuntimeError，绕过前置的 FileNotFoundError/HTTPException
    分支落到末端 except Exception，断言 500、detail 为通用 i18n 文案且哨兵串不出现在响应体。
    """

    @pytest.mark.unit
    def test_add_unexpected_error_no_leak(self, monkeypatch):
        monkeypatch.setattr(
            characters,
            "get_project_manager",
            lambda: (_ for _ in ()).throw(RuntimeError("LEAK_add")),
        )
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(characters.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/characters", json={"name": "Bob", "description": "x"})
            assert resp.status_code == 500
            assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
            assert "LEAK_add" not in resp.text

    @pytest.mark.unit
    def test_update_unexpected_error_no_leak(self, monkeypatch):
        monkeypatch.setattr(
            characters,
            "get_project_manager",
            lambda: (_ for _ in ()).throw(RuntimeError("LEAK_update")),
        )
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(characters.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        with TestClient(app) as client:
            resp = client.patch("/api/v1/projects/demo/characters/Alice", json={"description": "new"})
            assert resp.status_code == 500
            assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
            assert "LEAK_update" not in resp.text

    @pytest.mark.unit
    def test_delete_unexpected_error_no_leak(self, monkeypatch):
        monkeypatch.setattr(
            characters,
            "get_project_manager",
            lambda: (_ for _ in ()).throw(RuntimeError("LEAK_delete")),
        )
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(characters.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        with TestClient(app) as client:
            resp = client.delete("/api/v1/projects/demo/characters/Alice")
            assert resp.status_code == 500
            assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
            assert "LEAK_delete" not in resp.text


class TestNfcConvergence:
    """PATCH/DELETE 的路径参数与桶 key 形态可以不同：登记闸口落 NFC，存量 key 未迁移。"""

    @pytest.mark.unit
    def test_patch_resolves_across_normalization_forms(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"][_NAME_NFD] = {"name": _NAME_NFD, "description": "legacy"}

        resp = client.patch(f"/api/v1/projects/demo/characters/{_NAME_NFC}", json={"description": "new"})

        assert resp.status_code == 200
        chars = fake_pm.projects["demo"]["characters"]
        assert list(chars) == [_NAME_NFD]
        assert chars[_NAME_NFD]["description"] == "new"

    @pytest.mark.unit
    def test_delete_resolves_across_normalization_forms(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"][_NAME_NFC] = {"name": _NAME_NFC, "description": "d"}

        resp = client.delete(f"/api/v1/projects/demo/characters/{_NAME_NFD}")

        assert resp.status_code == 200
        assert fake_pm.projects["demo"]["characters"] == {}


class TestRenameEndpoint:
    """级联重命名端点：dry_run 预览与执行同一路由，错误按 400/404/409 映射。"""

    @pytest.mark.unit
    def test_rename_executes_and_returns_counts(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"]["Bob"] = {"description": "hero"}

        resp = client.post("/api/v1/projects/demo/characters/Bob/rename", json={"new_name": "Alice"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["dry_run"] is False
        assert (body["episodes"], body["references"], body["files"]) == (2, 5, 3)
        assert body["new_name"] == "Alice"
        assert list(fake_pm.projects["demo"]["characters"]) == ["Alice"]

    @pytest.mark.unit
    def test_rename_dry_run_previews_without_writing(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"]["Bob"] = {"description": "hero"}

        resp = client.post(
            "/api/v1/projects/demo/characters/Bob/rename",
            json={"new_name": "Alice", "dry_run": True},
        )

        assert resp.status_code == 200
        assert resp.json()["dry_run"] is True
        assert list(fake_pm.projects["demo"]["characters"]) == ["Bob"]

    @pytest.mark.unit
    def test_rename_missing_returns_404(self, monkeypatch):
        client, _fake_pm = _client(monkeypatch)
        resp = client.post("/api/v1/projects/demo/characters/Ghost/rename", json={"new_name": "Alice"})
        assert resp.status_code == 404

    @pytest.mark.unit
    def test_rename_conflict_returns_409(self, monkeypatch):
        client, fake_pm = _client(monkeypatch)
        fake_pm.projects["demo"]["characters"]["Bob"] = {"description": "hero"}
        fake_pm.projects["demo"]["characters"][_NAME_NFD] = {"description": "existing"}

        resp = client.post("/api/v1/projects/demo/characters/Bob/rename", json={"new_name": _NAME_NFC})

        assert resp.status_code == 409
        assert list(fake_pm.projects["demo"]["characters"]) == ["Bob", _NAME_NFD]

    @pytest.mark.unit
    def test_rename_invalid_new_name_returns_400(self, monkeypatch):
        client, _fake_pm = _client(monkeypatch)
        resp = client.post("/api/v1/projects/demo/characters/Bob/rename", json={"new_name": "bad/name"})
        assert resp.status_code == 400
