"""记账特征化测试矩阵：六通道落库行为锁。

以生成器公开方法驱动 + 假 backend（Protocol 实现，返回可控结果/抛可控异常）+ 真内存
SQLite 落库 + 冻结时钟，把记账链路（Ledger → UsageRepository → 结算 →
CostCalculator → 定价策略）的落库行为逐字段锁定：链路内部不 mock，断言 ApiCall 整行，
任何记账落库行为的意外变化都会在此当场失败。

六条通道：
- image / audio / video / text 生成括号（MediaGenerator / TextGenerator 公开方法）
- resume 补账（MediaGenerator.resume_video_async：成功与过期失败两分支 + pending 幂等守卫）
- agent 会话事后补录（SessionManager._record_assistant_usage）

时间字段口径：usage_repo 的 utc_now 被替换为步进时钟（每读一次前进 5 秒），
started_at / finished_at 可精确断言。SQLite 回读 DateTime(timezone=True) 为 naive
datetime，跨 session 的 finish/finalize 中相减前按 UTC 补齐 naive 一侧 tzinfo 再与
aware(finished_at) 相减，duration_ms 按真实步进（单次 start→finish 为 5000ms）回写
（PostgreSQL 下同样为真实时长，不再分岔）。created_at / updated_at 由 ORM 列默认绑定
真实时钟、非记账语义字段，仅断言为 datetime。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from lib.audio_backends.base import AudioCapability, AudioSynthesisRequest, AudioSynthesisResult
from lib.config.resolver import ConfigResolver
from lib.db.base import Base
from lib.db.models.api_call import ApiCall
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult
from lib.ledger import Ledger
from lib.media_generator import MediaGenerator
from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult
from lib.text_generator import TextGenerator
from lib.video_backends.base import (
    ResumeExpiredError,
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from server.agent_runtime.session_actor import SessionActor
from server.agent_runtime.session_manager import ManagedSession, SessionManager
from server.agent_runtime.session_store import SessionMetaStore

# ---------------------------------------------------------------------------
# 冻结时钟：写入侧（usage_repo.utc_now）是 aware datetime，SQLite 回读为 naive。
# ---------------------------------------------------------------------------

_T0_AWARE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_CLOCK_STEP = timedelta(seconds=5)
# ApiCall 行回读形态（naive）：T0 = started_at，T1 = 紧随其后一次 finish/finalize 的 finished_at
T0 = _T0_AWARE.replace(tzinfo=None)
T1 = T0 + _CLOCK_STEP


class _SteppingClock:
    """每读一次前进固定步长的确定性时钟。"""

    def __init__(self) -> None:
        self._now = _T0_AWARE

    def __call__(self) -> datetime:
        current = self._now
        self._now += _CLOCK_STEP
        return current


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> _SteppingClock:
    clock = _SteppingClock()
    monkeypatch.setattr("lib.db.repositories.usage_repo.utc_now", clock)
    return clock


# ---------------------------------------------------------------------------
# 真内存 SQLite 记账落点
# ---------------------------------------------------------------------------


@dataclass
class _AccountingDb:
    engine: AsyncEngine
    factory: async_sessionmaker

    async def fetch_rows(self) -> list[dict[str, Any]]:
        """按 id 升序取全部 ApiCall 行，整行快照（表列全集）。

        新增列会自动进入快照并让既有期望字典失配——行为锁刻意如此，逼迫矩阵随
        schema 演进被有意识地更新。
        """
        async with self.factory() as session:
            result = await session.execute(select(ApiCall).order_by(ApiCall.id))
            return [
                {column.key: getattr(row, column.key) for column in ApiCall.__table__.columns}
                for row in result.scalars().all()
            ]

    async def fetch_only_row(self) -> dict[str, Any]:
        rows = await self.fetch_rows()
        assert len(rows) == 1, f"期望恰好 1 行 ApiCall，实际 {len(rows)} 行"
        return rows[0]


@pytest.fixture
async def acct(frozen_clock: _SteppingClock) -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    db = _AccountingDb(engine=engine, factory=factory)
    yield db
    await engine.dispose()


def _assert_full_row(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    """整行断言：created_at / updated_at 仅要求为 datetime，其余列逐字段精确比对。"""
    actual = dict(actual)
    for column in ("created_at", "updated_at"):
        value = actual.pop(column)
        assert isinstance(value, datetime), f"{column} 应为 datetime"
    assert actual == expected


def _expected_row(**overrides: Any) -> dict[str, Any]:
    """ApiCall 整行期望基线：成功、零费用、默认 user，各 token 列为空。"""
    row: dict[str, Any] = {
        "id": 1,
        "user_id": "default",
        "project_name": "demo",
        "call_type": "image",
        "model": "",
        "prompt": None,
        "resolution": None,
        "duration_seconds": None,
        "aspect_ratio": None,
        "generate_audio": True,
        "status": "success",
        "error_message": None,
        "output_path": None,
        "segment_id": None,
        "started_at": T0,
        "finished_at": T1,
        # 单次 start→finish 步进时钟前进一格（5s），见模块 docstring
        "duration_ms": 5000,
        "retry_count": 0,
        "cost_amount": 0.0,
        "currency": "USD",
        "provider": "gemini",
        "usage_tokens": None,
        "input_tokens": None,
        "output_tokens": None,
        "image_input_tokens": None,
        "image_output_tokens": None,
        "text_input_tokens": None,
        "text_output_tokens": None,
    }
    row.update(overrides)
    return row


def _expected_pending_row(**overrides: Any) -> dict[str, Any]:
    """start_call 之后未终结的行：无 finished_at / duration_ms，费用列为 server 默认。"""
    return _expected_row(
        status="pending",
        finished_at=None,
        duration_ms=None,
        **overrides,
    )


# ---------------------------------------------------------------------------
# 假 backend（Protocol 实现）
# ---------------------------------------------------------------------------

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"


class _FakeImageBackend:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        result_fields: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._result_fields = result_fields or {}
        self._error = error

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        if self._error is not None:
            raise self._error
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(_PNG_BYTES)
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider,
            model=self._model,
            **self._result_fields,
        )


class _FakeAudioBackend:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        characters: int = 0,
        error: BaseException | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._characters = characters
        self._error = error

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH}

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        if self._error is not None:
            raise self._error
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"fake-wav")
        return AudioSynthesisResult(
            provider=self._provider,
            model=self._model,
            characters=self._characters,
            output_path=request.output_path,
        )


class _FakeVideoBackend:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        result_fields: dict[str, Any] | None = None,
        error: BaseException | None = None,
        resume_error: BaseException | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        # result_fields 里的 duration_seconds 模拟 provider 回报的实际计费时长；
        # 缺省回显请求时长（多数后端行为）。
        self._result_fields = result_fields or {}
        self._error = error
        self._resume_error = resume_error

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities()

    def _build_result(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"fake-mp4")
        fields = {
            "duration_seconds": request.duration_seconds,
            "video_uri": "fake-video-uri",
            "usage_tokens": None,
            "generate_audio": request.generate_audio,
            **self._result_fields,
        }
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self._provider,
            model=self._model,
            **fields,
        )

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if self._error is not None:
            raise self._error
        return self._build_result(request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        if self._resume_error is not None:
            raise self._resume_error
        return self._build_result(request)


class _FakeTextBackend:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._error = error

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return set()

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if self._error is not None:
            raise self._error
        return TextGenerationResult(
            text="ok",
            provider=self._provider,
            model=self._model,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )


class _FakeConfigResolver:
    """generate/resume_video_async 仅消费 video_generate_audio 这一个配置读点。"""

    def __init__(self, *, video_generate_audio: bool = True) -> None:
        self._video_generate_audio = video_generate_audio

    async def video_generate_audio(self, project_name: str | None = None) -> bool:
        return self._video_generate_audio


def _media_generator(
    tmp_path: Path,
    db: _AccountingDb,
    *,
    image_backend: _FakeImageBackend | None = None,
    video_backend: _FakeVideoBackend | None = None,
    audio_backend: _FakeAudioBackend | None = None,
    image_provider_id: str | None = None,
    video_provider_id: str | None = None,
    audio_provider_id: str | None = None,
) -> MediaGenerator:
    project_path = tmp_path / "projects" / "demo"
    project_path.mkdir(parents=True, exist_ok=True)
    # 成对不变量要求：backend 在则 provider_id 在。媒体侧真实 backend.name 与解析层 id 同值
    # （gemini-aistudio/ark/dashscope），故默认取 backend.name 保持矩阵零改动；反转锁测试显式传
    # 不同值证明记账取 provider_id 而非 name。
    gen = MediaGenerator(
        project_path,
        image_backend=image_backend,
        video_backend=video_backend,
        audio_backend=audio_backend,
        config_resolver=cast(ConfigResolver, _FakeConfigResolver()),
        image_provider_id=image_provider_id or (image_backend.name if image_backend else None),
        video_provider_id=video_provider_id or (video_backend.name if video_backend else None),
        audio_provider_id=audio_provider_id or (audio_backend.name if audio_backend else None),
    )
    # 唯一的接线替换：把记账落点指到测试内存库。记账链路内部（ledger→repo→结算→定价）全真实。
    gen.ledger = Ledger(session_factory=db.factory)
    return gen


def _output_path(tmp_path: Path, relative: str) -> str:
    return str((tmp_path / "projects" / "demo" / relative).resolve())


# ---------------------------------------------------------------------------
# 通道一：image 生成括号
# ---------------------------------------------------------------------------


class TestImageChannel:
    async def test_success_auto_billing_full_row(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="gemini",
                model="gemini-3-pro-image-preview",
                result_fields={"usage_tokens": 8},
            ),
        )

        await gen.generate_image_async(
            prompt="图" * 700, resource_type="characters", resource_id="婉儿", image_size="2K"
        )

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="image",
                model="gemini-3-pro-image-preview",
                prompt="图" * 500,
                resolution="2K",
                aspect_ratio="9:16",
                output_path=_output_path(tmp_path, "characters/婉儿.png"),
                cost_amount=pytest.approx(0.134),
                usage_tokens=8,
            ),
        )

    async def test_openai_token_sum_semantics_and_storyboard_segment(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="openai",
                model="gpt-image-2",
                result_fields={
                    "image_input_tokens": 100_000,
                    "image_output_tokens": 200_000,
                    "text_input_tokens": 50_000,
                    "text_output_tokens": 10_000,
                },
            ),
        )

        await gen.generate_image_async(prompt="p", resource_type="storyboards", resource_id="E1S01")

        # input/output_tokens 承载 image_* + text_* 的总和语义；storyboards 资源落 segment_id
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="image",
                model="gpt-image-2",
                provider="openai",
                prompt="p",
                aspect_ratio="9:16",
                segment_id="E1S01",
                output_path=_output_path(tmp_path, "storyboards/scene_E1S01.png"),
                cost_amount=pytest.approx(7.05),
                input_tokens=150_000,
                output_tokens=210_000,
                image_input_tokens=100_000,
                image_output_tokens=200_000,
                text_input_tokens=50_000,
                text_output_tokens=10_000,
            ),
        )

    async def test_failure_flips_failed_with_truncated_error(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="gemini",
                model="gemini-3-pro-image-preview",
                error=ValueError("x" * 600),
            ),
        )

        with pytest.raises(ValueError):
            await gen.generate_image_async(prompt="p", resource_type="characters", resource_id="婉儿")

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="image",
                model="gemini-3-pro-image-preview",
                prompt="p",
                aspect_ratio="9:16",
                status="failed",
                error_message="x" * 500,
            ),
        )

    async def test_cancellation_passes_through_leaving_pending(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="gemini",
                model="gemini-3-pro-image-preview",
                error=asyncio.CancelledError(),
            ),
        )

        with pytest.raises(asyncio.CancelledError):
            await gen.generate_image_async(prompt="p", resource_type="characters", resource_id="婉儿")

        # CancelledError 不经 except Exception 括号：行停在 pending，不翻 failed、不计费
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_pending_row(
                call_type="image",
                model="gemini-3-pro-image-preview",
                prompt="p",
                aspect_ratio="9:16",
            ),
        )


# ---------------------------------------------------------------------------
# 通道二：audio 生成括号
# ---------------------------------------------------------------------------


class TestAudioChannel:
    async def test_success_per_character_billing_full_row(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            audio_backend=_FakeAudioBackend(provider="dashscope", model="qwen3-tts-flash", characters=25_000),
        )

        await gen.generate_audio_async(text="念" * 700, resource_id="E1S01", voice="Cherry")

        # usage_tokens 承载合成字符数，按每万字符 0.8 CNY 计费
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="audio",
                model="qwen3-tts-flash",
                provider="dashscope",
                prompt="念" * 500,
                segment_id="E1S01",
                output_path=_output_path(tmp_path, "audio/segment_E1S01.wav"),
                cost_amount=pytest.approx(2.0),
                currency="CNY",
                usage_tokens=25_000,
            ),
        )

    async def test_failure_flips_failed(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            audio_backend=_FakeAudioBackend(
                provider="dashscope", model="qwen3-tts-flash", error=RuntimeError("tts down")
            ),
        )

        with pytest.raises(RuntimeError):
            await gen.generate_audio_async(text="t", resource_id="E1S01", voice="Cherry")

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="audio",
                model="qwen3-tts-flash",
                provider="dashscope",
                prompt="t",
                segment_id="E1S01",
                status="failed",
                error_message="tts down",
            ),
        )


# ---------------------------------------------------------------------------
# 通道三：video 生成括号
# ---------------------------------------------------------------------------


def _veo_backend(**result_fields: Any) -> _FakeVideoBackend:
    return _FakeVideoBackend(provider="gemini", model="veo-3.1-generate-preview", result_fields=result_fields)


async def _generate_video(gen: MediaGenerator) -> None:
    await gen.generate_video_async(
        prompt="p",
        resource_type="videos",
        resource_id="E1S01",
        duration_seconds="8",
        resolution="720p",
    )


def _expected_video_row(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "call_type": "video",
        "model": "veo-3.1-generate-preview",
        "prompt": "p",
        "resolution": "720p",
        "duration_seconds": 8,
        "aspect_ratio": "9:16",
        "segment_id": "E1S01",
        "output_path": _output_path(tmp_path, "videos/scene_E1S01.mp4"),
    }
    defaults.update(overrides)
    return _expected_row(**defaults)


class TestVideoChannel:
    async def test_success_per_second_matrix_billing_full_row(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0))

        await _generate_video(gen)

        # (720p, 有声) 0.40 USD/s × 8s
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(tmp_path, cost_amount=pytest.approx(3.2), usage_tokens=0),
        )

    async def test_backend_reported_audio_flag_overrides_request(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0, generate_audio=False))

        await _generate_video(gen)

        # provider 回报无声覆盖请求有声：行落 False，按 (720p, 无声) 0.20 USD/s 计费
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(tmp_path, generate_audio=False, cost_amount=pytest.approx(1.6), usage_tokens=0),
        )

    async def test_billed_duration_overrides_requested_duration(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0, duration_seconds=6))

        await _generate_video(gen)

        # 实际计费时长 6s 覆盖请求 8s，账本与自动费用同口径
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(tmp_path, duration_seconds=6, cost_amount=pytest.approx(2.4), usage_tokens=0),
        )

    async def test_billed_duration_over_limit_falls_back_to_request(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0, duration_seconds=86_401))

        await _generate_video(gen)

        # 超出 24h 上限的计费时长视同未提供，回落请求时长
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(tmp_path, duration_seconds=8, cost_amount=pytest.approx(3.2), usage_tokens=0),
        )

    async def test_ark_per_token_billing(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            video_backend=_FakeVideoBackend(
                provider="ark",
                model="doubao-seedance-1-5-pro-251215",
                result_fields={"usage_tokens": 1_000_000},
            ),
        )

        await _generate_video(gen)

        # (default, 有声) 16.00 CNY/百万 token × 1M token
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(
                tmp_path,
                model="doubao-seedance-1-5-pro-251215",
                provider="ark",
                cost_amount=pytest.approx(16.0),
                currency="CNY",
                usage_tokens=1_000_000,
            ),
        )

    async def test_failure_flips_failed_with_truncated_error(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            video_backend=_FakeVideoBackend(
                provider="gemini",
                model="veo-3.1-generate-preview",
                error=RuntimeError("v" * 600),
            ),
        )

        with pytest.raises(RuntimeError):
            await _generate_video(gen)

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(
                tmp_path,
                status="failed",
                error_message="v" * 500,
                output_path=None,
            ),
        )

    async def test_cancellation_passes_through_leaving_pending(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            video_backend=_FakeVideoBackend(
                provider="gemini",
                model="veo-3.1-generate-preview",
                error=asyncio.CancelledError(),
            ),
        )

        with pytest.raises(asyncio.CancelledError):
            await _generate_video(gen)

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_pending_row(
                call_type="video",
                model="veo-3.1-generate-preview",
                prompt="p",
                resolution="720p",
                duration_seconds=8,
                aspect_ratio="9:16",
                segment_id="E1S01",
            ),
        )


# ---------------------------------------------------------------------------
# 通道四：text 生成括号
# ---------------------------------------------------------------------------


class TestTextChannel:
    async def test_success_per_token_billing_full_row(self, acct: _AccountingDb) -> None:
        gen = TextGenerator(
            _FakeTextBackend(
                provider="gemini",
                model="gemini-3-flash-preview",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            ),
            Ledger(session_factory=acct.factory),
            "gemini-aistudio",
        )

        await gen.generate(TextGenerationRequest(prompt="文" * 700), project_name="demo")

        # 0.50 + 3.00 USD/百万 token；记账 provider 取解析层 id（gemini-aistudio），非 backend.name（gemini）
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="text",
                model="gemini-3-flash-preview",
                prompt="文" * 500,
                cost_amount=pytest.approx(3.5),
                provider="gemini-aistudio",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            ),
        )

    async def test_no_tokens_and_no_project_yields_zero_cost(self, acct: _AccountingDb) -> None:
        gen = TextGenerator(
            _FakeTextBackend(provider="gemini", model="gemini-3-flash-preview"),
            Ledger(session_factory=acct.factory),
            "gemini-aistudio",
        )

        await gen.generate(TextGenerationRequest(prompt="p"))

        # 无 token 数据无从计费（成功仍 0 费用）；project_name 缺省落空串
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="text",
                model="gemini-3-flash-preview",
                project_name="",
                prompt="p",
                provider="gemini-aistudio",
            ),
        )

    async def test_failure_flips_failed_with_truncated_error(self, acct: _AccountingDb) -> None:
        gen = TextGenerator(
            _FakeTextBackend(provider="gemini", model="gemini-3-flash-preview", error=ValueError("t" * 600)),
            Ledger(session_factory=acct.factory),
            "gemini-aistudio",
        )

        with pytest.raises(ValueError):
            await gen.generate(TextGenerationRequest(prompt="p"), project_name="demo")

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="text",
                model="gemini-3-flash-preview",
                prompt="p",
                status="failed",
                error_message="t" * 500,
                provider="gemini-aistudio",
            ),
        )

    async def test_custom_provider_priced_from_db(self, acct: _AccountingDb) -> None:
        async with acct.factory() as session:
            provider = CustomProvider(
                display_name="自定供应商",
                discovery_format="openai",
                base_url="http://localhost:9",
                api_key="k",
            )
            session.add(provider)
            await session.flush()
            session.add(
                CustomProviderModel(
                    provider_id=provider.id,
                    model_id="my-model",
                    display_name="My Model",
                    endpoint="openai-chat",
                    price_unit="token",
                    price_input=2.0,
                    price_output=4.0,
                    currency="CNY",
                )
            )
            await session.commit()
            provider_key = f"custom-{provider.id}"

        gen = TextGenerator(
            _FakeTextBackend(
                provider=provider_key,
                model="my-model",
                input_tokens=1_000_000,
                output_tokens=500_000,
            ),
            Ledger(session_factory=acct.factory),
            provider_key,
        )

        await gen.generate(TextGenerationRequest(prompt="p"), project_name="demo")

        # (1M × 2.0 + 0.5M × 4.0) / 1M = 4.0，币种取模型行声明
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="text",
                model="my-model",
                provider=provider_key,
                prompt="p",
                cost_amount=pytest.approx(4.0),
                currency="CNY",
                input_tokens=1_000_000,
                output_tokens=500_000,
            ),
        )


# ---------------------------------------------------------------------------
# 身份不变量：记账 provider 取解析层 provider_id，构造期成对不变量
# ---------------------------------------------------------------------------


class TestIdentityInvariant:
    async def test_media_records_resolver_provider_id_not_backend_name(
        self, tmp_path: Path, acct: _AccountingDb
    ) -> None:
        # backend.name 报裸 "gemini"，解析层 id 为 "gemini-aistudio"：记账须落解析层 id。
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(provider="gemini", model="gemini-3-pro-image-preview"),
            image_provider_id="gemini-aistudio",
        )

        await gen.generate_image_async(prompt="p", resource_type="characters", resource_id="婉儿", image_size="2K")

        row = await acct.fetch_only_row()
        assert row["provider"] == "gemini-aistudio"

    def test_media_backend_without_provider_id_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            MediaGenerator(
                tmp_path / "projects" / "demo",
                image_backend=_FakeImageBackend(provider="gemini", model="m"),
                image_provider_id=None,
            )

    def test_media_provider_id_without_backend_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            MediaGenerator(
                tmp_path / "projects" / "demo",
                video_provider_id="gemini-aistudio",
            )

    def test_text_missing_provider_id_raises(self, acct: _AccountingDb) -> None:
        with pytest.raises(ValueError):
            TextGenerator(
                _FakeTextBackend(provider="gemini", model="m"),
                Ledger(session_factory=acct.factory),
                cast(str, None),
            )


# ---------------------------------------------------------------------------
# 通道五：resume 补账
# ---------------------------------------------------------------------------


async def _pending_video_call(acct: _AccountingDb) -> int:
    """模拟 submit 侧已记账的 pending 行（resume 的补账锚点）。

    直接经 UsageRepository 落 pending 行 —— submit 侧生产入口是记账括号的 start，此处仅需
    锚点行，不必开括号。
    """
    async with acct.factory() as session:
        return await UsageRepository(session).start_call(
            project_name="demo",
            call_type="video",
            model="veo-3.1-generate-preview",
            prompt="p",
            resolution="720p",
            duration_seconds=8,
            aspect_ratio="9:16",
            generate_audio=True,
            provider="gemini",
            segment_id="E1S01",
        )


async def _resume_video(gen: MediaGenerator, api_call_id: int | None) -> None:
    await gen.resume_video_async(
        job_id="job-1",
        resource_type="videos",
        resource_id="E1S01",
        prompt="p",
        aspect_ratio="9:16",
        duration_seconds="8",
        resolution="720p",
        api_call_id=api_call_id,
    )


def _expected_resume_row(**overrides: Any) -> dict[str, Any]:
    # resume 补账不回写 output_path：finalize 只翻状态与费用口径字段
    defaults: dict[str, Any] = {
        "call_type": "video",
        "model": "veo-3.1-generate-preview",
        "prompt": "p",
        "resolution": "720p",
        "duration_seconds": 8,
        "aspect_ratio": "9:16",
        "segment_id": "E1S01",
        "output_path": None,
    }
    defaults.update(overrides)
    return _expected_row(**defaults)


class TestResumeChannel:
    async def test_success_finalizes_pending_with_auto_cost(self, tmp_path: Path, acct: _AccountingDb) -> None:
        call_id = await _pending_video_call(acct)
        gen = _media_generator(
            tmp_path,
            acct,
            video_backend=_veo_backend(usage_tokens=0, generate_audio=False, duration_seconds=6),
        )

        await _resume_video(gen, call_id)

        # backend 回报值覆盖请求口径：无声 + 6s → (720p, 无声) 0.20 USD/s × 6s
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_resume_row(
                duration_seconds=6,
                generate_audio=False,
                cost_amount=pytest.approx(1.2),
                usage_tokens=0,
            ),
        )

    async def test_success_clamps_billed_duration_and_keeps_request_audio(
        self, tmp_path: Path, acct: _AccountingDb
    ) -> None:
        call_id = await _pending_video_call(acct)
        gen = _media_generator(
            tmp_path,
            acct,
            video_backend=_veo_backend(usage_tokens=0, generate_audio=None, duration_seconds=86_401),
        )

        await _resume_video(gen, call_id)

        # 超限计费时长回落请求 8s；backend 未回报音频标志（None）保留行内请求值 True
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_resume_row(cost_amount=pytest.approx(3.2), usage_tokens=0),
        )

    async def test_expired_flips_pending_to_failed_without_billing(self, tmp_path: Path, acct: _AccountingDb) -> None:
        call_id = await _pending_video_call(acct)
        gen = _media_generator(
            tmp_path,
            acct,
            video_backend=_FakeVideoBackend(
                provider="gemini",
                model="veo-3.1-generate-preview",
                resume_error=ResumeExpiredError(job_id="job-1", provider="gemini"),
            ),
        )

        with pytest.raises(ResumeExpiredError):
            await _resume_video(gen, call_id)

        # 过期补账翻 failed、零费用；error_message 不落行（异常沿调用链上抛由 worker 兜底）
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_resume_row(status="failed"),
        )

    async def test_pending_guard_never_touches_terminal_row(self, tmp_path: Path, acct: _AccountingDb) -> None:
        call_id = await _pending_video_call(acct)
        # 预置一条已 success 的终态行（模拟 generate 已 finish），供 resume 幂等守卫验证。
        async with acct.factory() as session:
            await UsageRepository(session).finish_call(
                call_id,
                status="success",
                settlement=SettlementInput(usage_tokens=0, generate_audio=True, billed_duration_seconds=8),
                output_path="/already/done.mp4",
            )
        terminal_snapshot = await acct.fetch_only_row()

        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0, duration_seconds=6))
        await _resume_video(gen, call_id)

        # WHERE status='pending' 幂等守卫：已终态行整行原样（含 created_at/updated_at）
        assert await acct.fetch_only_row() == terminal_snapshot

    async def test_missing_api_call_id_leaves_row_pending(self, tmp_path: Path, acct: _AccountingDb) -> None:
        await _pending_video_call(acct)
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0))

        await _resume_video(gen, api_call_id=None)

        # 旧任务未持久化 api_call_id：不做模糊匹配补账，行保持 pending
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_pending_row(
                call_type="video",
                model="veo-3.1-generate-preview",
                prompt="p",
                resolution="720p",
                duration_seconds=8,
                aspect_ratio="9:16",
                segment_id="E1S01",
            ),
        )


# ---------------------------------------------------------------------------
# 通道六：agent 会话事后补录
# ---------------------------------------------------------------------------


def _managed_session() -> ManagedSession:
    managed = ManagedSession(session_id="s1", actor=cast(SessionActor, None), status="idle", project_name="demo")
    managed.last_user_prompt = "u" * 700
    return managed


_AGENT_USAGE = {
    "input_tokens": 400_000,
    "cache_creation_input_tokens": 100_000,
    "cache_read_input_tokens": 500_000,
    "output_tokens": 200_000,
}


class TestAgentBackfillChannel:
    @pytest.fixture
    async def manager(self, tmp_path: Path, acct: _AccountingDb) -> SessionManager:
        mgr = SessionManager(
            project_root=tmp_path,
            meta_store=SessionMetaStore(session_factory=acct.factory),
        )
        mgr.ledger = Ledger(session_factory=acct.factory)
        return mgr

    async def test_completed_turn_prefers_reported_cost(self, manager: SessionManager, acct: _AccountingDb) -> None:
        result_msg = {"model": "claude-sonnet-4", "usage": dict(_AGENT_USAGE), "total_cost_usd": 0.123}

        await manager._record_assistant_usage(_managed_session(), result_msg, "completed")

        # SDK 直报费用优先于按 token 自动计算（自动口径为 6.0 USD）；cache token 并入 input
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="text",
                model="claude-sonnet-4",
                provider="anthropic",
                prompt="u" * 500,
                cost_amount=pytest.approx(0.123),
                usage_tokens=1_200_000,
                input_tokens=1_000_000,
                output_tokens=200_000,
            ),
        )

    async def test_non_completed_turn_records_failed_but_keeps_reported_cost(
        self, manager: SessionManager, acct: _AccountingDb
    ) -> None:
        result_msg = {"model": "claude-sonnet-4", "usage": dict(_AGENT_USAGE), "total_cost_usd": 0.123}

        await manager._record_assistant_usage(_managed_session(), result_msg, "error")

        # 非 completed 终态翻 failed，但直报费用仍入账（provider 已实际扣费）
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="text",
                model="claude-sonnet-4",
                provider="anthropic",
                prompt="u" * 500,
                status="failed",
                cost_amount=pytest.approx(0.123),
                usage_tokens=1_200_000,
                input_tokens=1_000_000,
                output_tokens=200_000,
            ),
        )

    async def test_token_only_turn_auto_bills_from_anthropic_rates(
        self, manager: SessionManager, acct: _AccountingDb
    ) -> None:
        result_msg = {
            "model": "claude-sonnet-4",
            "usage": {"input_tokens": 1_000_000, "output_tokens": 200_000},
        }

        await manager._record_assistant_usage(_managed_session(), result_msg, "completed")

        # 无直报费用时按 3.00/15.00 USD 每百万 token 兜底：3.0 + 3.0
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                call_type="text",
                model="claude-sonnet-4",
                provider="anthropic",
                prompt="u" * 500,
                cost_amount=pytest.approx(6.0),
                usage_tokens=1_200_000,
                input_tokens=1_000_000,
                output_tokens=200_000,
            ),
        )

    async def test_turn_without_usage_or_cost_writes_nothing(
        self, manager: SessionManager, acct: _AccountingDb
    ) -> None:
        await manager._record_assistant_usage(_managed_session(), {"model": "claude-sonnet-4"}, "completed")

        assert await acct.fetch_rows() == []
