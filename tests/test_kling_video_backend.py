"""KlingVideoBackend 单元测试（mock httpx，异步轮询，不打真实 HTTP）。

覆盖：JWT / Bearer 双模式鉴权注入、子路径选择（text2video / image2video）、请求体构建、
脱敏日志视图、submit→轮询→下载端到端、provider_job_id 持久化、失败终态、resume 不重提交。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from lib.providers import PROVIDER_KLING
from lib.video_backends.base import VideoCapability, VideoCapabilityError, VideoGenerationRequest
from lib.video_backends.kling import KlingVideoBackend

_SECRET = "s" * 40


def _resp(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _submit(task_id: str = "t-1") -> dict:
    return {"code": 0, "message": "SUCCEED", "data": {"task_id": task_id, "task_status": "submitted"}}


def _query(status: str, url: str = "", status_msg: str = "") -> dict:
    data: dict = {"task_id": "t-1", "task_status": status, "task_status_msg": status_msg}
    if url:
        data["task_result"] = {"videos": [{"id": "v1", "url": url}]}
    return {"code": 0, "message": "SUCCEED", "data": data}


def _client(*, post=None, get=None) -> AsyncMock:
    c = AsyncMock()
    if post is not None:
        c.post = post
    if get is not None:
        c.get = get
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=None)
    return c


def _jwt_backend(model: str | None = None) -> KlingVideoBackend:
    return KlingVideoBackend(auth_mode="jwt", access_key="ak-1", secret_key=_SECRET, model=model)


def _bearer_backend(model: str | None = None) -> KlingVideoBackend:
    return KlingVideoBackend(auth_mode="bearer", api_key="static-key", model=model)


def _request(tmp_path: Path, **overrides) -> VideoGenerationRequest:
    kwargs: dict = {
        "prompt": "a cat walking",
        "output_path": tmp_path / "out.mp4",
        "duration_seconds": 5,
        "aspect_ratio": "9:16",
    }
    kwargs.update(overrides)
    return VideoGenerationRequest(**kwargs)


class TestConstructionAndCapabilities:
    def test_name_and_default_model(self):
        b = _jwt_backend()
        assert b.name == PROVIDER_KLING
        assert b.model == "kling-v2-5-turbo"

    def test_jwt_missing_credentials_raises(self):
        with pytest.raises(ValueError):
            KlingVideoBackend(auth_mode="jwt", access_key="ak", secret_key=None)

    def test_bearer_missing_api_key_raises(self):
        with pytest.raises(ValueError):
            KlingVideoBackend(auth_mode="bearer", api_key=None)

    def test_unknown_auth_mode_raises(self):
        with pytest.raises(ValueError):
            KlingVideoBackend(auth_mode="oauth", api_key="k")

    def test_capabilities_t2v_and_i2v(self):
        caps = _jwt_backend().capabilities
        assert VideoCapability.TEXT_TO_VIDEO in caps
        assert VideoCapability.IMAGE_TO_VIDEO in caps

    def test_video_capabilities_first_and_last_frame(self):
        caps = _jwt_backend().video_capabilities
        assert caps.first_frame is True
        assert caps.last_frame is True
        # turbo 不建模参考图（多图主体留后续片）
        assert caps.reference_images is False


class TestAuthHeaders:
    def test_jwt_mode_signs_bearer_token(self):
        headers = _jwt_backend()._headers()
        assert headers["Content-Type"] == "application/json"
        token = headers["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, _SECRET, algorithms=["HS256"], options={"verify_exp": False})
        assert claims["iss"] == "ak-1"

    def test_bearer_mode_uses_static_key(self):
        # bearer 模式旁路 JWT 管理器：Authorization 是静态 key，非签名 token
        headers = _bearer_backend()._headers()
        assert headers["Authorization"] == "Bearer static-key"


class TestPayloadBuilding:
    def test_text2video_no_image(self, tmp_path):
        subpath, payload = _jwt_backend()._build_payload(_request(tmp_path))
        assert subpath == "text2video"
        assert payload["model_name"] == "kling-v2-5-turbo"
        assert payload["mode"] == "std"
        assert payload["duration"] == "5"  # 字符串
        assert payload["aspect_ratio"] == "9:16"
        assert "image" not in payload

    def test_service_tier_pro_maps_to_mode_pro(self, tmp_path):
        _, payload = _jwt_backend()._build_payload(_request(tmp_path, service_tier="pro"))
        assert payload["mode"] == "pro"

    def test_service_tier_default_maps_to_std(self, tmp_path):
        _, payload = _jwt_backend()._build_payload(_request(tmp_path, service_tier="default"))
        assert payload["mode"] == "std"

    def test_image2video_embeds_base64_frame(self, tmp_path):
        img = tmp_path / "first.png"
        img.write_bytes(b"\x89PNG\r\n")
        subpath, payload = _jwt_backend()._build_payload(_request(tmp_path, start_image=img))
        assert subpath == "image2video"
        assert isinstance(payload["image"], str) and payload["image"]
        # 纯 base64，无 data URI 前缀
        assert not payload["image"].startswith("data:")
        assert "image_tail" not in payload

    def test_image2video_with_end_frame(self, tmp_path):
        first = tmp_path / "first.png"
        last = tmp_path / "last.png"
        first.write_bytes(b"\x89PNG\r\n1")
        last.write_bytes(b"\x89PNG\r\n2")
        _, payload = _jwt_backend()._build_payload(_request(tmp_path, start_image=first, end_image=last))
        assert "image" in payload and "image_tail" in payload

    def test_unreadable_start_image_raises(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend()._build_payload(_request(tmp_path, start_image=tmp_path / "nope.png"))
        assert exc.value.code == "video_start_image_unreadable"


class TestSafeLogView:
    def test_no_base64_or_prompt_leaks(self, tmp_path):
        img = tmp_path / "f.png"
        img.write_bytes(b"\x89PNG\r\n")
        b = _jwt_backend()
        subpath, payload = b._build_payload(_request(tmp_path, start_image=img))
        view = b._safe_log_view(subpath, payload)
        # 仅标量；base64 与 prompt 不展开
        assert view["has_image"] is True
        assert view["prompt_len"] == len("a cat walking")
        assert "image" not in view
        assert "prompt" not in view
        assert all(isinstance(v, (str, int, bool)) for v in view.values())


class TestGenerateHappyPath:
    async def test_submit_poll_download(self, tmp_path):
        post = AsyncMock(return_value=_resp(_submit("task-9")))
        get = AsyncMock(
            side_effect=[
                _resp(_query("processing")),
                _resp(_query("succeed", url="https://x/final.mp4")),
            ]
        )
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()) as dl,
        ):
            result = await _jwt_backend().generate(_request(tmp_path))

        assert result.provider == PROVIDER_KLING
        assert result.task_id == "task-9"
        assert result.video_uri == "https://x/final.mp4"
        assert result.generate_audio is False  # turbo 无音频
        dl.assert_awaited_once()
        # text2video 提交端点
        assert post.await_args.args[0].endswith("/videos/text2video")

    async def test_jwt_injected_on_submit(self, tmp_path):
        captured: dict = {}

        async def _post(url, json, headers):
            captured["headers"] = headers
            return _resp(_submit())

        post = AsyncMock(side_effect=_post)
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/v.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            await _jwt_backend().generate(_request(tmp_path))

        token = captured["headers"]["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, _SECRET, algorithms=["HS256"], options={"verify_exp": False})
        assert claims["iss"] == "ak-1"

    async def test_bearer_static_key_on_submit(self, tmp_path):
        captured: dict = {}

        async def _post(url, json, headers):
            captured["headers"] = headers
            return _resp(_submit())

        post = AsyncMock(side_effect=_post)
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/v.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            await _bearer_backend().generate(_request(tmp_path))
        assert captured["headers"]["Authorization"] == "Bearer static-key"

    async def test_failed_status_raises(self, tmp_path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_query("failed", status_msg="content rejected")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="content rejected"):
                await _jwt_backend().generate(_request(tmp_path))

    async def test_persists_provider_job_id_when_task_id_present(self, tmp_path):
        post = AsyncMock(return_value=_resp(_submit("task-x")))
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/v.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
            patch("lib.video_backends.kling.persist_provider_job_id", new=AsyncMock()) as persist,
        ):
            await _jwt_backend().generate(_request(tmp_path, task_id="local-task-1"))
        persist.assert_awaited_once()
        assert persist.await_args is not None
        assert persist.await_args.args[1] == "task-x"


class TestResume:
    async def test_resume_polls_without_resubmit(self, tmp_path):
        post = AsyncMock()  # must NOT be called
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()) as dl,
        ):
            result = await _jwt_backend().resume_video("task-resume", _request(tmp_path))

        post.assert_not_called()
        assert result.task_id == "task-resume"
        assert result.video_uri == "https://x/r.mp4"
        # 无首帧 → text2video 查询端点
        assert get.await_args.args[0].endswith("/videos/text2video/task-resume")
        dl.assert_awaited_once()

    async def test_resume_image2video_subpath_from_request(self, tmp_path):
        img = tmp_path / "f.png"
        img.write_bytes(b"\x89PNG\r\n")
        post = AsyncMock()
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            await _jwt_backend().resume_video("task-r2", _request(tmp_path, start_image=img))
        assert get.await_args.args[0].endswith("/videos/image2video/task-r2")
