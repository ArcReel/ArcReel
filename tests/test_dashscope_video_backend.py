"""DashScopeVideoBackend 单元测试（mock httpx，异步两步式）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.providers import PROVIDER_DASHSCOPE
from lib.video_backends.base import (
    ReferenceAudioMode,
    ResumeExpiredError,
    VideoCapabilityError,
    VideoGenerationRequest,
)


def _resp(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _http_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://x/api/v1/tasks/t")
    response = httpx.Response(status_code, request=request, text=message)
    return httpx.HTTPStatusError(f"error {status_code}", request=request, response=response)


def _http_error_503_in_message(status_code: int) -> httpx.HTTPStatusError:
    """生成 str() 含 "503" 子串、但状态码为 status_code 的真实 HTTPStatusError。

    raise_for_status 的消息包含请求 URL（这里 task_id 带 "503"），旧字符串兜底会据此误判重试；
    状态码谓词只读 response.status_code，不受 URL/消息中瞬态子串影响。
    """
    request = httpx.Request("POST", "https://x/api/v1/tasks/job-503-xyz")
    response = httpx.Response(status_code, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("expected HTTPStatusError")  # pragma: no cover


def _submit(task_id: str = "t-1") -> dict:
    return {"output": {"task_id": task_id, "task_status": "PENDING"}}


def _succeeded(url: str = "https://x/o.mp4", duration: int = 5) -> dict:
    return {
        "output": {"task_status": "SUCCEEDED", "video_url": url},
        "usage": {"duration": duration, "input_video_duration": 0, "output_video_duration": duration},
    }


def _client(*, post=None, get=None) -> AsyncMock:
    c = AsyncMock()
    if post is not None:
        c.post = post
    if get is not None:
        c.get = get
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=None)
    return c


def _patches(client: AsyncMock, download: AsyncMock):
    return (
        patch("httpx.AsyncClient", return_value=client),
        patch("lib.video_backends.dashscope.download_video", download),
        patch("lib.video_backends.dashscope.DASHSCOPE_POLL_INTERVAL_SECONDS", 0.0),
    )


def _ref(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\nfake")
    return p


class TestCapabilities:
    @pytest.mark.unit
    def test_name_and_model(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
        assert b.name == PROVIDER_DASHSCOPE
        assert b.model == "happyhorse-1.0-i2v"

    @pytest.mark.unit
    def test_happyhorse_r2v_caps(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
        vc = b.video_capabilities
        assert vc.max_reference_images > 0
        assert vc.max_reference_images == 9
        assert vc.first_frame is False

    @pytest.mark.unit
    def test_happyhorse_11_caps(self):
        """1.1 三模态各自登记能力档：t2v 无首帧（未登记会回落默认档的 first_frame=True，
        被误判进 i2v 桶），i2v 有首帧无参考图，r2v 参考图 9 张且无首帧。"""
        from lib.video_backends.dashscope import DashScopeVideoBackend

        t2v = DashScopeVideoBackend.video_capabilities_for_model("happyhorse-1.1-t2v")
        assert t2v.first_frame is False
        assert t2v.max_reference_images == 0

        i2v = DashScopeVideoBackend.video_capabilities_for_model("happyhorse-1.1-i2v")
        assert i2v.first_frame is True
        assert i2v.max_reference_images == 0

        r2v = DashScopeVideoBackend.video_capabilities_for_model("happyhorse-1.1-r2v")
        assert r2v.first_frame is False
        assert r2v.max_reference_images == 9

    @pytest.mark.unit
    def test_default_model_is_happyhorse_11_i2v(self):
        from lib.video_backends.dashscope import DEFAULT_MODEL, DashScopeVideoBackend

        assert DEFAULT_MODEL == "happyhorse-1.1-i2v"
        assert DashScopeVideoBackend(api_key="sk").model == "happyhorse-1.1-i2v"

    @pytest.mark.unit
    def test_wan_r2v_caps(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
        vc = b.video_capabilities
        assert vc.max_reference_images == 5
        assert vc.first_frame is True

    @pytest.mark.unit
    def test_t2v_caps(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-t2v")
        assert b.video_capabilities.first_frame is False
        assert b.video_capabilities.max_reference_images == 0

    @pytest.mark.unit
    def test_i2v_caps(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="wan2.7-i2v")
        assert b.video_capabilities.first_frame is True

    @pytest.mark.unit
    def test_decorated_model_name_resolves_r2v_caps(self):
        """代理中转的前缀/后缀装饰名（infer_endpoint 会按子串路由到 dashscope-async-video）
        必须解析出真实 r2v caps，而非退回 _DEFAULT_PROFILE 丢掉 reference_images。"""
        from lib.video_backends.dashscope import DashScopeVideoBackend

        for model, expected_max in (
            ("proxy/happyhorse-1.0-r2v", 9),
            ("provider:wan2.7-r2v", 5),
            ("wan2.7-r2v-0715", 5),  # 后缀版本号
            ("Pro/HappyHorse-1.0-R2V", 9),  # 大小写不敏感
        ):
            # 实例侧（_build_media 据此构造 media）
            b = DashScopeVideoBackend(api_key="sk", model=model)
            assert b.video_capabilities.max_reference_images > 0
            assert b.video_capabilities.max_reference_images == expected_max
            # resolver 侧（纯函数，不构造 backend）
            assert DashScopeVideoBackend.video_capabilities_for_model(model).max_reference_images == expected_max

    @pytest.mark.unit
    def test_alnum_glued_prefix_does_not_resolve_r2v_caps(self):
        """字母数字直接粘连的前缀（无分隔符）不算装饰名，须落回 _DEFAULT_PROFILE。

        与上一条用例的边界相反："myhappyhorse-1.0-r2v" 去掉 "my" 后与
        "happyhorse-1.0-r2v" 逐字符相同，若子串匹配不做标识符边界校验就会误判命中——
        代理装饰名靠 "/" ":" 等非字母数字分隔符与真实型号名区分，"my" 直接粘连不满足。
        """
        from lib.video_backends.dashscope import DashScopeVideoBackend

        assert DashScopeVideoBackend.video_capabilities_for_model("myhappyhorse-1.0-r2v").max_reference_images == 0

    @pytest.mark.unit
    @pytest.mark.parametrize("model", ["wan2.7-i2vfoo", "happyhorse-1.0-r2vfoo"])
    def test_alnum_glued_suffix_does_not_resolve_known_profile(self, model):
        """字母数字直接粘连的后缀同样不算装饰名，须落回 _DEFAULT_PROFILE。

        与前两条用例互补的第三个边界方向："wan2.7-i2vfoo" 截断掉 "foo" 后与 key "wan2.7-i2v"
        逐字符相同，若子串匹配只做左侧边界校验、不做右侧校验，未知变体后缀会被截断误判成已知
        modality；数字后缀（"-0715"）靠 "-" 分隔满足右侧边界，不受本用例约束。
        """
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model(model)
        assert caps.max_reference_images == 0
        assert caps.max_prompt_chars is None

    @pytest.mark.unit
    def test_unknown_bare_series_falls_back_to_default(self):
        """仅系列名无变体后缀（裸 "happyhorse"）无法判别 t2v/i2v/r2v → 通用默认（无 r2v）。"""
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="happyhorse")
        assert b.video_capabilities.max_reference_images == 0

    @pytest.mark.unit
    def test_wan27_declares_prompt_char_limit(self):
        """wan2.7 全家族 prompt ≤ 5000 字符；超限官方静默截断并照常计费，故须付费前拦。"""
        from lib.video_backends.dashscope import DashScopeVideoBackend

        for model in ("wan2.7-t2v", "wan2.7-i2v", "wan2.7-r2v"):
            assert DashScopeVideoBackend.video_capabilities_for_model(model).max_prompt_chars == 5000

    @pytest.mark.unit
    def test_models_without_verified_limit_declare_none(self):
        """未取证到上限的 model 不声明——未声明 ≠ 上限 0，凭空声明会误拒合法请求。"""
        from lib.video_backends.dashscope import DashScopeVideoBackend

        for model in ("happyhorse-1.0-t2v", "happyhorse-1.0-i2v", "happyhorse-1.0-r2v", "happyhorse"):
            assert DashScopeVideoBackend.video_capabilities_for_model(model).max_prompt_chars is None


class TestReferenceToVideo:
    @pytest.mark.unit
    async def test_r2v_happy_path(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("t-r2v")))
        get = AsyncMock(return_value=_resp(_succeeded(duration=8)))
        client = _client(post=post, get=get)
        download = AsyncMock()
        ref1, ref2 = _ref(tmp_path, "a.png"), _ref(tmp_path, "b.png")
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            result = await b.generate(
                VideoGenerationRequest(
                    prompt="[Image 1] dances",
                    output_path=tmp_path / "o.mp4",
                    reference_images=[ref1, ref2],
                    resolution="720p",
                    aspect_ratio="16:9",
                    duration_seconds=5,
                )
            )

        body = post.call_args.kwargs["json"]
        assert body["model"] == "happyhorse-1.0-r2v"
        media = body["input"]["media"]
        assert len(media) == 2
        assert all(m["type"] == "reference_image" for m in media)
        assert media[0]["url"].startswith("data:image/png;base64,")
        # resolution 大写、watermark 关、ratio 透传
        assert body["parameters"]["resolution"] == "720P"
        assert body["parameters"]["watermark"] is False
        assert body["parameters"]["ratio"] == "16:9"
        # submit 端点 + async 头
        assert post.call_args.args[0].endswith("/api/v1/services/aigc/video-generation/video-synthesis")
        assert post.call_args.kwargs["headers"]["X-DashScope-Async"] == "enable"
        # 计费时长取 usage.duration（非请求值 5）
        assert result.duration_seconds == 8
        assert result.provider == PROVIDER_DASHSCOPE
        assert result.task_id == "t-r2v"
        download.assert_called_once()

    @pytest.mark.unit
    async def test_r2v_ref_limit_happyhorse_9(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        refs = [_ref(tmp_path, f"r{i}.png") for i in range(12)]
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            await b.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", reference_images=refs, resolution="720p"
                )
            )
        assert len(post.call_args.kwargs["json"]["input"]["media"]) == 9

    @pytest.mark.unit
    async def test_r2v_ref_limit_wan_5(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        refs = [_ref(tmp_path, f"r{i}.png") for i in range(8)]
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
            await b.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", reference_images=refs, resolution="1080p"
                )
            )
        assert len(post.call_args.kwargs["json"]["input"]["media"]) == 5

    @pytest.mark.unit
    async def test_r2v_all_refs_missing_fail_loud(self, tmp_path: Path):
        # r2v 参考图缺失/不可读（含空串过滤后仍有声明项）须 fail-loud 报错列名，不静默退化
        post = AsyncMock(return_value=_resp(_submit()))
        client = _client(post=post, get=AsyncMock())
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        reference_images=[str(tmp_path / "nope.png"), ""],
                        resolution="720p",
                    )
                )
        assert ei.value.code == "video_reference_images_unreadable"
        assert "nope.png" in ei.value.params["names"]
        # 提交请求根本不应发出
        post.assert_not_called()

    @pytest.mark.unit
    async def test_r2v_no_refs_provided_raises(self, tmp_path: Path):
        # r2v 模型但调用方完全未提供参考图（None/空）→ required 错误，不提交无 media 的 r2v 请求
        post = AsyncMock(return_value=_resp(_submit()))
        client = _client(post=post, get=AsyncMock())
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
        assert ei.value.code == "video_reference_images_required"
        post.assert_not_called()

    @pytest.mark.unit
    async def test_r2v_partial_unreadable_refs_fail_loud(self, tmp_path: Path):
        # 部分参考图 read 抛 OSError（is_file 通过但读失败）→ fail-loud 中止并列出不可读文件名，
        # 不静默用可读子集生成（会产出错误结果且照常计费）
        post = AsyncMock(return_value=_resp(_submit("t-r2v")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        ra, rb = _ref(tmp_path, "a.png"), _ref(tmp_path, "b.png")

        def fake_uri(p: Path) -> str:
            if p.name == "a.png":
                raise OSError("io error")
            return "data:image/png;base64,OK"

        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.video_backends.dashscope.image_to_data_uri", side_effect=fake_uri):
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        reference_images=[str(ra), str(rb)],
                        resolution="720p",
                    )
                )
        assert ei.value.code == "video_reference_images_unreadable"
        assert "a.png" in ei.value.params["names"]
        post.assert_not_called()

    @pytest.mark.unit
    async def test_r2v_all_refs_unreadable_oserror_fail_loud(self, tmp_path: Path):
        # 全部参考图 read 抛 OSError → fail-loud，不提交无 media 的 r2v 请求
        post = AsyncMock(return_value=_resp(_submit()))
        client = _client(post=post, get=AsyncMock())
        ra = _ref(tmp_path, "a.png")
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.video_backends.dashscope.image_to_data_uri", side_effect=OSError("denied")):
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", reference_images=[str(ra)], resolution="720p"
                    )
                )
        assert ei.value.code == "video_reference_images_unreadable"
        post.assert_not_called()


class TestFirstFrameAndTextOnly:
    @pytest.mark.unit
    async def test_i2v_start_image_oserror_fail_loud(self, tmp_path: Path):
        # 声明了首帧图却 read 抛 OSError（权限/IO）→ fail-loud 中止，不静默忽略首帧照常出片
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        start = _ref(tmp_path, "start.png")
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.video_backends.dashscope.image_to_data_uri", side_effect=OSError("io")):
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-i2v")
            with pytest.raises(VideoCapabilityError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", start_image=start, resolution="720p"
                    )
                )
        assert ei.value.code == "video_start_image_unreadable"
        assert "start.png" in ei.value.params["name"]
        post.assert_not_called()

    @pytest.mark.unit
    async def test_i2v_first_frame(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        start = _ref(tmp_path, "start.png")
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-i2v")
            await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", start_image=start, resolution="720p")
            )
        media = post.call_args.kwargs["json"]["input"]["media"]
        assert media == [{"type": "first_frame", "url": media[0]["url"]}]
        assert media[0]["url"].startswith("data:image/png;base64,")
        # 带首帧（图生视频）按首帧定宽高比：默认 aspect_ratio 非空也不得下传 ratio，否则上游拒绝
        assert "ratio" not in post.call_args.kwargs["json"]["parameters"]

    @pytest.mark.unit
    async def test_t2v_no_media(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-t2v")
            await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="1080p"))
        assert "media" not in post.call_args.kwargs["json"]["input"]
        assert post.call_args.kwargs["json"]["parameters"]["resolution"] == "1080P"

    @pytest.mark.unit
    async def test_happyhorse_11_i2v_payload(self, tmp_path: Path):
        """1.1 与 1.0 同口径：水印显式关（官方默认开），480P 档位透传为大写。"""
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        start = _ref(tmp_path, "start.png")
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.1-i2v")
            await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", start_image=start, resolution="480p")
            )
        params = post.call_args.kwargs["json"]["parameters"]
        assert post.call_args.kwargs["json"]["model"] == "happyhorse-1.1-i2v"
        assert params["watermark"] is False
        assert params["resolution"] == "480P"
        assert post.call_args.kwargs["json"]["input"]["media"][0]["type"] == "first_frame"


class TestPollingAndFailures:
    @pytest.mark.unit
    async def test_polls_through_running(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("t3")))
        get = AsyncMock(
            side_effect=[
                _resp({"output": {"task_status": "RUNNING"}}),
                _resp({"output": {"task_status": "RUNNING"}}),
                _resp(_succeeded()),
            ]
        )
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            result = await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p")
            )
        assert get.call_count == 3
        assert result.task_id == "t3"

    @pytest.mark.unit
    async def test_failed_raises(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp({"output": {"task_status": "FAILED", "code": "X", "message": "boom"}}))
        client = _client(post=post, get=get)
        download = AsyncMock()
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-t2v")
            with pytest.raises(RuntimeError, match="boom"):
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
        download.assert_not_called()

    @pytest.mark.unit
    async def test_generate_unknown_raises_runtime(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("t-new")))
        get = AsyncMock(return_value=_resp({"output": {"task_status": "UNKNOWN"}}))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-t2v")
            with pytest.raises(RuntimeError) as ei:
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
            assert "expired" in str(ei.value).lower()
            assert not isinstance(ei.value, ResumeExpiredError)


class TestResume:
    @pytest.mark.unit
    async def test_resume_polls_without_post(self, tmp_path: Path):
        post = AsyncMock(side_effect=AssertionError("resume 不应 POST"))
        get = AsyncMock(return_value=_resp(_succeeded(url="https://x/r.mp4")))
        client = _client(post=post, get=get)
        download = AsyncMock()
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            result = await b.resume_video(
                "t-resume",
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"),
            )
        post.assert_not_called()
        assert get.call_args.args[0].endswith("/tasks/t-resume")
        assert result.task_id == "t-resume"

    @pytest.mark.unit
    async def test_resume_unknown_raises_resume_expired(self, tmp_path: Path):
        get = AsyncMock(return_value=_resp({"output": {"task_status": "UNKNOWN"}}))
        client = _client(get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(ResumeExpiredError) as ei:
                await b.resume_video(
                    "t-exp",
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"),
                )
            assert ei.value.job_id == "t-exp"
            assert ei.value.provider == PROVIDER_DASHSCOPE

    @pytest.mark.unit
    async def test_resume_404_raises_without_retry(self, tmp_path: Path):
        not_found = _resp({"error": "nope"}, status_code=404)
        not_found.raise_for_status = MagicMock(side_effect=_http_error(404, "not found"))
        get = AsyncMock(return_value=not_found)
        client = _client(get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(ResumeExpiredError):
                await b.resume_video(
                    "t-404",
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"),
                )
            assert get.call_count == 1


class TestPersist:
    @pytest.mark.unit
    async def test_persist_called_with_task_id(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("job-9")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        persist = AsyncMock()
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.video_backends.base.persist_provider_job_id", persist):
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            await b.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", resolution="720p", task_id="db-task-1"
                )
            )
        persist.assert_called_once()
        assert persist.call_args.args[0] == "db-task-1"
        assert persist.call_args.args[1] == "job-9"
        assert persist.call_args.kwargs["provider"] == PROVIDER_DASHSCOPE


class TestSubmit413:
    @pytest.mark.unit
    async def test_submit_413_surfaces_httpstatuserror_no_retry(self, tmp_path: Path):
        err413 = _resp({"code": "PayloadTooLarge"}, status_code=413)
        err413.raise_for_status = MagicMock(side_effect=_http_error(413, "Request Entity Too Large"))
        post = AsyncMock(return_value=err413)
        client = _client(post=post)
        download = AsyncMock()
        ref1 = _ref(tmp_path, "a.png")
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(
                    VideoGenerationRequest(
                        prompt="[Image 1] x",
                        output_path=tmp_path / "o.mp4",
                        reference_images=[ref1],
                        resolution="720p",
                        aspect_ratio="16:9",
                        duration_seconds=5,
                    )
                )
        # 保留 status_code 让咽喉层识别 413；413 非 retryable → fail-fast 单次提交
        assert ei.value.response.status_code == 413
        assert post.call_count == 1
        download.assert_not_called()


class TestRetryStatusGating:
    """提交/轮询按 HTTP status_code 决定重试，消除字符串子串误判。"""

    @pytest.mark.unit
    async def test_submit_4xx_with_503_substring_no_retry(self, tmp_path: Path):
        # 4xx 错误消息里带 "503" 子串（URL/task_id）：旧字符串兜底会误判重试，新谓词按 400 fail-fast。
        err = _http_error_503_in_message(400)
        assert "503" in str(err)
        bad = _resp({"code": "InvalidParameter"}, status_code=400)
        bad.raise_for_status = MagicMock(side_effect=err)
        post = AsyncMock(return_value=bad)
        client = _client(post=post)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.retry.asyncio.sleep", new_callable=AsyncMock):
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-t2v")
            with pytest.raises(httpx.HTTPStatusError) as ei:
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
        assert ei.value.response.status_code == 400
        assert post.call_count == 1

    @pytest.mark.unit
    async def test_submit_real_503_retries_then_succeeds(self, tmp_path: Path):
        # 真 5xx：按 status_code 重试，第三次成功。
        err503 = _resp({"code": "ServiceUnavailable"}, status_code=503)
        err503.raise_for_status = MagicMock(side_effect=_http_error_503_in_message(503))
        post = AsyncMock(side_effect=[err503, err503, _resp(_submit("t-ok"))])
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.retry.asyncio.sleep", new_callable=AsyncMock):
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-t2v")
            result = await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p")
            )
        assert post.call_count == 3
        assert result.task_id == "t-ok"

    @pytest.mark.unit
    async def test_submit_connect_error_retries(self, tmp_path: Path):
        # 网络层错误（连接确定未送达）维持重试。
        post = AsyncMock(side_effect=[httpx.ConnectError("refused"), _resp(_submit("t-ok"))])
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.retry.asyncio.sleep", new_callable=AsyncMock):
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-t2v")
            result = await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p")
            )
        assert post.call_count == 2
        assert result.task_id == "t-ok"

    @pytest.mark.unit
    async def test_poll_timeout_retries(self, tmp_path: Path):
        # 轮询（幂等 GET）网络层 Timeout 维持重试。
        post = AsyncMock(return_value=_resp(_submit("t-poll")))
        get = AsyncMock(side_effect=[httpx.TimeoutException("read timed out"), _resp(_succeeded())])
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            result = await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p")
            )
        assert get.call_count == 2
        assert result.task_id == "t-poll"


class TestWan27ReferenceVoice:
    """wan2.7-r2v 的音色不是独立 media 条目，而是挂在参考素材项上的 reference_voice 字段。"""

    @staticmethod
    def _backend():
        from lib.video_backends.dashscope import DashScopeVideoBackend

        return DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")

    @staticmethod
    def _refs(tmp_path, count: int) -> list:
        out = []
        for i in range(count):
            p = tmp_path / f"r{i}.png"
            p.write_bytes(b"\x89PNG\r\n")
            out.append(p)
        return out

    @staticmethod
    def _audio(tmp_path, name: str) -> Path:
        p = tmp_path / name
        p.write_bytes(b"riff-audio")
        return p

    @pytest.mark.unit
    def test_r2v_declares_audio_capability(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model("wan2.7-r2v")
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        # 音频逐段挂参考素材项，段数上限即参考素材总数上限
        assert caps.max_reference_audio_count == 5

    @pytest.mark.unit
    def test_i2v_declares_no_audio_capability(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model("wan2.7-i2v")
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE

    @pytest.mark.unit
    def test_audio_attached_to_reference_items_in_order(self, tmp_path):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="两人对话",
                output_path=tmp_path / "o.mp4",
                reference_images=self._refs(tmp_path, 2),
                reference_audio_files=[self._audio(tmp_path, "a.mp3"), self._audio(tmp_path, "b.wav")],
            )
        )

        refs = [m for m in payload["input"]["media"] if m["type"] == "reference_image"]
        assert len(refs) == 2
        assert refs[0]["reference_voice"].startswith("data:audio/mpeg;base64,")
        assert refs[1]["reference_voice"].startswith("data:audio/wav;base64,")

    @pytest.mark.unit
    def test_reference_audio_targets_align_by_explicit_index_not_position(self, tmp_path):
        """参考音频顺序（台词 speaker 首现）与参考图顺序（mention 首现）独立派生，不天然同序。

        场景：references = [场景, 张三]（图0=场景，图1=张三），audio_speakers = [张三]（唯一
        开口的角色）。若按位置对齐会把张三的声音错挂到场景图上；targets=[1] 显式声明张三的
        声音配图 1，须对齐到正确的 reference_items[1]。
        """
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="场景先出现，张三说话",
                output_path=tmp_path / "o.mp4",
                reference_images=self._refs(tmp_path, 2),
                reference_audio_files=[self._audio(tmp_path, "zhangsan.mp3")],
                reference_audio_targets=[1],
            )
        )

        refs = [m for m in payload["input"]["media"] if m["type"] == "reference_image"]
        assert "reference_voice" not in refs[0]
        assert refs[1]["reference_voice"].startswith("data:audio/mpeg;base64,")

    @pytest.mark.unit
    def test_reference_audio_targets_out_of_range_raises_slots_insufficient(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_payload(
                VideoGenerationRequest(
                    prompt="x",
                    output_path=tmp_path / "o.mp4",
                    reference_images=self._refs(tmp_path, 1),
                    reference_audio_files=[self._audio(tmp_path, "a.mp3")],
                    reference_audio_targets=[5],
                )
            )

        assert exc.value.code == "video_reference_audio_slots_insufficient"

    @pytest.mark.unit
    def test_reference_audio_targets_duplicate_index_raises_instead_of_silently_overwriting(self, tmp_path):
        """两段音频指向同一个参考素材项时，逐条赋值会静默覆盖前一条绑定——必须硬失败。"""
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_payload(
                VideoGenerationRequest(
                    prompt="两人对话",
                    output_path=tmp_path / "o.mp4",
                    reference_images=self._refs(tmp_path, 2),
                    reference_audio_files=[self._audio(tmp_path, "a.mp3"), self._audio(tmp_path, "b.wav")],
                    reference_audio_targets=[0, 0],
                )
            )

        assert exc.value.code == "video_reference_audio_slots_insufficient"

    @pytest.mark.unit
    def test_fewer_audios_than_references_leaves_rest_unvoiced(self, tmp_path):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="一人说话",
                output_path=tmp_path / "o.mp4",
                reference_images=self._refs(tmp_path, 3),
                reference_audio_files=[self._audio(tmp_path, "a.mp3")],
            )
        )

        refs = [m for m in payload["input"]["media"] if m["type"] == "reference_image"]
        assert "reference_voice" in refs[0]
        assert all("reference_voice" not in r for r in refs[1:])

    @pytest.mark.unit
    def test_no_audio_leaves_reference_items_untouched(self, tmp_path):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="无对白",
                output_path=tmp_path / "o.mp4",
                reference_images=self._refs(tmp_path, 2),
            )
        )

        refs = [m for m in payload["input"]["media"] if m["type"] == "reference_image"]
        assert all("reference_voice" not in r for r in refs)

    @pytest.mark.unit
    def test_more_audios_than_references_raises(self, tmp_path):
        """挂不上的音频不丢弃：静默丢弃会让某个角色的音色声明无声失效，且照常扣费。

        卡点是"可挂载的参考素材不够"，与 gate 的"超出模型能力上限"是两回事，故各用一个
        code：两者的处置建议相反（补参考图 vs 减角色），共用会给出与实际卡点不符的提示。
        """
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_payload(
                VideoGenerationRequest(
                    prompt="三人对话",
                    output_path=tmp_path / "o.mp4",
                    reference_images=self._refs(tmp_path, 1),
                    reference_audio_files=[self._audio(tmp_path, "a.mp3"), self._audio(tmp_path, "b.mp3")],
                )
            )

        assert exc.value.code == "video_reference_audio_slots_insufficient"
        assert exc.value.params["slots"] == 1
        assert exc.value.params["count"] == 2

    @pytest.mark.unit
    def test_model_without_reference_images_rejects_audio_instead_of_dropping(self, tmp_path):
        """无参考图能力的 model 收到音频要报错，不静默丢弃。

        可达路径：自定义供应商把 endpoint 级的 reference_audio_mode 覆盖成 direct（该 endpoint
        的 delegate 确实会下传音频），但具体 model 走 wan2.7-i2v 这类无参考素材的档位——
        音频无处挂载。丢弃会生成一段音色随机的视频并照常扣费。
        """
        from lib.video_backends.dashscope import DashScopeVideoBackend

        with pytest.raises(VideoCapabilityError) as exc:
            DashScopeVideoBackend(api_key="sk", model="wan2.7-i2v")._build_payload(
                VideoGenerationRequest(
                    prompt="独白",
                    output_path=tmp_path / "o.mp4",
                    start_image=self._refs(tmp_path, 1)[0],
                    reference_audio_files=[self._audio(tmp_path, "a.mp3")],
                )
            )

        assert exc.value.code == "video_reference_audio_unsupported"

    @pytest.mark.unit
    def test_missing_audio_file_raises(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_payload(
                VideoGenerationRequest(
                    prompt="x",
                    output_path=tmp_path / "o.mp4",
                    reference_images=self._refs(tmp_path, 1),
                    reference_audio_files=[tmp_path / "missing.mp3"],
                )
            )

        assert exc.value.code == "video_reference_audio_unreadable"

    @pytest.mark.unit
    def test_unsupported_audio_format_raises(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as exc:
            self._backend()._build_payload(
                VideoGenerationRequest(
                    prompt="x",
                    output_path=tmp_path / "o.mp4",
                    reference_images=self._refs(tmp_path, 1),
                    reference_audio_files=[self._audio(tmp_path, "a.ogg")],
                )
            )

        assert exc.value.code == "video_reference_audio_format_unsupported"


class TestWan2Aliases:
    """wan2.7 的 model_id 能力档解析：连字符/下划线别名与点号形态须归同一档；WAN2_PATTERN 只认
    2.7，其余 2.x 小版本（2.1/2.2 等）不在本正则确权范围内（见 WAN2_PATTERN 处的说明）。
    """

    @pytest.mark.unit
    @pytest.mark.parametrize("model", ["wan-2.7-r2v", "wan_2.7-r2v", "wan_2.7_r2v"])
    def test_alias_forms_get_wan27_r2v_capabilities(self, model):
        """discovery 返回的连字符/下划线 wan2.7 别名（endpoints.py 已路由到本后端）须认作
        wan2.7-r2v，不落回默认档案。

        与 endpoints.infer_endpoint 共用 WAN2_PATTERN：两处不同宽即会出现"路由到本后端却被当
        通用型号丢失参考图/首帧参数"的矛盾。"wan_2.7_r2v" 这类版本号与模态后缀之间也用下划线
        分隔的别名，须额外把该分隔符归一化成连字符才能匹配 _MODEL_PROFILES 的 key。
        """
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model(model)
        assert caps.first_frame is True
        assert caps.max_reference_images == 5
        assert caps.max_reference_audio_count == 5

    @pytest.mark.unit
    @pytest.mark.parametrize("model", ["wan-2.7-image-to-video", "wan_2.7-image2video", "wan_2.7_image_to_video"])
    def test_image_to_video_alias_gets_wan27_i2v_capabilities(self, model):
        """连字符/下划线形态的 image-to-video 续接别名归一化后仍带该后缀（如
        "wan2.7-image-to-video"），不与 _MODEL_PROFILES 的 "wan2.7-i2v" 构成子串关系；须先把
        该后缀折成 "i2v" 再查表，否则静默落默认档、丢失 first_frame，_build_media 据此不下发
        start_image。
        """
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model(model)
        assert caps.first_frame is True
        # r2v 同样 first_frame=True，靠 max_reference_images 区分二者：i2v 无参考图能力，
        # 缺这条断言时归一化误落 r2v 档案也会通过。
        assert caps.max_reference_images == 0
        assert caps.max_prompt_chars == 5000

    @pytest.mark.unit
    def test_fully_underscored_t2v_alias_does_not_get_default_first_frame(self):
        """ "wan_2.7_t2v" 的版本号与模态后缀均用下划线分隔；t2v 无首帧能力，与 _DEFAULT_PROFILE
        的默认 first_frame=True 恰好相反，能验证确实解析到了 wan2.7-t2v 而非静默落回默认档。
        """
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model("wan_2.7_t2v")
        assert caps.first_frame is False
        assert caps.max_prompt_chars == 5000

    @pytest.mark.unit
    @pytest.mark.parametrize("model", ["swan2", "vendorwan2", "wan20", "swan2.7-r2v", "vendorwan2.7-t2v"])
    def test_substring_without_boundary_does_not_get_wan2_capabilities(self, model):
        """含 "wan2" 子串但两侧非字母数字边界不成立的型号名，不得被误判为万相 2.x 家族。

        "swan2.7-r2v" / "vendorwan2.7-t2v" 这类完整别名（左侧紧贴字母、右侧带合法模态后缀）单靠
        WAN2_PATTERN 的边界锚点无法拦下——命中的是 _profile_for_model 末尾的兜底子串匹配：
        "swan2.7-r2v" 去掉首字符后与 _MODEL_PROFILES 的 key "wan2.7-r2v" 逐字符相同，该循环须
        对每个 key 同样做左侧边界校验才不会误判命中。
        """
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model(model)
        assert caps.max_reference_images == 0
        assert caps.max_prompt_chars is None

    @pytest.mark.unit
    @pytest.mark.parametrize("model", ["wan-2.1-r2v", "wan_2.2-i2v"])
    def test_non_27_alias_forms_fall_back_to_default_profile(self, model):
        """WAN2_PATTERN 只认 2.7：连字符/下划线形态的其余 2.x 小版本不落 wan2.7 能力档——
        本后端固定请求的端点与这些小版本的实际协议不符（见 WAN2_PATTERN 处的说明），
        endpoints.py 也不会把它们路由到本后端；此处确认能力档解析同样不越界。
        """
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model(model)
        assert caps.max_reference_images == 0
        assert caps.max_prompt_chars is None


class TestWan3:
    """wan3.0-video：单模型通吃三条路径，首尾帧 + 独立参考音频条目 + 可控音轨。"""

    @staticmethod
    def _backend(**kwargs):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        return DashScopeVideoBackend(api_key="sk", model="wan3.0-video", **kwargs)

    @staticmethod
    def _file(tmp_path, name: str, data: bytes = b"\x89PNG\r\n") -> Path:
        p = tmp_path / name
        p.write_bytes(data)
        return p

    @pytest.mark.unit
    def test_declares_capabilities(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model("wan3.0-video")
        assert caps.first_frame is True
        assert caps.last_frame is True
        assert caps.max_reference_images == 10
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 5
        assert caps.max_reference_audio_total_seconds == 15.0
        assert caps.max_prompt_chars == 20000
        # 音频是独立 media 条目，不挂在参考素材项上（与 wan2.7-r2v 相反）
        assert caps.reference_audio_per_image is False

    @pytest.mark.unit
    @pytest.mark.parametrize("model", ["wan-3-turbo", "wan3-turbo", "wan_3-turbo", "wan_3_turbo"])
    def test_alias_forms_get_wan3_capabilities(self, model):
        """discovery 返回的连字符/下划线别名（endpoints.py 已路由到本后端）须认作 wan3.0，不落回默认档案。

        与 endpoints.infer_endpoint / duration_presets 共用 WAN3_PATTERN：三处不同宽即会出现
        "路由到本后端却被当通用型号丢失参考图/尾帧/音轨参数" 的矛盾。
        """
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model(model)
        assert caps.last_frame is True
        assert caps.max_reference_images == 10
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_prompt_chars == 20000

    @pytest.mark.unit
    @pytest.mark.parametrize("model", ["swan3", "vendorwan3", "wan30"])
    def test_wan3_substring_without_boundary_does_not_get_wan3_capabilities(self, model):
        """含 "wan3" 子串但两侧非字母数字边界不成立的型号名，不得被误判为万相 3.0 家族。"""
        from lib.video_backends.dashscope import DashScopeVideoBackend

        caps = DashScopeVideoBackend.video_capabilities_for_model(model)
        assert caps.max_reference_images == 0
        assert caps.max_prompt_chars is None

    @pytest.mark.unit
    def test_first_and_last_frame_in_media(self, tmp_path):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="p",
                output_path=tmp_path / "o.mp4",
                start_image=self._file(tmp_path, "s.png"),
                end_image=self._file(tmp_path, "e.png"),
            )
        )
        types = [m["type"] for m in payload["input"]["media"]]
        assert types == ["first_frame", "last_frame"]
        # 首帧在场即不下发 ratio（上游按首帧定比例）
        assert "ratio" not in payload["parameters"]

    @pytest.mark.unit
    def test_reference_audio_is_standalone_media_item(self, tmp_path):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="p",
                output_path=tmp_path / "o.mp4",
                reference_images=[self._file(tmp_path, "r0.png")],
                reference_audio_files=[
                    self._file(tmp_path, "a.mp3", b"riff"),
                    self._file(tmp_path, "b.wav", b"riff"),
                ],
            )
        )
        media = payload["input"]["media"]
        assert [m["type"] for m in media] == ["reference_image", "reference_audio", "reference_audio"]
        # 顺序即 prompt 中「音频N」的指认契约，不得重排
        assert media[1]["url"].startswith("data:audio/mpeg;base64,")
        assert media[2]["url"].startswith("data:audio/wav;base64,")
        # 音频不挂在参考图项上
        assert "reference_voice" not in media[0]

    @pytest.mark.unit
    def test_reference_images_optional(self, tmp_path):
        """通吃型号的图生/文生请求没有参考图，不能按 r2v 专用型号那样判 required。"""
        payload = self._backend()._build_payload(
            VideoGenerationRequest(
                prompt="p", output_path=tmp_path / "o.mp4", start_image=self._file(tmp_path, "s.png")
            )
        )
        assert [m["type"] for m in payload["input"]["media"]] == ["first_frame"]

    @pytest.mark.unit
    @pytest.mark.parametrize("generate_audio", [True, False])
    def test_audio_switch_is_sent(self, tmp_path, generate_audio):
        payload = self._backend()._build_payload(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=generate_audio)
        )
        assert payload["parameters"]["audio"] is generate_audio

    @pytest.mark.unit
    @pytest.mark.parametrize("model", ["wan2.7-i2v", "happyhorse-1.1-i2v"])
    def test_audio_switch_not_sent_for_always_on_models(self, tmp_path, model):
        """恒有声型号收到该参数会被上游当非法参数拒。"""
        from lib.video_backends.dashscope import DashScopeVideoBackend

        payload = DashScopeVideoBackend(api_key="sk", model=model)._build_payload(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=False)
        )
        assert "audio" not in payload["parameters"]

    @pytest.mark.unit
    @pytest.mark.parametrize("model", ["wan-3-turbo", "wan3-turbo", "wan_3-turbo", "wan_3_turbo"])
    def test_audio_switch_is_sent_for_alias_forms(self, tmp_path, model):
        """别名同样按可控音轨型号分派，不落回恒有声默认档案。"""
        from lib.video_backends.dashscope import DashScopeVideoBackend

        payload = DashScopeVideoBackend(api_key="sk", model=model)._build_payload(
            VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=False)
        )
        assert payload["parameters"]["audio"] is False

    @pytest.mark.unit
    def test_prompt_over_limit_rejected_before_submit(self, tmp_path):
        """超限对端静默截断且照常计费，故由 gate 在付费前拒。"""
        from lib.video_backends.dashscope import DashScopeVideoBackend
        from lib.video_frame_slots import gate_video_request

        caps = DashScopeVideoBackend.video_capabilities_for_model("wan3.0-video")
        with pytest.raises(VideoCapabilityError) as exc:
            gate_video_request(
                caps=caps,
                provider=PROVIDER_DASHSCOPE,
                model="wan3.0-video",
                prompt="超" * 20001,
            )
        assert exc.value.code == "video_prompt_too_long"

    @pytest.mark.unit
    async def test_dedicated_base_url_used_for_submit_and_poll(self):
        b = self._backend(wan3_base_url="https://maas-cn-hangzhou.example.com/ws-123/api/v1/")
        # 尾部斜杠归一，提交与轮询同域名（任务 id 只在创建它的 endpoint 上可查）
        assert b._request_base_url == "https://maas-cn-hangzhou.example.com/ws-123/api/v1"
        # 断言实际发出的 POST/GET URL，而非只读属性——两处若改回固定 base_url，属性断言仍会绿
        post = AsyncMock(return_value=_resp(_submit("t-wan3")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        assert await b._create_task(client, {}) == "t-wan3"
        await b._poll_once(client, "t-wan3", b._request_base_url)
        from lib.video_backends.dashscope import _VIDEO_ENDPOINT

        assert post.await_args.args[0] == f"https://maas-cn-hangzhou.example.com/ws-123/api/v1{_VIDEO_ENDPOINT}"
        assert get.await_args.args[0] == "https://maas-cn-hangzhou.example.com/ws-123/api/v1/tasks/t-wan3"

    @pytest.mark.unit
    def test_falls_back_to_shared_base_url(self):
        b = self._backend()
        assert b._request_base_url == b._base_url
        assert b._request_base_url.endswith("/api/v1")

    @pytest.mark.unit
    def test_dedicated_base_url_not_applied_to_other_models(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="wan2.7-i2v", wan3_base_url="https://maas.example.com/api/v1")
        assert b._request_base_url == b._base_url

    @pytest.mark.unit
    async def test_submit_persists_actual_base_url(self, tmp_path: Path):
        """提交时把实际使用的域名与 job_id 一并落库——续跑要靠它回放。"""
        post = AsyncMock(return_value=_resp(_submit("t-wan3")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        persist = AsyncMock()
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.video_backends.base.persist_provider_job_id", persist):
            b = self._backend(wan3_base_url="https://maas-a.example.com/ws-1/api/v1")
            await b.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", resolution="720p", task_id="db-task-1"
                )
            )

        assert persist.call_args.kwargs["base_url"] == "https://maas-a.example.com/ws-1/api/v1"
        assert persist.call_args.kwargs["endpoint"] is None

    @pytest.mark.unit
    async def test_resume_polls_submitted_base_url_after_config_change(self, tmp_path: Path):
        """在途改 wan3_base_url 后续跑：轮询仍打提交时的域名，而非当下配置解析出的域名。"""
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=AsyncMock(), get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            # 配置已被改成 B，提交时用的是 A
            b = self._backend(wan3_base_url="https://maas-b.example.com/ws-2/api/v1")
            await b.resume_video(
                "t-wan3",
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    resolution="720p",
                    submitted_base_url="https://maas-a.example.com/ws-1/api/v1",
                ),
            )

        assert get.await_args.args[0] == "https://maas-a.example.com/ws-1/api/v1/tasks/t-wan3"


class TestCustomProviderBaseUrlReplay:
    """自定义供应商委托 dashscope 协议：协议标识与提交域名分列落地，续跑按提交域名回放。"""

    @staticmethod
    def _wrapped(base_url: str):
        from lib.custom_provider.backends import CustomVideoBackend
        from lib.video_backends.dashscope import DashScopeVideoBackend

        delegate = DashScopeVideoBackend(api_key="sk", base_url=base_url, model="wan3.0-video")
        return CustomVideoBackend(
            provider_id="custom-7",
            delegate=delegate,
            model="wan3.0-video",
            endpoint="dashscope-async-video",
        )

    @pytest.mark.unit
    async def test_submit_persists_protocol_id_and_domain(self, tmp_path: Path):
        """穿过包装层的提交：endpoint 位落协议标识供比对，域名落 base_url 位供回放。"""
        post = AsyncMock(return_value=_resp(_submit("job-c1")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        persist = AsyncMock()
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.video_backends.base.persist_provider_job_id", persist):
            backend = self._wrapped("https://custom-a.example.com")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", resolution="720p", task_id="db-task-c1"
                )
            )

        assert persist.call_args.kwargs["endpoint"] == "dashscope-async-video"
        assert persist.call_args.kwargs["base_url"] == "https://custom-a.example.com/api/v1"

    @pytest.mark.unit
    async def test_resume_polls_submitted_domain_after_base_url_change(self, tmp_path: Path):
        """在途改自定义供应商的 base_url 后续跑：轮询打提交时的域名，job 仍在该域名上。"""
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=AsyncMock(), get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            # 配置已被改成 B，提交时用的是 A
            backend = self._wrapped("https://custom-b.example.com")
            await backend.resume_video(
                "job-c1",
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    resolution="720p",
                    submitted_base_url="https://custom-a.example.com/api/v1",
                ),
            )

        assert get.await_args.args[0] == "https://custom-a.example.com/api/v1/tasks/job-c1"
