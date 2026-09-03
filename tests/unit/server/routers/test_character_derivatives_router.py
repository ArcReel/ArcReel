"""角色衍生子资源路由：登记、改描述、改名、删除，以及能力未开启的资产类型不暴露该子资源。"""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.data_validator import DataValidator
from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import characters, scenes
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import FakeProjectAssetMutationMixin


class _FakePM(FakeProjectAssetMutationMixin):
    def __init__(self):
        self.projects = {
            "demo": {
                "characters": {
                    "阿岚": {
                        "description": "少女",
                        "character_sheet": "",
                        "voice_style": "",
                        "derivatives": {},
                    }
                },
                "scenes": {"茶楼": {"description": "旧楼", "scene_sheet": ""}},
            }
        }

    def _add_asset(self, asset_type, project_name, name, entry):
        from lib.asset_types import ASSET_SPECS

        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        bucket = self.projects[project_name].setdefault(ASSET_SPECS[asset_type].bucket_key, {})
        if name in bucket:
            return False
        bucket[name] = entry
        return True

    def load_project(self, project_name):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        return self.projects[project_name]

    def update_project(self, project_name, mutate_fn):
        project = self.load_project(project_name)
        mutate_fn(project)
        return project


def _client(monkeypatch, fake_pm, *, router=None, module=characters):
    monkeypatch.setattr(module, "get_project_manager", lambda: fake_pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(router or module.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


def _derivatives(pm: _FakePM) -> dict:
    return pm.projects["demo"]["characters"]["阿岚"]["derivatives"]


class TestCharacterDerivativesRouter:
    def test_add_update_rename_delete_round_trip(self, monkeypatch):
        pm = _FakePM()
        with _client(monkeypatch, pm) as client:
            added = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives",
                json={"name": "战斗装", "description": "换上黑色重甲，其余保持不变"},
            )
            assert added.status_code == 200, added.text
            assert added.json()["character"]["derivatives"]["战斗装"] == {
                "description": "换上黑色重甲，其余保持不变",
                "character_sheet": "",
            }

            updated = client.patch(
                "/api/v1/projects/demo/characters/阿岚/derivatives/战斗装",
                json={"description": "换上银色轻甲"},
            )
            assert updated.status_code == 200, updated.text
            assert _derivatives(pm)["战斗装"]["description"] == "换上银色轻甲"

            renamed = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives/战斗装/rename",
                json={"new_name": "轻甲"},
            )
            assert renamed.status_code == 200, renamed.text
            assert list(_derivatives(pm)) == ["轻甲"]
            assert _derivatives(pm)["轻甲"]["description"] == "换上银色轻甲"

            deleted = client.delete("/api/v1/projects/demo/characters/阿岚/derivatives/轻甲")
            assert deleted.status_code == 200, deleted.text
            assert "轻甲" in deleted.json()["message"]
            assert _derivatives(pm) == {}

    def test_rename_keeps_other_derivatives(self, monkeypatch):
        pm = _FakePM()
        _derivatives(pm).update(
            {
                "战斗装": {"description": "重甲", "character_sheet": ""},
                "便装": {"description": "布衣", "character_sheet": ""},
            }
        )
        with _client(monkeypatch, pm) as client:
            response = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives/战斗装/rename",
                json={"new_name": "铠甲"},
            )
        assert response.status_code == 200, response.text
        assert set(_derivatives(pm)) == {"铠甲", "便装"}

    def test_duplicate_name_is_rejected_with_translated_detail(self, monkeypatch):
        pm = _FakePM()
        _derivatives(pm)["战斗装"] = {"description": "重甲", "character_sheet": ""}
        with _client(monkeypatch, pm) as client:
            created = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives",
                json={"name": "战斗装", "description": "另一套"},
                headers={"Accept-Language": "vi"},
            )
            renamed = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives/战斗装/rename",
                json={"new_name": "战斗装 "},
            )
        assert created.status_code == 422, created.text
        assert "战斗装" in created.json()["detail"]
        assert "phái sinh" in created.json()["detail"]
        # 改名到自身的等价形式不算重名：strip + NFC 后与原键指向同一条目。
        assert renamed.status_code == 200, renamed.text
        assert list(_derivatives(pm)) == ["战斗装"]
        assert _derivatives(pm)["战斗装"]["description"] == "重甲"

    def test_rename_onto_a_sibling_name_is_rejected(self, monkeypatch):
        pm = _FakePM()
        _derivatives(pm).update(
            {
                "战斗装": {"description": "重甲", "character_sheet": ""},
                "便装": {"description": "布衣", "character_sheet": ""},
            }
        )
        with _client(monkeypatch, pm) as client:
            response = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives/战斗装/rename",
                json={"new_name": "便装"},
            )
        assert response.status_code == 422, response.text
        assert _derivatives(pm)["便装"]["description"] == "布衣"

    def test_illegal_names_are_rejected_in_all_locales(self, monkeypatch):
        pm = _FakePM()
        with _client(monkeypatch, pm) as client:
            responses = {
                locale: client.post(
                    "/api/v1/projects/demo/characters/阿岚/derivatives",
                    json={"name": "战斗/装", "description": "x"},
                    headers={"Accept-Language": locale},
                )
                for locale in ("zh", "en", "vi")
            }
        assert [r.status_code for r in responses.values()] == [422, 422, 422]
        details = {locale: response.json()["detail"] for locale, response in responses.items()}
        assert "衍生名" in details["zh"]
        assert "Derivative name" in details["en"]
        assert "Tên phái sinh" in details["vi"]
        assert _derivatives(pm) == {}

    def test_missing_character_and_missing_derivative_are_404(self, monkeypatch):
        pm = _FakePM()
        with _client(monkeypatch, pm) as client:
            no_character = client.post(
                "/api/v1/projects/demo/characters/无名/derivatives",
                json={"name": "战斗装", "description": "x"},
            )
            no_derivative = client.patch(
                "/api/v1/projects/demo/characters/阿岚/derivatives/无此衍生",
                json={"description": "x"},
            )
            no_project = client.delete("/api/v1/projects/missing/characters/阿岚/derivatives/战斗装")
        assert no_character.status_code == 404
        assert no_derivative.status_code == 404
        assert no_project.status_code == 404

    def test_deleting_the_character_takes_its_derivatives_with_it(self, monkeypatch):
        pm = _FakePM()
        pm.expected_delete_asset_table = "characters"
        _derivatives(pm)["战斗装"] = {"description": "重甲", "character_sheet": ""}
        with _client(monkeypatch, pm) as client:
            response = client.delete("/api/v1/projects/demo/characters/阿岚")
        assert response.status_code == 200, response.text
        assert pm.projects["demo"]["characters"] == {}

    def test_scene_router_has_no_derivative_sub_resource(self, monkeypatch):
        pm = _FakePM()
        with _client(monkeypatch, pm, module=scenes) as client:
            response = client.post(
                "/api/v1/projects/demo/scenes/茶楼/derivatives",
                json={"name": "夜景", "description": "x"},
            )
        assert response.status_code == 404
        assert "derivatives" not in pm.projects["demo"]["scenes"]["茶楼"]

    def test_created_character_carries_an_empty_derivative_table(self, monkeypatch):
        pm = _FakePM()
        with _client(monkeypatch, pm) as client:
            response = client.post(
                "/api/v1/projects/demo/characters",
                json={"name": "老陈", "description": "老人", "derivatives": {"伪装": {"description": "x"}}},
            )
        assert response.status_code == 200, response.text
        # 创建请求体里的衍生表不落盘：衍生只经衍生子资源登记。
        assert response.json()["character"]["derivatives"] == {}
        assert pm.projects["demo"]["characters"]["老陈"]["derivatives"] == {}

    def test_scene_create_and_patch_never_persist_derivatives(self, monkeypatch):
        pm = _FakePM()
        with _client(monkeypatch, pm, module=scenes) as client:
            created = client.post(
                "/api/v1/projects/demo/scenes",
                json={"name": "码头", "description": "夜色", "derivatives": {"雨夜": {"description": "x"}}},
            )
            patched = client.patch(
                "/api/v1/projects/demo/scenes/茶楼",
                json={"description": "新描述", "derivatives": {"雨夜": {"description": "x"}}},
            )
        assert created.status_code == 200, created.text
        assert patched.status_code == 200, patched.text
        assert "derivatives" not in pm.projects["demo"]["scenes"]["码头"]
        assert "derivatives" not in pm.projects["demo"]["scenes"]["茶楼"]


class TestCharacterDerivativesPersistence:
    """真实 ProjectManager：衍生落在角色条目里，随角色一起进出 project.json。"""

    def test_derivative_lands_in_project_json_and_passes_validation(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "阿岚", "少女")

        with _client(monkeypatch, pm) as client:
            added = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives",
                json={"name": "战斗装", "description": "换上黑色重甲"},
            )
            assert added.status_code == 200, added.text

            persisted = json.loads((pm.get_project_path("demo") / "project.json").read_text(encoding="utf-8"))
            assert persisted["characters"]["阿岚"]["derivatives"] == {
                "战斗装": {"description": "换上黑色重甲", "character_sheet": ""}
            }
            assert DataValidator(str(pm.projects_root)).validate_project("demo").errors == []

            rejected = client.post(
                "/api/v1/projects/demo/characters/阿岚/derivatives",
                json={"name": "战斗装", "description": "另一套"},
            )
            assert rejected.status_code == 422, rejected.text
            # 被拒的写入整体不落盘：mutation 在项目锁内抛出，project.json 一个字节都没改。
            unchanged = json.loads((pm.get_project_path("demo") / "project.json").read_text(encoding="utf-8"))
            assert unchanged["characters"]["阿岚"]["derivatives"]["战斗装"]["description"] == "换上黑色重甲"

            deleted = client.delete("/api/v1/projects/demo/characters/阿岚")
            assert deleted.status_code == 200, deleted.text

        after = json.loads((pm.get_project_path("demo") / "project.json").read_text(encoding="utf-8"))
        assert after["characters"] == {}
