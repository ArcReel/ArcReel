"""KlingImageBackend 单元测试（mock httpx，异步轮询，不打真实 HTTP）。

覆盖：JWT / Bearer 双模式鉴权注入、请求体构建（文生图 / 图生图 image 数组）、参考图上限截断、
缺失参考图 fail-loud、脱敏日志视图、submit→轮询→取 image_url→下载端到端、失败终态、多图取首张。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt
import pytest

from lib.image_backends.base import (
    ImageCapability,
    ImageCapabilityError,
    ImageGenerationRequest,
    ReferenceImage,
)
from lib.image_backends.kling import KlingImageBackend
from lib.providers import PROVIDER_KLING

_SECRET = "s" * 40


def _resp(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _submit(task_id: str = "t-1") -> dict:
    return {"code": 0, "message": "SUCCEED", "data": {"task_id": task_id, "task_status": "submitted"}}


def _query(status: str, urls: list[str] | None = None, status_msg: str = "") -> dict:
    data: dict = {"task_id": "t-1", "task_status": status, "task_status_msg": status_msg}
    if urls:
        data["task_result"] = {"images": [{"index": i, "url": u} for i, u in enumerate(urls)]}
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


def _jwt_backend(model: str | None = None, api_model_name: str | None = None) -> KlingImageBackend:
    return KlingImageBackend(
        auth_mode="jwt", access_key="ak-1", secret_key=_SECRET, model=model, api_model_name=api_model_name
    )


def _bearer_backend(model: str | None = None) -> KlingImageBackend:
    return KlingImageBackend(auth_mode="bearer", api_key="static-key", model=model)


def _request(tmp_path: Path, **overrides) -> ImageGenerationRequest:
    kwargs: dict = {
        "prompt": "a hero portrait",
        "output_path": tmp_path / "out.png",
        "aspect_ratio": "9:16",
    }
    kwargs.update(overrides)
    return ImageGenerationRequest(**kwargs)


def _ref(tmp_path: Path, name: str) -> ReferenceImage:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n" + name.encode())
    return ReferenceImage(path=str(p))


class TestConstructionAndCapabilities:
    def test_name_and_default_model(self):
        b = _jwt_backend()
        assert b.name == PROVIDER_KLING
        assert b.model == "kling-image-o1"

    def test_explicit_model_keeps_registry_key(self):
        # model 属性 = registry 键名（result.model / 计费查表键），即使带 API 名别名。
        b = _jwt_backend("kling-v3-omni-image", api_model_name="kling-v3-omni")
        assert b.model == "kling-v3-omni-image"

    def test_jwt_missing_credentials_raises(self):
        with pytest.raises(ValueError):
            KlingImageBackend(auth_mode="jwt", access_key="ak", secret_key=None)

    def test_bearer_missing_api_key_raises(self):
        with pytest.raises(ValueError):
            KlingImageBackend(auth_mode="bearer", api_key=None)

    def test_unknown_auth_mode_raises(self):
        with pytest.raises(ValueError):
            KlingImageBackend(auth_mode="oauth", api_key="k")

    def test_capabilities_t2i_and_i2i(self):
        caps = _jwt_backend().capabilities
        assert ImageCapability.TEXT_TO_IMAGE in caps
        assert ImageCapability.IMAGE_TO_IMAGE in caps


class TestAuthHeaders:
    def test_jwt_mode_signs_bearer_token(self):
        headers = _jwt_backend()._headers()
        assert headers["Content-Type"] == "application/json"
        token = headers["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, _SECRET, algorithms=["HS256"], options={"verify_exp": False})
        assert claims["iss"] == "ak-1"

    def test_bearer_mode_uses_static_key(self):
        headers = _bearer_backend()._headers()
        assert headers["Authorization"] == "Bearer static-key"


class TestApiModelNameResolution:
    def test_alias_key_sends_api_model_name(self, tmp_path):
        # 别名键（registry 键 ≠ API 名）：请求体 model_name 发真实 API 名。
        b = _jwt_backend("kling-v3-omni-image", api_model_name="kling-v3-omni")
        payload = b._build_payload(_request(tmp_path))
        assert payload["model_name"] == "kling-v3-omni"

    def test_plain_key_sends_itself(self, tmp_path):
        # 普通键（无别名）：请求体 model_name 回退到键名自身。
        b = _jwt_backend("kling-image-o1")
        payload = b._build_payload(_request(tmp_path))
        assert payload["model_name"] == "kling-image-o1"

    def test_default_model_sends_itself(self, tmp_path):
        payload = _jwt_backend()._build_payload(_request(tmp_path))
        assert payload["model_name"] == "kling-image-o1"


class TestPayloadBuilding:
    def test_text2image_no_reference(self, tmp_path):
        payload = _jwt_backend()._build_payload(_request(tmp_path))
        assert payload["model_name"] == "kling-image-o1"
        assert payload["aspect_ratio"] == "9:16"
        assert payload["n"] == 1
        assert "image" not in payload

    def test_image2image_embeds_base64_array(self, tmp_path):
        refs = [_ref(tmp_path, "a.png"), _ref(tmp_path, "b.png")]
        payload = _jwt_backend()._build_payload(_request(tmp_path, reference_images=refs))
        assert isinstance(payload["image"], list)
        assert len(payload["image"]) == 2
        # 纯 base64，无 data URI 前缀
        assert all(isinstance(u, str) and u and not u.startswith("data:") for u in payload["image"])

    def test_reference_over_limit_truncated(self, tmp_path):
        refs = [_ref(tmp_path, f"r{i}.png") for i in range(12)]
        payload = _jwt_backend()._build_payload(_request(tmp_path, reference_images=refs))
        # o1 上限 10 张，超出截断
        assert len(payload["image"]) == 10

    def test_missing_reference_raises(self, tmp_path):
        bad = ReferenceImage(path=str(tmp_path / "nope.png"))
        with pytest.raises(ImageCapabilityError) as exc:
            _jwt_backend()._build_payload(_request(tmp_path, reference_images=[bad]))
        assert exc.value.code == "image_reference_images_unreadable"

    def test_empty_filename_path_uses_index_placeholder(self, tmp_path):
        # "." 解析出空文件名（非文件）：报错按序号 #N 标识，不漏空 token。
        bad = ReferenceImage(path=".")
        with pytest.raises(ImageCapabilityError) as exc:
            _jwt_backend()._build_payload(_request(tmp_path, reference_images=[bad]))
        assert exc.value.params["names"] == "#1"


class TestSafeLogView:
    def test_no_base64_or_prompt_leaks(self, tmp_path):
        refs = [_ref(tmp_path, "a.png")]
        b = _jwt_backend()
        payload = b._build_payload(_request(tmp_path, reference_images=refs))
        view = b._safe_log_view(payload)
        assert view["reference_count"] == 1
        assert view["prompt_len"] == len("a hero portrait")
        assert "image" not in view
        assert "prompt" not in view
        assert all(isinstance(v, (str, int, bool)) for v in view.values())


class TestGenerateHappyPath:
    async def test_submit_poll_download(self, tmp_path):
        post = AsyncMock(return_value=_resp(_submit("task-9")))
        get = AsyncMock(
            side_effect=[
                _resp(_query("processing")),
                _resp(_query("succeed", urls=["https://x/final.png"])),
            ]
        )
        client = _client(post=post, get=get)
        with (
            patch("lib.image_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.image_backends.kling._POLL_INTERVAL_SECONDS", 0),
            patch("lib.image_backends.kling.download_image_to_path", new=AsyncMock()) as dl,
        ):
            result = await _jwt_backend().generate(_request(tmp_path))

        assert result.provider == PROVIDER_KLING
        assert result.image_uri == "https://x/final.png"
        dl.assert_awaited_once()
        # images/generations 提交端点
        assert post.await_args.args[0].endswith("/images/generations")

    async def test_multiple_images_takes_first(self, tmp_path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_query("succeed", urls=["https://x/1.png", "https://x/2.png"])))
        client = _client(post=post, get=get)
        with (
            patch("lib.image_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.image_backends.kling._POLL_INTERVAL_SECONDS", 0),
            patch("lib.image_backends.kling.download_image_to_path", new=AsyncMock()),
        ):
            result = await _jwt_backend().generate(_request(tmp_path))
        assert result.image_uri == "https://x/1.png"

    async def test_jwt_injected_on_submit(self, tmp_path):
        captured: dict = {}

        async def _post(url, json, headers):
            captured["headers"] = headers
            return _resp(_submit())

        post = AsyncMock(side_effect=_post)
        get = AsyncMock(return_value=_resp(_query("succeed", urls=["https://x/v.png"])))
        client = _client(post=post, get=get)
        with (
            patch("lib.image_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.image_backends.kling._POLL_INTERVAL_SECONDS", 0),
            patch("lib.image_backends.kling.download_image_to_path", new=AsyncMock()),
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
        get = AsyncMock(return_value=_resp(_query("succeed", urls=["https://x/v.png"])))
        client = _client(post=post, get=get)
        with (
            patch("lib.image_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.image_backends.kling._POLL_INTERVAL_SECONDS", 0),
            patch("lib.image_backends.kling.download_image_to_path", new=AsyncMock()),
        ):
            await _bearer_backend().generate(_request(tmp_path))
        assert captured["headers"]["Authorization"] == "Bearer static-key"

    async def test_failed_status_raises(self, tmp_path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_query("failed", status_msg="content rejected")))
        client = _client(post=post, get=get)
        with (
            patch("lib.image_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.image_backends.kling._POLL_INTERVAL_SECONDS", 0),
            patch("lib.image_backends.kling.download_image_to_path", new=AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="content rejected"):
                await _jwt_backend().generate(_request(tmp_path))

    async def test_http_error_raises_and_no_retry_on_4xx(self, tmp_path):
        # 真实 httpx.Response：submit 阶段 4xx 经 raise_for_status 抛 HTTPStatusError；
        # 确定性 4xx 不重试（非幂等建任务 POST），post 仅调用一次，不重复建任务 + 重复计费。
        req = httpx.Request("POST", "https://api.klingai.com/v1/images/generations")
        resp = httpx.Response(400, request=req, text="Bad Request")
        post = AsyncMock(return_value=resp)
        client = _client(post=post)
        with (
            patch("lib.image_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.image_backends.kling._POLL_INTERVAL_SECONDS", 0),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await _jwt_backend().generate(_request(tmp_path))
        assert post.call_count == 1


class TestRegistration:
    def test_kling_image_backend_registered(self):
        # 触发 image_backends 包级自动注册
        from lib.image_backends import create_backend, get_registered_backends

        assert PROVIDER_KLING in get_registered_backends()
        backend = create_backend(PROVIDER_KLING, auth_mode="jwt", access_key="ak", secret_key=_SECRET)
        assert isinstance(backend, KlingImageBackend)
