"""projects 路由的未预期异常不泄漏内部细节。"""

from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.i18n.zh import errors as zh_errors
from server.routers import projects
from tests.integration.server.routers.projects_router_support import (
    _FakePM,
    build_projects_client,
    override,
)


def _raise(sentinel):
    """返回一个「一调用即抛 RuntimeError(sentinel)」的可调用，用于替换 try 块内最早被命中的内部函数。

    RuntimeError 不会被路由前面的 except FileNotFoundError / ValueError / HTTPException 捕获，
    必然落到 except Exception 兜底分支，从而走到「通用 500 + 不回显内部异常」路径。
    """

    def _factory(*_a, **_k):
        raise RuntimeError(sentinel)

    return _factory


def _raising_service(sentinel):
    """服务替身：任意方法一被调用即抛 RuntimeError(sentinel)。

    经依赖覆盖注入，异常因而落在处理器体内，与被替换的内部函数同一位置。
    """

    class _Raising:
        def __getattr__(self, _name):
            def _call(*_a, **_k):
                raise RuntimeError(sentinel)

            return _call

    return _Raising()


class TestUnexpectedErrorsDoNotLeak:
    """未预期异常统一映射为通用 500，且响应体不得回显内部异常文本（不泄露）。

    每个端点用独一无二的哨兵串替换 try 块内最早被调用的内部函数，再断言：
    响应 500 且哨兵串不出现在响应体里。
    """

    def _body(self, resp):
        return resp.text

    def test_create_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_create_project"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post(
                "/api/v1/projects",
                json={"generation_mode": "storyboard", "name": "demo", "title": "T", "content_mode": "narration"},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_get_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_get_project"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.get("/api/v1/projects/ready")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_project"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"title": "X"})
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_delete_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_delete_project"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.delete("/api/v1/projects/remove-me")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_get_script_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_get_script"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.get("/api/v1/projects/ready/scripts/episode_1.json")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_scene_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_scene"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch(
                "/api/v1/projects/ready/script-scenes/001",
                json={"script_file": "scripts/episode_1.json", "updates": {"note": "x"}},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_shot_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_shot"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch(
                "/api/v1/projects/ready/script-shots/shot-1",
                json={"script_file": "scripts/episode_1.json", "updates": {"note": "x"}},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_move_script_item_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_move_script_item"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post(
                "/api/v1/projects/ready/script-items/a/move",
                json={"script_file": "scripts/episode_1.json", "after_id": "b"},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_segment_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_segment"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch(
                "/api/v1/projects/ready/segments/E1S01",
                json={"script_file": "scripts/narration.json", "duration_seconds": 5},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_episode_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_episode"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch("/api/v1/projects/ready/episodes/1", json={"title": "新标题"})
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_set_project_source_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_set_source"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post(
                "/api/v1/projects/ready/source",
                data={"content": "正文", "generate_overview": "false"},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_set_project_source_overview_error_does_not_leak_path(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post(
                "/api/v1/projects/leaky/source",
                data={"content": "正文", "generate_overview": "true"},
            )
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["success"] is True
            assert payload["overview"] is None
            assert "/Users" not in resp.text
            assert "/var" not in resp.text
            assert "/secret/" not in resp.text
            assert payload["overview_error"] == zh_errors.MESSAGES["overview_generation_failed"]

    def test_set_project_source_overview_model_not_in_catalog_guides_reselect(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post(
                "/api/v1/projects/retired-upload/source",
                data={"content": "正文", "generate_overview": "true"},
            )
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["overview"] is None
            assert "重新选择模型" in payload["overview_error"]

    def test_generate_overview_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_generate_overview"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post("/api/v1/projects/ready/generate-overview")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_generate_overview_corrupted_project_maps_to_500_not_provider_error(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post("/api/v1/projects/corrupted/generate-overview")
            assert resp.status_code == 500
            assert "配置文本供应商" not in self._body(resp)

    def test_generate_overview_model_not_in_catalog_asks_to_reselect(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post("/api/v1/projects/retired-model/generate-overview", headers={"Accept-Language": "en"})
            assert resp.status_code == 400
            detail = resp.json()["detail"]
            assert "doubao-seed-1-8-251228" in detail
            assert "re-select a model" in detail

    def test_generate_overview_schema_failure_maps_to_ai_response_invalid(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post("/api/v1/projects/bad-schema/generate-overview")
            assert resp.status_code == 400
            assert resp.json()["detail"] == zh_errors.MESSAGES["overview_ai_response_invalid"]

    async def test_generate_overview_truncated_maps_to_truncation_problem(
        self, tmp_path, monkeypatch, db_factory, set_error_handler_sessions
    ):
        async with db_factory() as session:
            for provider_id, name in ((3, "My Text Model"), (4, "Other Text Model")):
                session.add(
                    CustomProvider(
                        id=provider_id,
                        display_name=f"Provider {provider_id}",
                        discovery_format="openai",
                        base_url="https://example.test",
                        api_key="test",
                    )
                )
                await session.flush()
                session.add(
                    CustomProviderModel(
                        provider_id=provider_id, model_id="my-llm", display_name=name, endpoint="openai-chat"
                    )
                )
            await session.commit()
        set_error_handler_sessions(db_factory)
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post("/api/v1/projects/truncated/generate-overview")
            assert resp.status_code == 422
            body = resp.json()
            assert body["detail"] == zh_errors.MESSAGES["text_output_truncated"].format(model="My Text Model")
            assert body["diagnostic"]["code"] == "text_output_truncated"
            assert body["diagnostic"]["params"] == {"provider_id": "custom-3", "model": "my-llm", "custom_model": True}

    def test_generate_overview_invalid_project_name_maps_to_400_not_provider_error(self, tmp_path, monkeypatch):
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post("/api/v1/projects/illegal-name/generate-overview")
            assert resp.status_code == 400
            assert "配置文本供应商" not in self._body(resp)
            assert "illegal-name" in self._body(resp)

    def test_update_overview_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_overview"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch("/api/v1/projects/ready/overview", json={"synopsis": "新简介"})
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_create_export_token_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_export_token"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        override(client, projects.get_archive_service, lambda: _raising_service(sentinel))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post("/api/v1/projects/ready/export/token")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_export_project_archive_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_export_archive"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "verify_download_token", lambda token, name: {"sub": "u"})
        override(client, projects.get_archive_service, lambda: _raising_service(sentinel))
        with client:
            resp = client.get("/api/v1/projects/ready/export?download_token=tok&scope=full")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_import_project_archive_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_import_archive"
        client = build_projects_client(monkeypatch, _FakePM(tmp_path))
        override(client, projects.get_archive_service, lambda: _raising_service(sentinel))
        with client:
            resp = client.post(
                "/api/v1/projects/import",
                files={"file": ("demo.zip", b"PK\x03\x04not-a-real-zip", "application/zip")},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)
