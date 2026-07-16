"""app 级异常处理器测试：状态码映射、Accept-Language 翻译、脱敏。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.api_errors import ApiError, BadRequestError, NotFoundError
from lib.generation_queue_client import TaskSpecValidationError
from lib.script_editor import ScriptEditError
from server.error_handlers import register_error_handlers

_SERVER_PATH = "/Users/someone/projects/demo/episode_1.json"


def _make_client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/api-error-404")
    async def _api_error_404():
        raise NotFoundError("segment_not_found", id="E1S01")

    @app.get("/api-error-400")
    async def _api_error_400():
        raise BadRequestError("audio_provider_not_configured")

    @app.get("/api-error-custom-status")
    async def _api_error_custom():
        raise ApiError("internal_server_error", status_code=503)

    @app.get("/task-spec-error")
    async def _task_spec_error():
        raise TaskSpecValidationError("prompt_text_empty")

    @app.get("/script-edit-error")
    async def _script_edit_error():
        raise ScriptEditError("segments 必须是列表，当前为 NoneType")

    @app.get("/file-not-found")
    async def _file_not_found():
        raise FileNotFoundError(f"剧本文件不存在: {_SERVER_PATH}")

    @app.get("/unexpected")
    async def _unexpected():
        raise RuntimeError(f"boom at {_SERVER_PATH}")

    return TestClient(app, raise_server_exceptions=False)


class TestApiErrorHandler:
    def test_not_found_translated_zh_default(self):
        client = _make_client()
        resp = client.get("/api-error-404")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "片段 'E1S01' 不存在"

    def test_bad_request_400(self):
        client = _make_client()
        resp = client.get("/api-error-400")
        assert resp.status_code == 400
        assert "音频" in resp.json()["detail"]

    def test_accept_language_en(self):
        client = _make_client()
        resp = client.get("/api-error-404", headers={"Accept-Language": "en-US,en;q=0.9"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Segment 'E1S01' does not exist"

    def test_accept_language_vi(self):
        client = _make_client()
        resp = client.get("/api-error-404", headers={"Accept-Language": "vi"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Đoạn 'E1S01' không tồn tại"

    def test_custom_status_code(self):
        client = _make_client()
        resp = client.get("/api-error-custom-status")
        assert resp.status_code == 503


class TestLibExceptionHandlers:
    def test_task_spec_validation_error_400(self):
        client = _make_client()
        resp = client.get("/task-spec-error")
        assert resp.status_code == 400
        # detail 是翻译后的成品文案，不是裸 code
        assert resp.json()["detail"] != "prompt_text_empty"

    def test_script_edit_error_400(self):
        client = _make_client()
        resp = client.get("/script-edit-error")
        assert resp.status_code == 400
        assert "损坏" in resp.json()["detail"]

    def test_file_not_found_404_hides_server_path(self):
        client = _make_client()
        resp = client.get("/file-not-found")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "请求的资源不存在"
        assert "/Users" not in resp.text

    def test_unexpected_exception_500_hides_details(self):
        client = _make_client()
        resp = client.get("/unexpected")
        assert resp.status_code == 500
        assert resp.json()["detail"] == "服务器内部错误，请稍后重试"
        assert "boom" not in resp.text
        assert "/Users" not in resp.text


class TestRealAppRegistration:
    def test_server_app_registers_all_handlers(self):
        from server.app import app as real_app

        for exc_type in (ApiError, TaskSpecValidationError, ScriptEditError, FileNotFoundError, Exception):
            assert exc_type in real_app.exception_handlers, f"{exc_type} 未注册 app 级 handler"
