from pathlib import Path

import pytest

from lib.image_backends.base import ImageCapability, ImageGenerationResult
from lib.media_generator import MediaGenerator


class _FakeImageBackend:
    """Fake ImageBackend conforming to the protocol."""

    name = "fake-image"
    model = "img-model"
    capabilities = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    def __init__(self):
        self.calls = []

    async def generate(self, request):
        self.calls.append(request)
        # Touch the output file so version tracking works
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"fake-image-data")
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self.name,
            model=self.model,
            usage_tokens=8,
        )


class _FakeVideoResult:
    def __init__(self, duration_seconds: int = 8):
        self.video_uri = "video-uri"
        self.usage_tokens = 0
        self.generate_audio = True
        self.duration_seconds = duration_seconds


class _FakeVideoBackend:
    """Fake VideoBackend conforming to the protocol."""

    name = "fake-video"
    model = "video-model"

    def __init__(self, result_duration_seconds: int | None = None):
        self.calls = []
        # None = 回显请求时长（多数后端行为）；指定值 = 模拟 provider 回报的实际计费时长
        self._result_duration_seconds = result_duration_seconds

    async def generate(self, request):
        self.calls.append(request)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"fake-video-data")
        duration = (
            self._result_duration_seconds if self._result_duration_seconds is not None else request.duration_seconds
        )
        return _FakeVideoResult(duration_seconds=duration)


class _FakeVersions:
    def __init__(self):
        self.ensure_calls = []
        self.add_calls = []

    def ensure_current_tracked(self, **kwargs):
        self.ensure_calls.append(kwargs)

    def add_version(self, **kwargs):
        self.add_calls.append(kwargs)
        return len(self.add_calls)

    def get_versions(self, resource_type, resource_id):
        return {
            "current_version": len(self.add_calls),
            "versions": [{"created_at": "2026-01-01T00:00:00Z"}] * max(1, len(self.add_calls)),
        }


class _FakeUsage:
    def __init__(self):
        self.started = []
        self.finished = []

    async def start_call(self, **kwargs):
        self.started.append(kwargs)
        return len(self.started)

    async def finish_call(self, **kwargs):
        self.finished.append(kwargs)


class _FakeConfigResolver:
    """Fake ConfigResolver，返回可控的配置值。"""

    def __init__(self, video_generate_audio: bool = False):
        self._video_generate_audio = video_generate_audio

    async def video_generate_audio(self, project_name=None):
        return self._video_generate_audio

    async def reference_payload_limits(self, provider_id=None):
        # 与真实 resolver 同契约：provider_id 为 None 或未配置时返回 service 层保守默认。
        from lib.config.service import (
            _DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
            _DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
        )

        return _DEFAULT_REFERENCE_TOTAL_MAX_BYTES, _DEFAULT_REFERENCE_SINGLE_MAX_BYTES


def _build_generator(tmp_path: Path) -> MediaGenerator:
    gen = object.__new__(MediaGenerator)
    gen.project_path = tmp_path / "projects" / "demo"
    gen.project_path.mkdir(parents=True, exist_ok=True)
    gen.project_name = "demo"
    gen._rate_limiter = None
    gen._image_backend = _FakeImageBackend()
    gen._video_backend = _FakeVideoBackend()
    gen._user_id = "default"
    gen._config = _FakeConfigResolver()
    gen._image_provider_id = None
    gen._video_provider_id = None
    gen.versions = _FakeVersions()
    gen.usage_tracker = _FakeUsage()
    return gen


class TestMediaGenerator:
    def test_get_output_path_and_invalid_type(self, tmp_path):
        gen = _build_generator(tmp_path)
        assert gen._get_output_path("storyboards", "E1S01").name == "scene_E1S01.png"
        assert gen._get_output_path("videos", "E1S01").name == "scene_E1S01.mp4"
        assert gen._get_output_path("characters", "Alice").name == "Alice.png"
        assert gen._get_output_path("reference_videos", "E1U1").name == "E1U1.mp4"
        with pytest.raises(ValueError):
            gen._get_output_path("bad", "x")

    def test_generate_image_success_and_failure(self, tmp_path):
        gen = _build_generator(tmp_path)
        output_path, version = gen.generate_image(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            aspect_ratio="9:16",
        )

        assert output_path.name == "scene_E1S01.png"
        assert version == 1
        assert gen.usage_tracker.started[0]["call_type"] == "image"
        assert gen.usage_tracker.finished[0]["status"] == "success"
        assert gen.usage_tracker.finished[0]["usage_tokens"] == 8

        async def _raise(request):
            raise RuntimeError("boom")

        gen._image_backend.generate = _raise
        with pytest.raises(RuntimeError):
            gen.generate_image(prompt="p", resource_type="characters", resource_id="A")

        assert any(item["status"] == "failed" for item in gen.usage_tracker.finished)

    @pytest.mark.asyncio
    async def test_generate_video_sync_and_async(self, tmp_path):
        gen = _build_generator(tmp_path)

        video_path, version, video_ref, video_uri = gen.generate_video(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            duration_seconds="bad",
        )
        assert video_path.name == "scene_E1S01.mp4"
        assert version == 1
        assert video_ref is None
        assert video_uri == "video-uri"

        video_path2, version2, _, _ = await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S02",
            duration_seconds="6",
        )
        assert video_path2.name == "scene_E1S02.mp4"
        assert version2 == 2
        assert gen.usage_tracker.started[-1]["call_type"] == "video"

    @pytest.mark.asyncio
    async def test_video_billed_duration_passed_to_finish_call(self, tmp_path):
        """backend 返回与请求不同的实际计费时长时，视频路径透传给 finish_call。"""
        gen = _build_generator(tmp_path)
        gen._video_backend = _FakeVideoBackend(result_duration_seconds=15)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S10",
            duration_seconds="6",
        )
        assert gen.usage_tracker.started[-1]["duration_seconds"] == 6
        assert gen.usage_tracker.finished[-1]["billed_duration_seconds"] == 15

    @pytest.mark.asyncio
    async def test_video_billed_duration_lands_in_ledger(self, tmp_path):
        """端到端：backend 返回与请求不同的实际计费时长，ApiCall 账本记录 backend 值。"""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from lib.db.base import Base
        from lib.usage_tracker import UsageTracker

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            gen = _build_generator(tmp_path)
            gen._video_backend = _FakeVideoBackend(result_duration_seconds=15)
            tracker = UsageTracker(session_factory=async_sessionmaker(engine, expire_on_commit=False))
            gen.usage_tracker = tracker

            await gen.generate_video_async(
                prompt="p",
                resource_type="videos",
                resource_id="E1S11",
                duration_seconds="6",
            )

            item = (await tracker.get_calls(project_name="demo"))["items"][0]
            assert item["status"] == "success"
            assert item["duration_seconds"] == 15
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_video_generate_audio_from_config_resolver(self, tmp_path):
        """验证 generate_video_async 通过 ConfigResolver 获取 audio 设置。"""
        gen = _build_generator(tmp_path)
        gen._config = _FakeConfigResolver(video_generate_audio=False)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S03",
        )
        # VideoBackend 路径尊重 ConfigResolver 返回的值
        assert gen.usage_tracker.started[-1]["generate_audio"] is False

    @pytest.mark.asyncio
    async def test_video_generate_audio_respects_config_true(self, tmp_path):
        """验证 video_backend 尊重 ConfigResolver 返回的 True。"""
        gen = _build_generator(tmp_path)
        gen._config = _FakeConfigResolver(video_generate_audio=True)

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S04",
        )
        assert gen.usage_tracker.started[-1]["generate_audio"] is True

    @pytest.mark.asyncio
    async def test_video_generate_audio_defaults_true_when_config_none(self, tmp_path):
        """当 self._config is None 时，fallback 默认 True，
        与 ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO 对齐（PR7 §11）。"""
        gen = _build_generator(tmp_path)
        gen._config = None

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S05",
        )
        assert gen.usage_tracker.started[-1]["generate_audio"] is True


# ── 咽喉层参考图压缩接线 ────────────────────────────────────────────────────

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from lib.media_generator import _is_413  # noqa: E402
from lib.reference_compression import LADDER_STEPS, ReferencePayloadFloorError  # noqa: E402


def _http_413_error() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://example.test")
    resp = httpx.Response(status_code=413, request=req)
    return httpx.HTTPStatusError("Request Entity Too Large", request=req, response=resp)


def _noise_png(tmp_path: Path, name: str, w: int, h: int) -> Path:
    p = tmp_path / name
    Image.effect_noise((w, h), 80).convert("RGB").save(p, format="PNG")
    return p


def _solid_png(tmp_path: Path, name: str, w: int, h: int) -> Path:
    p = tmp_path / name
    Image.new("RGB", (w, h), color=(200, 100, 50)).save(p, format="PNG")
    return p


class _ConfigurableImageBackend:
    """可配置 413 失败次数的 image backend，记录每次收到的参考图路径。"""

    name = "fake-image"
    model = "img-model"
    capabilities = {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    def __init__(self, fail_413_times: int = 0):
        self.calls = []
        self.received_refs: list[list[Path]] = []
        self._fail_left = fail_413_times

    async def generate(self, request):
        self.calls.append(request)
        self.received_refs.append([Path(r.path) for r in request.reference_images])
        if self._fail_left > 0:
            self._fail_left -= 1
            raise _http_413_error()
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"img")
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self.name,
            model=self.model,
            usage_tokens=8,
        )


class TestIs413:
    def test_httpx_413(self):
        assert _is_413(_http_413_error()) is True

    def test_phrase_match(self):
        assert _is_413(RuntimeError("Request Entity Too Large")) is True
        assert _is_413(RuntimeError("oops: PAYLOAD TOO LARGE")) is True

    def test_byte_count_not_misread(self):
        # 修正④：不用裸 "413" 子串，避免字节数 / 请求 ID 误命中
        assert _is_413(RuntimeError("only 41300 bytes uploaded")) is False
        assert _is_413(RuntimeError("error code 413xyz")) is False

    def test_non_413_status(self):
        req = httpx.Request("POST", "https://example.test")
        resp = httpx.Response(status_code=400, request=req)
        err = httpx.HTTPStatusError("bad request", request=req, response=resp)
        assert _is_413(err) is False

    def test_sdk_status_code_attr(self):
        # OpenAI/xai 风格 SDK 异常：直接带 .status_code
        class _SdkErr(Exception):
            status_code = 413

        assert _is_413(_SdkErr("too big")) is True

    def test_sdk_code_attr(self):
        # google-genai 风格 APIError：带 .code
        class _ApiErr(Exception):
            code = 413

        assert _is_413(_ApiErr("Request payload size exceeds the limit")) is True

    def test_sdk_non_413_code_not_matched(self):
        class _ApiErr(Exception):
            code = 400

        assert _is_413(_ApiErr("bad request")) is False

    def test_string_status_code_413(self):
        # 个别 SDK / mock 把状态码给成字符串 "413"，需防御性 int 转换
        class _StrErr(Exception):
            status_code = "413"

        assert _is_413(_StrErr("too big")) is True

    def test_non_numeric_status_code_falls_back_to_phrase(self):
        # 非数字状态码不应抛 ValueError，落回短语匹配
        class _WeirdErr(Exception):
            status_code = "not-a-number"

        assert _is_413(_WeirdErr("totally unrelated")) is False
        assert _is_413(_WeirdErr("request entity too large")) is True


class TestReferenceCompressionSeam:
    async def test_backend_receives_compressed_copy_source_untouched(self, tmp_path):
        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend()
        gen._image_backend = backend

        src = _noise_png(tmp_path, "ref.png", 3000, 3000)
        src_bytes_before = src.read_bytes()

        await gen.generate_image_async(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            reference_images=[str(src)],
        )

        received = backend.received_refs[-1]
        assert len(received) == 1
        # backend 收到的是压缩临时副本，而非源路径
        assert received[0] != src
        # 源文件字节未被改动（只动上传副本）
        assert src.read_bytes() == src_bytes_before
        # 临时副本退出后清理
        assert not received[0].exists()

    async def test_413_retry_then_success_single_finish_call(self, tmp_path):
        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend(fail_413_times=1)
        gen._image_backend = backend

        src = _noise_png(tmp_path, "ref.png", 1200, 1200)
        await gen.generate_image_async(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            reference_images=[str(src)],
        )

        # 一次 413 后降档重试成功：backend 被调两次
        assert len(backend.calls) == 2
        # 只记一条 success（413 内循环重试不额外记账）
        assert len(gen.usage_tracker.finished) == 1
        assert gen.usage_tracker.finished[0]["status"] == "success"

    async def test_413_exhausted_raises_floor_records_failed(self, tmp_path):
        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend(fail_413_times=99)
        gen._image_backend = backend

        src = _noise_png(tmp_path, "ref.png", 800, 800)
        with pytest.raises(ReferencePayloadFloorError):
            await gen.generate_image_async(
                prompt="p",
                resource_type="storyboards",
                resource_id="E1S01",
                reference_images=[str(src)],
            )

        # 走完梯子（基线 + LADDER_STEPS-1 档 + 地板）= LADDER_STEPS + 1 次调用后耗尽
        assert len(backend.calls) == LADDER_STEPS + 1
        # 耗尽冒泡到外层 except 记一条 failed
        assert len(gen.usage_tracker.finished) == 1
        assert gen.usage_tracker.finished[0]["status"] == "failed"

    async def test_t2i_no_refs_413_not_converted_to_floor(self, tmp_path):
        # 无参考图（T2I）的 413 与参考图无关，不应被误转成 floor、也不降档
        gen = _build_generator(tmp_path)
        backend = _ConfigurableImageBackend(fail_413_times=99)
        gen._image_backend = backend

        with pytest.raises(httpx.HTTPStatusError):
            await gen.generate_image_async(
                prompt="p",
                resource_type="storyboards",
                resource_id="E1S01",
            )
        # 单次调用，无降档重试
        assert len(backend.calls) == 1

    async def test_video_frame_not_resized_array_laddered(self, tmp_path):
        gen = _build_generator(tmp_path)

        class _CapturingVideoBackend:
            name = "fake-video"
            model = "video-model"

            def __init__(self):
                self.start_dims = None
                self.ref_dims: list[tuple[int, int]] = []

            async def generate(self, request):
                if request.start_image:
                    with Image.open(request.start_image) as im:
                        self.start_dims = im.size
                for r in request.reference_images or []:
                    with Image.open(r) as im:
                        self.ref_dims.append(im.size)
                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                request.output_path.write_bytes(b"v")
                return _FakeVideoResult()

        backend = _CapturingVideoBackend()
        gen._video_backend = backend

        start = _solid_png(tmp_path, "start.png", 3000, 2000)  # FRAME：永不缩尺寸（重格式仅重编码）
        ref = _solid_png(tmp_path, "ref.png", 3000, 3000)  # ARRAY：走梯子缩到 ≤2048

        await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            start_image=str(start),
            reference_images=[ref],
        )

        assert backend.start_dims == (3000, 2000)  # FRAME 尺寸保持
        assert max(backend.ref_dims[0]) == 2048  # ARRAY 缩到长边 2048
