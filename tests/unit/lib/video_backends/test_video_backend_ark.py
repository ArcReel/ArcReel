"""ArkVideoBackend 单元测试 — mock Ark SDK。"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from lib.video_backends.ark import ArkVideoBackend
from lib.video_backends.base import (
    ReferenceAudioMode,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.video_frame_slots import FIRST_FRAME_ADAPTIVE_RATIO, resolve_first_frame_aspect_ratio
from tests.fakes import bounded_poll_clock, captured_ark_clients


@pytest.fixture
def mock_ark_client():
    client = MagicMock()
    client.content_generation = MagicMock()
    client.content_generation.tasks = MagicMock()
    return client


@pytest.fixture
def ark_backend(mock_ark_client):
    with patch("lib.video_backends.ark.create_ark_client", return_value=mock_ark_client):
        b = ArkVideoBackend(
            api_key="test-ark-key",
        )
    b._client = mock_ark_client
    return b


def _mock_httpx_stream(data: bytes = b"fake-mp4-data"):
    """成片下载的出站流：respx 在 transport 层拦截，任一下载 URL 都回给定字节。

    保留 start/stop 形态（调用方以 try/finally 收尾），换掉的是拦截层——落在断言范围内的
    是真实序列化后的流式 GET，而不是客户端替身记录的调用参数。
    """
    router = respx.mock(assert_all_called=False)
    router.start()
    router.get(url__regex=r".*").mock(return_value=httpx.Response(200, content=data))
    return router


class TestArkProperties:
    def test_name(self, ark_backend):
        assert ark_backend.name == "ark"


class TestArkGenerate:
    async def test_text_to_video(self, ark_backend, tmp_path):
        """文生视频：无 start_image。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-20250101-test"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = 58944
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 246840
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="a flower field",
                output_path=output,
                duration_seconds=5,
            )
            result = await ark_backend.generate(request)
        finally:
            patcher.stop()

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "ark"
        assert result.model == "doubao-seedance-1-5-pro-251215"
        assert result.seed == 58944
        assert result.usage_tokens == 246840
        assert result.task_id == "cgt-20250101-test"

    async def test_image_to_video(self, ark_backend, tmp_path):
        """图生视频：有 start_image，必须带 role=first_frame。"""
        output = tmp_path / "out.mp4"
        frame = tmp_path / "scene_E1S01.png"
        frame.write_bytes(b"fake-png")

        create_result = MagicMock()
        create_result.id = "cgt-i2v-test"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video2.mp4"
        get_result.seed = 12345
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 200000
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="girl opens eyes",
                output_path=output,
                start_image=frame,
                generate_audio=True,
            )
            result = await ark_backend.generate(request)
        finally:
            patcher.stop()

        assert result.provider == "ark"
        create_call = ark_backend._client.content_generation.tasks.create
        call_kwargs = create_call.call_args
        content_arg = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content")
        assert len(content_arg) == 2
        assert content_arg[1]["type"] == "image_url"
        assert content_arg[1]["image_url"]["url"].startswith("data:image/")
        assert content_arg[1]["role"] == "first_frame"

    async def test_first_last_frame_role_fields(self, ark_backend, tmp_path):
        """首尾帧：start_image/end_image 必须分别带 role=first_frame / role=last_frame，
        且 image_url 对象不再使用 position（由 role 表达位置）。"""
        output = tmp_path / "out.mp4"
        first = tmp_path / "first.png"
        first.write_bytes(b"fake-first")
        last = tmp_path / "last.png"
        last.write_bytes(b"fake-last")

        create_result = MagicMock()
        create_result.id = "cgt-fl-test"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = None
        get_result.usage = None
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="morph",
                output_path=output,
                start_image=first,
                end_image=last,
            )
            await ark_backend.generate(request)
        finally:
            patcher.stop()

        create_kwargs = ark_backend._client.content_generation.tasks.create.call_args.kwargs
        content_arg = create_kwargs["content"]
        image_items = [c for c in content_arg if c["type"] == "image_url"]
        assert len(image_items) == 2
        assert image_items[0]["role"] == "first_frame"
        assert image_items[1]["role"] == "last_frame"
        # role 表达位置后，不应再塞 position 到 image_url
        assert "position" not in image_items[1]["image_url"]

    async def test_reference_images_role(self, ark_backend, tmp_path):
        """参考图：每张 reference_images 必须带 role=reference_image（Ark 多图触发条件）。"""
        output = tmp_path / "out.mp4"
        ref1 = tmp_path / "ref1.jpg"
        ref1.write_bytes(b"fake-ref-1")
        ref2 = tmp_path / "ref2.jpg"
        ref2.write_bytes(b"fake-ref-2")

        create_result = MagicMock()
        create_result.id = "cgt-refs-test"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = None
        get_result.usage = None
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="[图1] 与 [图2] 对话",
                output_path=output,
                reference_images=[ref1, ref2],
            )
            await ark_backend.generate(request)
        finally:
            patcher.stop()

        create_kwargs = ark_backend._client.content_generation.tasks.create.call_args.kwargs
        content_arg = create_kwargs["content"]
        image_items = [c for c in content_arg if c["type"] == "image_url"]
        assert len(image_items) == 2
        assert all(item["role"] == "reference_image" for item in image_items)

    async def test_missing_end_image_fails_loud(self, ark_backend, tmp_path):
        """尾帧文件不存在时中止提交：静默跳过会照常计费并产出没有尾帧的成片。"""
        ark_backend._client.content_generation.tasks.create = MagicMock()

        request = VideoGenerationRequest(
            prompt="morph",
            output_path=tmp_path / "out.mp4",
            end_image=tmp_path / "gone.png",
        )
        with pytest.raises(VideoCapabilityError) as exc:
            await ark_backend.generate(request)
        assert exc.value.code == "video_end_image_unreadable"
        ark_backend._client.content_generation.tasks.create.assert_not_called()

    async def test_missing_reference_image_fails_loud(self, ark_backend, tmp_path):
        """参考图缺失时中止提交，理由同尾帧：少一张仍会出片，成片却与意图不符。"""
        present = tmp_path / "ref1.jpg"
        present.write_bytes(b"fake-ref-1")
        ark_backend._client.content_generation.tasks.create = MagicMock()

        request = VideoGenerationRequest(
            prompt="[图1] 与 [图2] 对话",
            output_path=tmp_path / "out.mp4",
            reference_images=[present, tmp_path / "gone.jpg"],
        )
        with pytest.raises(VideoCapabilityError) as exc:
            await ark_backend.generate(request)
        assert exc.value.code == "video_reference_images_unreadable"
        ark_backend._client.content_generation.tasks.create.assert_not_called()

    async def test_failed_task_raises(self, ark_backend, tmp_path):
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-fail"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "failed"
        get_result.error = "content violation"
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(RuntimeError, match="Ark 视频生成失败"):
            await ark_backend.generate(request)

    async def test_with_seed_and_flex(self, ark_backend, tmp_path):
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-flex"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = 42
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 100000
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output,
                seed=42,
                service_tier="flex",
            )
            await ark_backend.generate(request)
        finally:
            patcher.stop()

        create_call = ark_backend._client.content_generation.tasks.create
        call_kwargs = create_call.call_args
        assert call_kwargs.kwargs.get("seed") == 42 or call_kwargs[1].get("seed") == 42
        assert call_kwargs.kwargs.get("service_tier") == "flex" or call_kwargs[1].get("service_tier") == "flex"

    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="Ark API Key"):
                ArkVideoBackend(api_key=None)


class TestArkRetryBehavior:
    """测试任务创建与轮询的重试分离行为。"""

    async def test_poll_transient_error_retries_without_recreating_task(self, ark_backend, tmp_path):
        """轮询阶段瞬态错误应重试轮询，而不是重新创建任务。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-retry-test"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_success = MagicMock()
        get_success.status = "succeeded"
        get_success.content = MagicMock()
        get_success.content.video_url = "https://cdn.example.com/video.mp4"
        get_success.seed = None
        get_success.usage = None

        # 第一次轮询抛 ConnectionError，第二次成功
        ark_backend._client.content_generation.tasks.get = MagicMock(
            side_effect=[ConnectionError("connection reset"), get_success]
        )

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(prompt="test", output_path=output)
            with bounded_poll_clock():
                result = await ark_backend.generate(request)
        finally:
            patcher.stop()

        assert result.task_id == "cgt-retry-test"
        # 关键断言：任务只创建了一次
        assert ark_backend._client.content_generation.tasks.create.call_count == 1
        # 轮询调用了两次（一次失败 + 一次成功）
        assert ark_backend._client.content_generation.tasks.get.call_count == 2

    async def test_create_retries_on_transient_error(self, ark_backend, tmp_path):
        """任务创建阶段的瞬态错误应由 @with_retry_async 重试。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-create-retry"
        # 第一次创建抛 ConnectionError，第二次成功
        ark_backend._client.content_generation.tasks.create = MagicMock(
            side_effect=[ConnectionError("connection reset"), create_result]
        )

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = None
        get_result.usage = None
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(prompt="test", output_path=output)
            with (
                bounded_poll_clock(),
            ):
                result = await ark_backend.generate(request)
        finally:
            patcher.stop()

        assert result.task_id == "cgt-create-retry"
        # 创建调用了两次（一次失败 + 一次成功）
        assert ark_backend._client.content_generation.tasks.create.call_count == 2

    async def test_poll_non_retryable_error_propagates(self, ark_backend, tmp_path):
        """轮询阶段不可重试的错误应直接抛出。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-no-retry"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        ark_backend._client.content_generation.tasks.get = MagicMock(side_effect=ValueError("invalid response"))

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(ValueError, match="invalid response"):
            with bounded_poll_clock():
                await ark_backend.generate(request)

        # 创建只调用一次，轮询只尝试一次就抛出
        assert ark_backend._client.content_generation.tasks.create.call_count == 1
        assert ark_backend._client.content_generation.tasks.get.call_count == 1


class TestArkModelCapabilities:
    """测试不同模型的能力映射。"""

    def test_seedance_1_5_pro_default_model_supports_last_frame(self):
        """DEFAULT_MODEL：能力表标首尾帧，generate() 下发 role=last_frame。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-5-pro-251215")
        assert caps.last_frame is True

    def test_seedance_1_0_pro_fast_no_last_frame(self):
        """能力表「图生视频-首尾帧」列该型号标 "-"，但「图生视频-首帧」列仍是 ✅。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-0-pro-fast-251015")
        assert caps.first_frame is True
        assert caps.last_frame is False

    def test_seedance_1_0_pro_fast_dot_naming_no_last_frame(self):
        """上游命名不统一：费用文档用点号 "1.0" 而非连字符 "1-0"，判定须同时兼容两种写法。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1.0-pro-fast")
        assert caps.last_frame is False

    def test_seedance_1_0_pro_non_fast_supports_last_frame(self):
        """能力表标首尾帧 ✅；型号名含 "seedance-1-0-pro" 是 "seedance-1-0-pro-fast" 的前缀子串，
        验证白名单与黑名单的先后顺序不会把非 fast 型号误判为不支持。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-0-pro-250528")
        assert caps.last_frame is True

    def test_unknown_model_defaults_to_no_last_frame(self):
        """白名单而非黑名单：能力表之外的未知型号（自定义供应商新配置、上游未来新增）一律保守
        判定为不支持尾帧，避免错误声明支持而绕过硬拒绝、产生真实供应商调用与扣费。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-9-9-ultra-future")
        assert caps.last_frame is False

    def test_unknown_suffix_on_known_prefix_does_not_inherit_last_frame(self):
        """白名单命中要求边界匹配：型号名包含已验证前缀 "seedance-1-5-pro" 但带未知后缀
        （非纯数字日期戳）时，不能因子串包含关系继承该前缀型号的尾帧能力。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-5-pro-future")
        assert caps.last_frame is False

    @pytest.mark.parametrize(
        "model", ["doubao-seedance-1-5-pro-251215", "doubao-seedance-1.5-pro", "dreamina-seedance-1-5-pro"]
    )
    def test_seedance_1_5_pro_declares_reference_images(self, model: str):
        """1.5 pro 是既有的参考生视频可用路径：_create_task 对任何 model 都序列化 role=reference_image，
        编排层按 registry 的 9 张派图。此处声明 0 会让 gate_video_request 把这些请求整批拒掉。"""
        caps = ArkVideoBackend.video_capabilities_for_model(model)
        assert caps.max_reference_images == 9

    def test_unknown_model_declares_no_reference_images(self):
        """未上表型号保守判 0：与尾帧同口径，不为未知型号假设参考图容量。"""
        assert (
            ArkVideoBackend.video_capabilities_for_model("doubao-seedance-9-9-ultra-future").max_reference_images == 0
        )
        assert ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-5-pro-future").max_reference_images == 0

    def test_seedance_1_0_lite_t2v_no_last_frame(self):
        """纯文生视频型号，能力表「图生视频-首帧」「图生视频-首尾帧」均标 "-"，不接受任何图片输入。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-0-lite-t2v-250428")
        assert caps.first_frame is False
        assert caps.last_frame is False

    def test_seedance_1_0_lite_i2v_supports_last_frame(self):
        """能力表标首尾帧 ✅，且型号名含 "lite-t2v" 判定串的邻近变体，验证不误命中。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-0-lite-i2v-250428")
        assert caps.first_frame is True
        assert caps.last_frame is True

    @pytest.mark.parametrize(
        "model",
        [
            "doubao-seedance-2-0-260128",
            "doubao-seedance-2-0-fast-260128",
            "doubao-seedance-2-0-mini-260615",
            "doubao-seedance-2.0",
            "doubao-seedance-2.0-fast",
            "doubao-seedance-2.0-mini",
        ],
    )
    def test_seedance_2_known_variants_support_last_frame(self, model: str):
        caps = ArkVideoBackend.video_capabilities_for_model(model)
        assert caps.last_frame is True

    @pytest.mark.parametrize("model", ["doubao-seedance-2-5-260628", "doubao-seedance-2.5", "dreamina-seedance-2-5"])
    def test_seedance_2_5_declares_own_material_limits(self, model: str):
        """2.5 的素材上限比 2.0 宽（参考图 30 / 音频 10 段 30 秒），不得沿用 2.0 的一档。"""
        caps = ArkVideoBackend.video_capabilities_for_model(model)
        assert caps.last_frame is True
        assert caps.max_reference_images == 30
        assert caps.reference_audio_mode == ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 10
        assert caps.max_reference_audio_total_seconds == 30.0

    def test_seedance_2_5_requires_adaptive_first_frame_ratio(self):
        """有首帧时比例交给上游按首帧自适应：下发固定 ratio 会与首帧尺寸冲突。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-5-260628")
        assert caps.first_frame_ratio_adaptive_only is True

    @pytest.mark.parametrize("aspect_ratio", ["16:9", "9:16"])
    def test_seedance_2_5_first_frame_request_ratio_resolves_to_adaptive(self, aspect_ratio: str):
        """能力声明经共享解析器落到实际下发值：带首帧时任何用户比例都被覆盖成 adaptive。

        单测 caps 标志位只锁住声明，锁不住「首帧任务 ratio 恒为 adaptive」这条对外承诺——
        承诺是声明与 resolve_first_frame_aspect_ratio 合起来才成立的，故在此合测。
        """
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-5-260628")
        resolved = resolve_first_frame_aspect_ratio(caps=caps, aspect_ratio=aspect_ratio, has_first_frame=True)
        assert resolved == FIRST_FRAME_ADAPTIVE_RATIO
        # 无首帧的纯文生 / 仅参考图请求不受影响，用户比例原样下发
        assert (
            resolve_first_frame_aspect_ratio(caps=caps, aspect_ratio=aspect_ratio, has_first_frame=False)
            == aspect_ratio
        )

    def test_seedance_2_0_keeps_passthrough_first_frame_ratio(self):
        """2.0 未声明 adaptive：比例语义与素材上限按 2.0 自己的档位，不随 2.5 分支走。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-0-260128")
        assert caps.first_frame_ratio_adaptive_only is False
        assert caps.max_reference_images == 9
        assert caps.max_reference_audio_count == 3

    def test_seedance_2_5_unknown_suffix_does_not_inherit_verified_caps(self):
        """未验证的 2.5 变体只保留族群级的参考图容量，尾帧与参考音频不继承。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-5-future")
        assert caps.last_frame is False
        assert caps.reference_audio_mode == ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0
        assert caps.max_reference_audio_total_seconds is None

    def test_seedance_2_unknown_suffix_does_not_inherit_last_frame(self):
        """白名单命中要求边界匹配：型号名包含已验证前缀 "seedance-2-0" 但带未知后缀
        （非已验证的 fast/mini/日期戳形态）时，不能因子串包含关系继承 2.0 系列的尾帧能力。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-0-future")
        assert caps.last_frame is False


class TestArkPollBudget:
    """长时长任务的轮询上限：固定 600s 只够到 10 秒档。"""

    @pytest.mark.parametrize(
        ("duration", "expected_max_wait"),
        [(5, 600), (10, 600), (15, 900), (30, 1800)],
    )
    async def test_default_tier_max_wait_scales_with_duration(self, ark_backend, tmp_path, duration, expected_max_wait):
        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=duration, service_tier="default"
        )
        with patch("lib.video_backends.ark.poll_with_retry", new_callable=AsyncMock) as poll:
            poll.side_effect = RuntimeError("stop")
            with pytest.raises(RuntimeError):
                await ark_backend._poll_until_done("task-1", request)
        assert poll.call_args.kwargs["max_wait"] == expected_max_wait
        assert poll.call_args.kwargs["poll_interval"] == 10

    async def test_flex_tier_keeps_hour_long_budget(self, ark_backend, tmp_path):
        """flex 队列本就按小时级排队，不随时长缩放。"""
        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=30, service_tier="flex"
        )
        with patch("lib.video_backends.ark.poll_with_retry", new_callable=AsyncMock) as poll:
            poll.side_effect = RuntimeError("stop")
            with pytest.raises(RuntimeError):
                await ark_backend._poll_until_done("task-1", request)
        assert poll.call_args.kwargs["max_wait"] == 3600
        assert poll.call_args.kwargs["poll_interval"] == 60

    @pytest.mark.parametrize("model", ["doubao-seedance-2-0-260128", "doubao-seedance-2-5-260628"])
    async def test_tier_stripped_model_polls_on_default_cadence(self, mock_ark_client, tmp_path, model):
        """不下传 service_tier 的型号按默认档建任务，轮询节奏须跟着走默认档而非请求里残留的 flex。"""
        with patch("lib.video_backends.ark.create_ark_client", return_value=mock_ark_client):
            b = ArkVideoBackend(api_key="test-ark-key", model=model)
        b._client = mock_ark_client
        assert b._supports_service_tier is False
        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=30, service_tier="flex"
        )
        with patch("lib.video_backends.ark.poll_with_retry", new_callable=AsyncMock) as poll:
            poll.side_effect = RuntimeError("stop")
            with pytest.raises(RuntimeError):
                await b._poll_until_done("task-1", request)
        assert poll.call_args.kwargs["poll_interval"] == 10
        assert poll.call_args.kwargs["max_wait"] == 1800


class TestArkServiceTierParam:
    """service_tier 只对支持该参数的模型传入，否则 API 会报错。"""

    @pytest.mark.parametrize(
        "model",
        [
            "doubao-seedance-2-0-260128",
            # ark-agent-plan 用点号命名，BytePlus 国际站用 dreamina- 前缀：同族的各种命名
            # 变体都必须被 _is_seedance_2 识别，漏掉任一种都会让 r2v 请求被上游 400 拒。
            "doubao-seedance-2.0",
            "doubao-seedance-2.0-fast",
            "dreamina-seedance-2-0-260128",
            "dreamina-seedance-2-0-fast-260128",
            # 2.5 与 2.0 同样拒收 service_tier，但走独立判定，需各自锁定
            "doubao-seedance-2-5-260628",
            "doubao-seedance-2.5",
        ],
    )
    async def test_seedance_2_does_not_send_service_tier(self, tmp_path, model):
        """seedance-2 系列（含 dreamina- 前缀的自定义供应商命名）不得发 service_tier，否则 r2v 上游 400。"""
        output = tmp_path / "out.mp4"
        mock_client = MagicMock()
        mock_client.content_generation = MagicMock()
        mock_client.content_generation.tasks = MagicMock()

        with patch("lib.video_backends.ark.create_ark_client", return_value=mock_client):
            ark_backend = ArkVideoBackend(api_key="test", model=model)
        ark_backend._client = mock_client

        create_result = MagicMock()
        create_result.id = "cgt-seedance2"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/v.mp4"
        get_result.seed = None
        get_result.usage = None
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(prompt="test", output_path=output)
            with bounded_poll_clock():
                await ark_backend.generate(request)
        finally:
            patcher.stop()

        create_kwargs = ark_backend._client.content_generation.tasks.create.call_args.kwargs
        assert "service_tier" not in create_kwargs

    async def test_seedance_1_5_sends_service_tier(self, ark_backend, tmp_path):
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-seedance15"
        ark_backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/v.mp4"
        get_result.seed = None
        get_result.usage = None
        ark_backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(prompt="test", output_path=output)
            with bounded_poll_clock():
                await ark_backend.generate(request)
        finally:
            patcher.stop()

        create_kwargs = ark_backend._client.content_generation.tasks.create.call_args.kwargs
        assert create_kwargs.get("service_tier") == "default"


class TestArkVideoBackendBaseUrl:
    def test_custom_base_url_passed_through(self):
        with captured_ark_clients("lib.video_backends.ark") as created:
            ArkVideoBackend(api_key="k", base_url="https://ark.cn-beijing.volces.com/api/plan/v3")
        assert created == [{"api_key": "k", "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3"}]

    def test_default_base_url_is_none(self):
        with captured_ark_clients("lib.video_backends.ark") as created:
            ArkVideoBackend(api_key="k")
        assert created == [{"api_key": "k", "base_url": None}]


class TestIsArkNotFound:
    """用 task_not_found / tasknotfound 精确匹配，剔除宽泛 "not found" 兜底；
    保留 "expired" 字串识别（_poll_until_done 把 status=expired 转 RuntimeError）。"""

    def test_excludes_business_not_found(self):
        from lib.video_backends.ark import _is_ark_not_found

        exc = RuntimeError("reference image not found in storage")
        assert _is_ark_not_found(exc) is False

    def test_recognizes_task_not_found(self):
        from lib.video_backends.ark import _is_ark_not_found

        assert _is_ark_not_found(RuntimeError("task_not_found: invalid id")) is True

    def test_recognizes_expired_status(self):
        from lib.video_backends.ark import _is_ark_not_found

        assert _is_ark_not_found(RuntimeError("Ark 任务失败 ... status=expired")) is True

    def test_recognizes_404(self):
        from lib.video_backends.ark import _is_ark_not_found

        exc = RuntimeError("any")
        exc.status_code = 404  # type: ignore[attr-defined]
        assert _is_ark_not_found(exc) is True


class TestArkReferenceAudio:
    """Seedance 2.0 参考音频：content 数组条目 + 顺序契约。"""

    @staticmethod
    def _seedance_2_backend():
        with patch("lib.video_backends.ark.create_ark_client", return_value=MagicMock()):
            return ArkVideoBackend(api_key="test", model="doubao-seedance-2-0-260128")

    def test_request_log_view_collapses_media_without_base64(self):
        """素材不进日志：content 内的图片与音频都是 base64 data URI，原样交给日志格式化
        只会截断成前缀而非剔除，等于把素材内容（音频还可能带 mp3 的 ID3 元数据）写进 info 日志。
        prompt 是用户文本、排查生成效果要用，保留。"""
        from lib.video_backends.ark import _safe_create_params_for_log

        view = _safe_create_params_for_log(
            {
                "model": "doubao-seedance-2-0-260128",
                "content": [
                    {"type": "text", "text": "两人对话"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                    {"type": "audio_url", "audio_url": {"url": "data:audio/mp3;base64,BBBB"}},
                ],
            }
        )

        assert "base64" not in json.dumps(view, ensure_ascii=False)
        assert view["content"] == "<1 audio_url, 1 image_url, 1 text>"
        assert view["prompt"] == "两人对话"

    def test_seedance_2_declares_audio_capability(self):
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-0-260128")
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 3

    @pytest.mark.parametrize(
        "model",
        ["doubao-seedance-2.0-fast", "doubao-seedance-2-0-mini-260615", "dreamina-seedance-2-0-260128"],
    )
    def test_seedance_2_variants_declare_audio_capability(self, model):
        assert ArkVideoBackend.video_capabilities_for_model(model).reference_audio_mode is ReferenceAudioMode.DIRECT

    def test_unverified_seedance_2_variant_does_not_inherit_audio(self):
        """未上表的 2.0 变体不得因子串包含关系继承音频参考能力（与尾帧同一白名单口径）。"""
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-0-future")
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0

    def test_seedance_1_5_declares_no_audio_capability(self):
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-5-pro-251215")
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE

    async def test_reference_audio_sent_as_audio_url_entries(self, tmp_path):
        """每段音频发一条 type=audio_url + role=reference_audio，且顺序与请求字段一致。"""
        ark_backend = self._seedance_2_backend()
        first_audio = tmp_path / "a.mp3"
        first_audio.write_bytes(b"id3-first")
        second_audio = tmp_path / "b.wav"
        second_audio.write_bytes(b"riff-second")
        ref = tmp_path / "r.png"
        ref.write_bytes(b"fake-ref")

        ark_backend._client.content_generation.tasks.create = MagicMock(side_effect=RuntimeError("stop"))

        request = VideoGenerationRequest(
            prompt="两人对话",
            output_path=tmp_path / "out.mp4",
            reference_images=[ref],
            reference_audio_files=[first_audio, second_audio],
        )
        with pytest.raises(RuntimeError):
            await ark_backend._create_task(request)

        content = ark_backend._client.content_generation.tasks.create.call_args.kwargs["content"]
        audio_items = [c for c in content if c["type"] == "audio_url"]
        assert len(audio_items) == 2
        assert [c["role"] for c in audio_items] == ["reference_audio", "reference_audio"]
        # 顺序即 prompt 中「音频N」的编号：第一段必须是请求里的第一段
        assert audio_items[0]["audio_url"]["url"].startswith("data:audio/mp3;base64,")
        assert audio_items[1]["audio_url"]["url"].startswith("data:audio/wav;base64,")

    async def test_no_audio_entries_when_request_has_none(self, tmp_path):
        ark_backend = self._seedance_2_backend()
        ref = tmp_path / "r.png"
        ref.write_bytes(b"fake-ref")
        ark_backend._client.content_generation.tasks.create = MagicMock(side_effect=RuntimeError("stop"))

        with pytest.raises(RuntimeError):
            await ark_backend._create_task(
                VideoGenerationRequest(prompt="x", output_path=tmp_path / "o.mp4", reference_images=[ref])
            )

        content = ark_backend._client.content_generation.tasks.create.call_args.kwargs["content"]
        assert not [c for c in content if c["type"] == "audio_url"]

    async def test_unsupported_audio_format_raises(self, tmp_path):
        """格式不受支持时抛错而非跳过：跳过会让其后所有音频编号整体前移、错绑角色音色。"""
        ark_backend = self._seedance_2_backend()
        bad = tmp_path / "a.ogg"
        bad.write_bytes(b"ogg")

        with pytest.raises(VideoCapabilityError) as exc:
            await ark_backend._create_task(
                VideoGenerationRequest(prompt="x", output_path=tmp_path / "o.mp4", reference_audio_files=[bad])
            )

        assert exc.value.code == "video_reference_audio_format_unsupported"

    async def test_missing_audio_file_raises(self, tmp_path):
        ark_backend = self._seedance_2_backend()

        with pytest.raises(VideoCapabilityError) as exc:
            await ark_backend._create_task(
                VideoGenerationRequest(
                    prompt="x",
                    output_path=tmp_path / "o.mp4",
                    reference_audio_files=[tmp_path / "missing.mp3"],
                )
            )

        assert exc.value.code == "video_reference_audio_unreadable"
