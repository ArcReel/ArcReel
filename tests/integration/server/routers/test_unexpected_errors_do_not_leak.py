"""Tests for unexpected_errors_do_not_leak (split from test_projects_router.py)."""

from lib.i18n.zh import errors as zh_errors
from server.routers import projects
from tests.integration.server.routers.projects_router_support import (
    _client,
    _FakePM,
)


def _raise(sentinel):
    """返回一个「一调用即抛 RuntimeError(sentinel)」的可调用，用于替换 try 块内最早被命中的内部函数。

    RuntimeError 不会被路由前面的 except FileNotFoundError / ValueError / HTTPException 捕获，
    必然落到 except Exception 兜底分支，从而走到「通用 500 + 不回显内部异常」路径。
    """

    def _factory(*_a, **_k):
        raise RuntimeError(sentinel)

    return _factory


class TestUnexpectedErrorsDoNotLeak:
    """未预期异常统一映射为通用 500，且响应体不得回显内部异常文本（不泄露）。

    每个端点用独一无二的哨兵串替换 try 块内最早被调用的内部函数，再断言：
    响应 500 且哨兵串不出现在响应体里。
    """

    def _body(self, resp):
        # detail 在普通端点是 json["detail"]，import 端点用 JSONResponse 同样有 detail；
        # 这里直接断言整段原始文本，覆盖 detail / errors / warnings 任意字段都不泄露。
        return resp.text

    def test_create_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_create_project"
        client = _client(monkeypatch, _FakePM(tmp_path))
        # _sync 里最早命中 get_project_manager()，RuntimeError 绕过 ValueError/HTTPException 分支
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
        client = _client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.get("/api/v1/projects/ready")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_project"
        client = _client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch("/api/v1/projects/ready", json={"title": "X"})
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_delete_project_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_delete_project"
        client = _client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.delete("/api/v1/projects/remove-me")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_get_script_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_get_script"
        client = _client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.get("/api/v1/projects/ready/scripts/episode_1.json")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_scene_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_scene"
        client = _client(monkeypatch, _FakePM(tmp_path))
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
        client = _client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch(
                "/api/v1/projects/ready/script-shots/shot-1",
                json={"script_file": "scripts/episode_1.json", "updates": {"note": "x"}},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_reorder_shots_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_reorder_shots"
        client = _client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post(
                "/api/v1/projects/ready/script-shots/reorder",
                json={"script_file": "scripts/episode_1.json", "shot_ids": ["a", "b"]},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_update_segment_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_segment"
        client = _client(monkeypatch, _FakePM(tmp_path))
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
        client = _client(monkeypatch, _FakePM(tmp_path))
        # title 非空校验在 try 前；_sync 里最早命中 get_project_manager()
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch("/api/v1/projects/ready/episodes/1", json={"title": "新标题"})
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_set_project_source_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_set_source"
        client = _client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            # content 走 multipart form；get_project_manager() 在 try 内最早被调用
            resp = client.post(
                "/api/v1/projects/ready/source",
                data={"content": "正文", "generate_overview": "false"},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_set_project_source_overview_error_does_not_leak_path(self, tmp_path, monkeypatch):
        # 概览生成是上传的可选后续：失败时上传仍成功（200），错误只降级回传 overview_error。
        # 底层异常文本可能携带服务器绝对路径，该分支不得把裸 str(e) 透传给客户端。
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post(
                "/api/v1/projects/leaky/source",
                data={"content": "正文", "generate_overview": "true"},
            )
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["success"] is True
            assert payload["overview"] is None
            # 上传成功后合法回传的 filename 是 novel.txt，不在泄漏范畴；
            # 泄漏关注的是异常文本携带的服务器绝对路径片段。
            assert "/Users" not in resp.text
            assert "/var" not in resp.text
            assert "/secret/" not in resp.text
            # 回传的是翻译后的通用文案，而非裸异常串
            assert payload["overview_error"] == zh_errors.MESSAGES["overview_generation_failed"]

    def test_generate_overview_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_generate_overview"
        client = _client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post("/api/v1/projects/ready/generate-overview")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_generate_overview_corrupted_project_maps_to_500_not_provider_error(self, tmp_path, monkeypatch):
        # JSONDecodeError 是 ValueError 子类：损坏的 project.json 不能被 except ValueError
        # 误判为「未配置文本供应商」，须先于其拦截并映射为通用 500
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post("/api/v1/projects/corrupted/generate-overview")
            assert resp.status_code == 500
            assert "配置文本供应商" not in self._body(resp)

    def test_generate_overview_schema_failure_maps_to_ai_response_invalid(self, tmp_path, monkeypatch):
        # pydantic ValidationError 也是 ValueError 子类：模型输出不合 schema 时须命中
        # 「AI 响应无效」专属分支，不能被通用 ValueError 处理误判为「未配置文本供应商」
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post("/api/v1/projects/bad-schema/generate-overview")
            assert resp.status_code == 400
            assert resp.json()["detail"] == zh_errors.MESSAGES["overview_ai_response_invalid"]

    def test_generate_overview_invalid_project_name_maps_to_400_not_provider_error(self, tmp_path, monkeypatch):
        # get_project_path 抛出的非法项目名 ValueError（路径穿越等）不能被 generate_overview()
        # 内部供应商解析链路的 except ValueError 误判为「未配置文本供应商」
        client = _client(monkeypatch, _FakePM(tmp_path))
        with client:
            resp = client.post("/api/v1/projects/illegal-name/generate-overview")
            assert resp.status_code == 400
            assert "配置文本供应商" not in self._body(resp)
            assert "illegal-name" in self._body(resp)

    def test_update_overview_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_update_overview"
        client = _client(monkeypatch, _FakePM(tmp_path))
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.patch("/api/v1/projects/ready/overview", json={"synopsis": "新简介"})
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_create_export_token_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_export_token"
        client = _client(monkeypatch, _FakePM(tmp_path))
        # scope 合法（默认 full）；_sync 里最早命中 get_project_manager()
        monkeypatch.setattr(projects, "get_project_manager", _raise(sentinel))
        with client:
            resp = client.post("/api/v1/projects/ready/export/token")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_export_project_archive_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_export_archive"
        client = _client(monkeypatch, _FakePM(tmp_path))
        # download_token 校验先放行，再让归档服务抛 RuntimeError 落到兜底
        monkeypatch.setattr(projects, "verify_download_token", lambda token, name: {"sub": "u"})
        monkeypatch.setattr(projects, "get_archive_service", _raise(sentinel))
        with client:
            resp = client.get("/api/v1/projects/ready/export?download_token=tok&scope=full")
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)

    def test_import_project_archive_unexpected_error_maps_to_500(self, tmp_path, monkeypatch):
        sentinel = "LEAKED_SECRET_import_archive"
        client = _client(monkeypatch, _FakePM(tmp_path))
        # 上传副本写盘成功后，_sync 调归档服务抛 RuntimeError，落到 JSONResponse(500) 兜底
        monkeypatch.setattr(projects, "get_archive_service", _raise(sentinel))
        with client:
            resp = client.post(
                "/api/v1/projects/import",
                files={"file": ("demo.zip", b"PK\x03\x04not-a-real-zip", "application/zip")},
            )
            assert resp.status_code == 500
            assert sentinel not in self._body(resp)
