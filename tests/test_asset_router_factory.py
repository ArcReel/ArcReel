"""统一资产工厂单测：覆盖 character 的 extra 字段透传与 409 冲突响应。

scenes/props 的 CRUD 行为由 test_scenes_router / test_props_router 覆盖；本文件聚焦
factory 引入的新能力（character extras + extra='allow' 创建语义）。
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import characters
from tests.conftest import make_translator

# 兜底 500 的默认 locale 文案：测试未覆盖 get_translator，端点回落到 DEFAULT_LOCALE("zh")，
# 与 make_translator() 默认 locale 一致。
_INTERNAL_ERROR_DETAIL = make_translator()("internal_server_error")


class _FakePM:
    def __init__(self):
        self.projects = {"demo": {"characters": {}}}

    def _add_asset(self, asset_type, project_name, name, entry):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        bucket = self.projects[project_name].setdefault("characters", {})
        if name in bucket:
            return False
        bucket[name] = entry
        return True

    def load_project(self, project_name):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        return self.projects[project_name]

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
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(characters.router, prefix="/api/v1")
    return TestClient(app), fake_pm


class TestAssetRouterFactory:
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

    def test_add_unexpected_error_no_leak(self, monkeypatch):
        monkeypatch.setattr(
            characters,
            "get_project_manager",
            lambda: (_ for _ in ()).throw(RuntimeError("LEAK_add")),
        )
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(characters.router, prefix="/api/v1")
        with TestClient(app) as client:
            resp = client.post("/api/v1/projects/demo/characters", json={"name": "Bob", "description": "x"})
            assert resp.status_code == 500
            assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
            assert "LEAK_add" not in resp.text

    def test_update_unexpected_error_no_leak(self, monkeypatch):
        monkeypatch.setattr(
            characters,
            "get_project_manager",
            lambda: (_ for _ in ()).throw(RuntimeError("LEAK_update")),
        )
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(characters.router, prefix="/api/v1")
        with TestClient(app) as client:
            resp = client.patch("/api/v1/projects/demo/characters/Alice", json={"description": "new"})
            assert resp.status_code == 500
            assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
            assert "LEAK_update" not in resp.text

    def test_delete_unexpected_error_no_leak(self, monkeypatch):
        monkeypatch.setattr(
            characters,
            "get_project_manager",
            lambda: (_ for _ in ()).throw(RuntimeError("LEAK_delete")),
        )
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.include_router(characters.router, prefix="/api/v1")
        with TestClient(app) as client:
            resp = client.delete("/api/v1/projects/demo/characters/Alice")
            assert resp.status_code == 500
            assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL
            assert "LEAK_delete" not in resp.text
