"""消息改写端点的 HTTP 契约：响应形态与各拒绝理由的可辨识性。"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.i18n import get_translator
from server.agent_runtime.service import (
    InterruptSettleTimeout,
    PendingQuestionError,
    RewriteAnchorError,
    RewriteUnavailableError,
    SessionSupersededError,
)
from server.agent_runtime.session_branch import SessionBranchError
from server.agent_runtime.session_manager import SessionBusyError, SessionCapacityError
from server.auth import CurrentUserInfo, get_current_user, get_current_user_flexible
from server.error_handlers import register_error_handlers
from server.routers import assistant
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.conftest import make_translator

pytestmark = pytest.mark.unit

PROJECT = "demo"
PREFIX = f"/api/v1/projects/{PROJECT}/assistant"
ORIGIN = "origin-session"
REWRITE_URL = f"{PREFIX}/sessions/{ORIGIN}/rewrite"

_FAKE_USER = CurrentUserInfo(id="default", sub="testuser", role="admin")
_T = make_translator()


def _override_translator():
    return make_translator()


def _build_client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    app.dependency_overrides[get_current_user_flexible] = lambda: _FAKE_USER
    app.dependency_overrides[get_translator] = _override_translator
    app.include_router(
        assistant.router, prefix="/api/v1/projects/{project_name}/assistant", dependencies=AUTH_DEPENDENCIES
    )
    app.include_router(assistant.self_auth_router, prefix="/api/v1/projects/{project_name}/assistant")
    return TestClient(app)


def _post(rewrite_result, body: dict | None = None):
    """以替身服务发一次改写请求；``rewrite_result`` 为返回值或抛出的异常。"""
    payload = {"anchor_entry_uuid": "user-abc", "content": "改写后的指令", "client_key": "ck-1"}
    payload.update(body or {})
    fake = AsyncMock()
    if isinstance(rewrite_result, BaseException):
        fake.side_effect = rewrite_result
    else:
        fake.return_value = rewrite_result
    with patch.object(assistant.assistant_service, "rewrite_message", new=fake):
        with _build_client() as client:
            return client.post(REWRITE_URL, json=payload), fake


class TestRewriteContract:
    def test_accepted_response_carries_new_session_and_authoritative_entry(self):
        entry = {"uuid": "user-new", "seq": 3, "type": "user"}
        response, fake = _post(
            {
                "status": "accepted",
                "session_id": "branch-session",
                "origin_session_id": ORIGIN,
                "entry": entry,
            }
        )

        assert response.status_code == 200
        payload = response.json()
        # 与发送端点同构：status + session_id + 权威 entry；session_id 是新分支。
        assert payload["status"] == "accepted"
        assert payload["session_id"] == "branch-session"
        assert payload["origin_session_id"] == ORIGIN
        assert payload["entry"] == entry

        kwargs = fake.await_args.kwargs
        assert fake.await_args.args == (PROJECT, ORIGIN)
        assert kwargs["anchor_entry_uuid"] == "user-abc"
        assert kwargs["content"] == "改写后的指令"
        assert kwargs["client_key"] == "ck-1"

    def test_images_and_absent_client_key_are_forwarded(self):
        response, fake = _post(
            {"status": "accepted", "session_id": "branch-session", "origin_session_id": ORIGIN, "entry": None},
            {"client_key": None, "images": [{"data": "AAA", "media_type": "image/png"}]},
        )

        assert response.status_code == 200
        kwargs = fake.await_args.kwargs
        assert kwargs["client_key"] is None
        assert [img.media_type for img in kwargs["images"]] == ["image/png"]

    def test_anchor_missing_from_body_is_rejected_before_the_service(self):
        response, fake = _post({}, {"anchor_entry_uuid": ""})

        assert response.status_code == 422
        fake.assert_not_awaited()


class TestRewriteRejections:
    """每种拒绝理由都要能从响应里分辨出来——前端据此决定下一步提示。"""

    @pytest.mark.parametrize(
        ("exc", "status", "key"),
        [
            (RewriteAnchorError("bad anchor"), 400, "rewrite_anchor_invalid"),
            (PendingQuestionError("pending"), 409, "rewrite_blocked_by_question"),
            (SessionSupersededError("superseded"), 409, "session_already_superseded"),
            (RewriteUnavailableError("no store"), 503, "rewrite_unavailable"),
            (InterruptSettleTimeout("stuck"), 504, "rewrite_interrupt_timeout"),
            (SessionBranchError("copy failed"), 500, "rewrite_failed"),
            (SessionCapacityError("full"), 503, "session_capacity_exceeded"),
            (SessionBusyError("busy"), 409, "session_busy"),
            (FileNotFoundError("gone"), 404, "session_or_project_not_found"),
            (ValueError("empty"), 400, "request_invalid"),
        ],
    )
    def test_rejection_maps_to_its_own_status_and_message(self, exc, status, key):
        response, _ = _post(exc)

        assert response.status_code == status
        assert response.json()["detail"] == _T(key)

    def test_unexpected_failure_does_not_leak_internals(self):
        response, _ = _post(RuntimeError("sentinel-internal-detail"))

        assert response.status_code == 500
        assert response.json()["detail"] == _T("internal_server_error")
        assert "sentinel-internal-detail" not in response.text
