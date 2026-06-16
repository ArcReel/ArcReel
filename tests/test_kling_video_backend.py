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
        # turbo 不建模参考图（多图主体留 v3-omni/o1）
        assert caps.reference_images is False
        assert caps.max_reference_images == 0


class TestPerModelCapabilities:
    def test_v3_t2v_i2v_no_audio_no_reference(self):
        b = _jwt_backend("kling-v3")
        assert b.capabilities == {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}
        vc = b.video_capabilities
        assert vc.first_frame is True and vc.last_frame is True
        assert vc.reference_images is False and vc.max_reference_images == 0

    def test_v3_omni_declares_reference_images(self):
        b = _jwt_backend("kling-v3-omni")
        assert b.capabilities == {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}
        vc = b.video_capabilities
        assert vc.reference_images is True
        assert vc.max_reference_images == 4  # 保守值，待控制台核对

    def test_v2_6_declares_generate_audio_no_reference(self):
        b = _jwt_backend("kling-v2-6")
        assert VideoCapability.GENERATE_AUDIO in b.capabilities
        assert VideoCapability.TEXT_TO_VIDEO in b.capabilities
        assert b.video_capabilities.reference_images is False

    def test_video_o1_i2v_only_with_reference_images(self):
        b = _jwt_backend("kling-video-o1")
        # 仅图生（无 t2v），多图主体 R2V
        assert b.capabilities == {VideoCapability.IMAGE_TO_VIDEO}
        vc = b.video_capabilities
        assert vc.last_frame is True
        assert vc.reference_images is True and vc.max_reference_images == 4

    def test_unknown_model_falls_back_to_default_caps(self):
        # bearer 透传原生 model_name：未登记 → 保守默认（t2v+i2v、首尾帧、无音频/参考）
        b = _bearer_backend("kling-some-passthrough")
        assert b.capabilities == {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}
        vc = b.video_capabilities
        assert vc.last_frame is True
        assert vc.reference_images is False and vc.max_reference_images == 0

    def test_prefixed_and_cased_model_normalizes_to_registered_caps(self):
        # 中转 model_id 带厂商前缀（仓库既有约定 / 与 :）+ 非规范大小写/空白：归一化后实例 caps 仍精确命中
        # 已登记档，生成时防御与 resolver 裁剪同源——否则编排层放 4 张参考图、backend 却按默认 0 拒收。
        for model in ("vendor/Kling-V3-Omni", "provider:kling-v3-omni", "  provider:kling-v3-omni  "):
            vc = _bearer_backend(model).video_capabilities
            assert vc.reference_images is True and vc.max_reference_images == 4

    def test_future_version_does_not_inherit_caps_by_substring(self):
        # kling-v4 含子串 "kling-v3"？不含——但即便形如 kling-v3-omni-pro 也不得被子串误判继承能力；
        # 未登记一律保守默认，不猜未知 model 的参考图上限。
        for model in ("kling-v4", "kling-v3-omni-pro"):
            vc = _bearer_backend(model).video_capabilities
            assert vc.reference_images is False and vc.max_reference_images == 0


class TestModeAndResolution:
    def test_resolution_4k_maps_to_mode_4k(self, tmp_path):
        _, payload = _jwt_backend("kling-v3")._build_payload(_request(tmp_path, resolution="4k"))
        assert payload["mode"] == "4k"

    def test_resolution_4k_case_insensitive(self, tmp_path):
        _, payload = _jwt_backend("kling-v3-omni")._build_payload(_request(tmp_path, resolution="4K"))
        assert payload["mode"] == "4k"

    def test_4k_overrides_service_tier(self, tmp_path):
        # 4k 档优先于 std/pro（与 per_second_tiered 档位派生一致）
        _, payload = _jwt_backend("kling-v3")._build_payload(_request(tmp_path, resolution="4k", service_tier="pro"))
        assert payload["mode"] == "4k"

    def test_non_4k_resolution_keeps_service_tier_mode(self, tmp_path):
        _, payload = _jwt_backend("kling-v3")._build_payload(_request(tmp_path, resolution="1080p", service_tier="pro"))
        assert payload["mode"] == "pro"


class TestAudioGating:
    def test_v2_6_pro_audio_enabled(self, tmp_path):
        _, payload = _jwt_backend("kling-v2-6")._build_payload(
            _request(tmp_path, service_tier="pro", generate_audio=True)
        )
        assert payload["enable_audio"] is True

    def test_v2_6_std_audio_forced_off(self, tmp_path):
        # 人声仅 pro 档：std 即使请求有声也压制为无声
        _, payload = _jwt_backend("kling-v2-6")._build_payload(
            _request(tmp_path, service_tier="std", generate_audio=True)
        )
        assert payload["enable_audio"] is False

    def test_v3_audio_capability_absent_forced_off(self, tmp_path):
        # 无 generate_audio 能力的 model：即使请求有声，enable_audio 强制 False（压制 v3 默认有声）
        _, payload = _jwt_backend("kling-v3")._build_payload(
            _request(tmp_path, service_tier="pro", generate_audio=True)
        )
        assert payload["enable_audio"] is False

    def test_turbo_omits_enable_audio_field(self, tmp_path):
        # 旧档无 enable_audio 字段：不携带，避免向不支持的端点发未知参数
        _, payload = _jwt_backend()._build_payload(_request(tmp_path, generate_audio=True))
        assert "enable_audio" not in payload


class TestMultiImageSubpath:
    @staticmethod
    def _refs(tmp_path: Path, n: int) -> list[Path]:
        paths: list[Path] = []
        for i in range(n):
            p = tmp_path / f"ref{i}.png"
            p.write_bytes(b"\x89PNG\r\n" + bytes([i]))
            paths.append(p)
        return paths

    def test_reference_images_select_multi_image2video(self, tmp_path):
        refs = self._refs(tmp_path, 2)
        subpath, payload = _jwt_backend("kling-v3-omni")._build_payload(_request(tmp_path, reference_images=refs))
        assert subpath == "multi-image2video"
        # image_list 为 [{"image": <base64>}] 形态，无单首帧
        assert isinstance(payload["image_list"], list) and len(payload["image_list"]) == 2
        assert all(set(e) == {"image"} and isinstance(e["image"], str) and e["image"] for e in payload["image_list"])
        assert not payload["image_list"][0]["image"].startswith("data:")
        assert "image" not in payload and "image_tail" not in payload

    def test_multi_image2video_omits_enable_audio(self, tmp_path):
        refs = self._refs(tmp_path, 1)
        _, payload = _jwt_backend("kling-v3-omni")._build_payload(
            _request(tmp_path, reference_images=refs, service_tier="pro", generate_audio=True)
        )
        # multi-image2video 原生 schema 不含 enable_audio
        assert "enable_audio" not in payload

    def test_reference_images_take_precedence_over_start_image(self, tmp_path):
        refs = self._refs(tmp_path, 1)
        start = tmp_path / "start.png"
        start.write_bytes(b"\x89PNG\r\n")
        subpath, payload = _jwt_backend("kling-video-o1")._build_payload(
            _request(tmp_path, reference_images=refs, start_image=start)
        )
        assert subpath == "multi-image2video"
        assert "image_list" in payload

    def test_empty_reference_images_falls_through(self, tmp_path):
        subpath, _ = _jwt_backend("kling-v3-omni")._build_payload(_request(tmp_path, reference_images=[]))
        assert subpath == "text2video"

    def test_unreadable_reference_image_raises(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-v3-omni")._build_payload(
                _request(tmp_path, reference_images=[tmp_path / "missing.png"])
            )
        assert exc.value.code == "video_start_image_unreadable"


class TestCapabilityValidation:
    """生成时防御：能力不匹配的请求 fail-loud，不发出必然报错且照常计费的调用。"""

    @staticmethod
    def _refs(tmp_path: Path, n: int) -> list[Path]:
        paths: list[Path] = []
        for i in range(n):
            p = tmp_path / f"ref{i}.png"
            p.write_bytes(b"\x89PNG\r\n" + bytes([i]))
            paths.append(p)
        return paths

    def test_reference_images_on_unsupported_model_raises(self, tmp_path):
        # kling-v3 未声明多图主体能力：带参考图即拒绝，不误升级到 multi-image2video 子路径
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-v3")._build_payload(_request(tmp_path, reference_images=self._refs(tmp_path, 1)))
        assert exc.value.code == "video_reference_images_unsupported"

    def test_reference_images_over_limit_raises(self, tmp_path):
        # v3-omni 上限 4：传 5 张即拒绝
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-v3-omni")._build_payload(_request(tmp_path, reference_images=self._refs(tmp_path, 5)))
        assert exc.value.code == "video_reference_images_exceeded"
        assert exc.value.params["limit"] == 4
        assert exc.value.params["count"] == 5

    def test_text2video_on_model_without_t2v_raises(self, tmp_path):
        # kling-video-o1 不支持文生视频：无首帧/无参考即拒绝，不回落 text2video 子路径
        with pytest.raises(VideoCapabilityError) as exc:
            _jwt_backend("kling-video-o1")._build_payload(_request(tmp_path))
        assert exc.value.code == "video_capability_missing_t2v"

    def test_reference_at_limit_allowed(self, tmp_path):
        # 恰好达上限（4 张）放行
        subpath, payload = _jwt_backend("kling-v3-omni")._build_payload(
            _request(tmp_path, reference_images=self._refs(tmp_path, 4))
        )
        assert subpath == "multi-image2video"
        assert len(payload["image_list"]) == 4


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

    def test_image2video_empty_end_frame_is_omitted(self, tmp_path):
        # 空串 end_image 等价于"无尾帧"，按 truthy 判定跳过，不应误当路径去编码而崩溃。
        first = tmp_path / "first.png"
        first.write_bytes(b"\x89PNG\r\n1")
        subpath, payload = _jwt_backend()._build_payload(_request(tmp_path, start_image=first, end_image=""))
        assert subpath == "image2video"
        assert "image" in payload and "image_tail" not in payload

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
        # 持久化的是「子路径:task_id:有声标志」，resume 据此复原查询端点（text2video 因无首帧）+ 有声决策（turbo 恒 0）
        assert persist.await_args.args[1] == "text2video:task-x:0"

    async def test_persists_image2video_subpath_in_job_id(self, tmp_path):
        img = tmp_path / "first.png"
        img.write_bytes(b"\x89PNG\r\n")
        post = AsyncMock(return_value=_resp(_submit("task-i")))
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/v.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
            patch("lib.video_backends.kling.persist_provider_job_id", new=AsyncMock()) as persist,
        ):
            await _jwt_backend().generate(_request(tmp_path, task_id="local-task-2", start_image=img))
        # 有首帧 → image2video 前缀编入 job_id，resume 才能查对端点
        assert persist.await_args is not None
        assert persist.await_args.args[1] == "image2video:task-i:0"


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
            # 持久化 job_id 带 text2video 前缀 → 复原查询端点 + 还原裸 task_id
            result = await _jwt_backend().resume_video("text2video:task-resume", _request(tmp_path))

        post.assert_not_called()
        assert result.task_id == "task-resume"
        assert result.video_uri == "https://x/r.mp4"
        assert get.await_args.args[0].endswith("/videos/text2video/task-resume")
        dl.assert_awaited_once()

    async def test_resume_image2video_subpath_from_encoded_job_id(self, tmp_path):
        # resume 请求不带 start_image（真实重启路径如此）：子路径必须来自持久化 job_id 前缀，
        # 否则 image2video 任务会误查 text2video 端点取不到。
        post = AsyncMock()
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            result = await _jwt_backend().resume_video("image2video:task-r2", _request(tmp_path))
        post.assert_not_called()
        assert result.task_id == "task-r2"
        assert get.await_args.args[0].endswith("/videos/image2video/task-r2")

    async def test_resume_bare_job_id_falls_back_to_text2video(self, tmp_path):
        # 无已知前缀（异常/旧数据）回落 text2video，整串作 task_id。
        post = AsyncMock()
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            result = await _jwt_backend().resume_video("legacy-bare-id", _request(tmp_path))
        assert result.task_id == "legacy-bare-id"
        assert get.await_args.args[0].endswith("/videos/text2video/legacy-bare-id")

    async def test_resume_multi_image2video_subpath_from_encoded_job_id(self, tmp_path):
        # 多图主体任务 resume：子路径从持久化 job_id 前缀复原，查 multi-image2video 端点。
        post = AsyncMock()
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            result = await _jwt_backend("kling-v3-omni").resume_video("multi-image2video:task-m", _request(tmp_path))
        post.assert_not_called()
        assert result.task_id == "task-m"
        assert get.await_args.args[0].endswith("/videos/multi-image2video/task-m")


class TestAudioGatingResult:
    async def test_v2_6_pro_audio_result_true(self, tmp_path):
        # v2-6 pro + 请求有声 → result.generate_audio=True（下游计费取有声价 ¥1/s）
        post = AsyncMock(return_value=_resp(_submit("task-a")))
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/v.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            result = await _jwt_backend("kling-v2-6").generate(
                _request(tmp_path, service_tier="pro", generate_audio=True)
            )
        assert result.generate_audio is True
        assert post.await_args.args[0].endswith("/videos/text2video")

    async def test_v3_audio_gated_result_false(self, tmp_path):
        # v3 无音频能力：即使请求有声，result.generate_audio=False（计费取无声价）
        post = AsyncMock(return_value=_resp(_submit("task-b")))
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/v.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            result = await _jwt_backend("kling-v3").generate(
                _request(tmp_path, service_tier="pro", generate_audio=True)
            )
        assert result.generate_audio is False

    async def test_v2_6_pro_persists_audio_bit_in_job_id(self, tmp_path):
        # submit 时算定的有声决策编入 job_id（v2-6 pro 有声 → 末段 :1），resume 据此直连计费
        post = AsyncMock(return_value=_resp(_submit("task-c")))
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/v.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
            patch("lib.video_backends.kling.persist_provider_job_id", new=AsyncMock()) as persist,
        ):
            await _jwt_backend("kling-v2-6").generate(
                _request(tmp_path, task_id="local-a", service_tier="pro", generate_audio=True)
            )
        assert persist.await_args is not None
        assert persist.await_args.args[1] == "text2video:task-c:1"

    async def test_resume_reuses_persisted_audio_over_recompute(self, tmp_path):
        # 持久化有声标志（:1）优先于 resume 时按请求重算：即使请求 generate_audio=False，
        # 结果仍取 submit 时算定的有声（避免计费漂移）
        post = AsyncMock()
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            result = await _jwt_backend("kling-v2-6").resume_video(
                "text2video:task-c:1", _request(tmp_path, service_tier="pro", generate_audio=False)
            )
        post.assert_not_called()
        assert result.generate_audio is True
        # 锁定解析契约：3 段 job_id 的有声末段不得漏进 task_id，子路径/任务号须正确复原
        assert result.task_id == "task-c"
        assert get.await_args.args[0].endswith("/videos/text2video/task-c")

    async def test_resume_legacy_job_id_recomputes_audio(self, tmp_path):
        # 旧 job_id（2 段，未持久化有声标志）回落按请求重算
        post = AsyncMock()
        get = AsyncMock(return_value=_resp(_query("succeed", url="https://x/r.mp4")))
        client = _client(post=post, get=get)
        with (
            patch("lib.video_backends.kling.httpx.AsyncClient", return_value=client),
            patch("lib.video_backends.kling._KLING_VIDEO_POLL_INTERVAL_SECONDS", 0),
            patch("lib.video_backends.kling.download_video", new=AsyncMock()),
        ):
            result = await _jwt_backend("kling-v2-6").resume_video(
                "text2video:task-c", _request(tmp_path, service_tier="pro", generate_audio=True)
            )
        post.assert_not_called()
        assert result.generate_audio is True
        # 2 段旧 job_id 同样须正确复原子路径/任务号（不误把整串当 task_id）
        assert result.task_id == "task-c"
        assert get.await_args.args[0].endswith("/videos/text2video/task-c")
