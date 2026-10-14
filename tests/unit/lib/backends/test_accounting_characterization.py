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
（PostgreSQL 下同样为真实时长，不分岔）。created_at / updated_at 由 ORM 列默认绑定
真实时钟、非记账语义字段，仅断言为 datetime。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from arcreel_catalog_core import CatalogEntry, Meter, PriceRow, PriceTable, TextFacts
from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.audio_backends.base import AudioCapability, AudioSynthesisRequest, AudioSynthesisResult
from lib.backends.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageRequestFacts,
)
from lib.backends.image_backends.gemini import GeminiImageBackend
from lib.backends.providers import CallPurpose
from lib.backends.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    prompt_json_fallback,
)
from lib.backends.text_generator import TextGenerator
from lib.billing.ledger import Ledger
from lib.billing.metering import Usage
from lib.billing.multipliers import set_provider_multiplier
from lib.config.resolver import ConfigResolver
from lib.db.models.api_call import ApiCall
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.generation.media_generator import MediaGenerator
from server.agent_runtime.session_actor import SessionActor
from server.agent_runtime.session_manager import ManagedSession, SessionManager
from server.agent_runtime.session_store import SessionMetaStore
from tests.catalog_support import catalog_of, install_catalog
from tests.fakes import FakeConfigResolver, write_backend_output

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
async def acct(frozen_clock: _SteppingClock, db_engine: AsyncEngine, db_factory) -> Any:
    return _AccountingDb(engine=db_engine, factory=db_factory)


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
        "generate_audio": None,
        "status": "success",
        "error_message": None,
        "error_code": None,
        "error_params": None,
        "output_path": None,
        "segment_id": None,
        "task_id": None,
        "purpose": None,
        "session_id": None,
        "inputs": None,
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
        "last_provider_response": None,
        "unpriced_reason": None,
        "cache_read_tokens": None,
        "cache_write_5m_tokens": None,
        "cache_write_1h_tokens": None,
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
        usage_from_request: Callable[[ImageGenerationRequest], Usage] | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._result_fields = result_fields or {}
        self._error = error
        self._usage_from_request = usage_from_request

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
        await write_backend_output(request.output_path, _PNG_BYTES)
        usage = self._usage_from_request(request) if self._usage_from_request is not None else None
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider,
            model=self._model,
            usage=usage,
            **self._result_fields,
        )


class _FakeAudioBackend:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        error: BaseException | None = None,
        input_tokens: int | None = None,
        audio_output_tokens: int | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._input_tokens = input_tokens
        self._audio_output_tokens = audio_output_tokens
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
        await write_backend_output(request.output_path, b"fake-wav")
        return AudioSynthesisResult(
            provider=self._provider,
            model=self._model,
            text=request.text,
            output_path=request.output_path,
            input_tokens=self._input_tokens,
            audio_output_tokens=self._audio_output_tokens,
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
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities()

    async def _build_result(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        await write_backend_output(request.output_path, b"fake-mp4")
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
        return await self._build_result(request)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        if self._resume_error is not None:
            raise self._resume_error
        return await self._build_result(request)


class _FakeTextBackend:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error: BaseException | None = None,
        **cache_tokens: int,
    ) -> None:
        self._provider = provider
        self._model = model
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._cache_tokens = cache_tokens
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
            **self._cache_tokens,
        )


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
        config_resolver=cast(ConfigResolver, FakeConfigResolver()),
        image_provider_id=image_provider_id or (image_backend.name if image_backend else None),
        video_provider_id=video_provider_id or (video_backend.name if video_backend else None),
        audio_provider_id=audio_provider_id or (audio_backend.name if audio_backend else None),
    )
    # 唯一的接线替换：把记账落点指到测试内存库。记账链路内部（ledger→repo→结算→定价）全真实。
    gen.ledger = Ledger(session_factory=db.factory)
    return gen


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
                model="gemini-3-pro-image",
                result_fields={"usage_tokens": 8},
            ),
        )

        await gen.generate_image_async(
            prompt="图" * 700, resource_type="characters", resource_id="婉儿", image_size="2K", task_id="T-1"
        )

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="generation_task",
                call_type="image",
                model="gemini-3-pro-image",
                prompt="图" * 700,
                task_id="T-1",
                resolution="2K",
                aspect_ratio="9:16",
                output_path="characters/婉儿.png",
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
                purpose="generation_task",
                call_type="image",
                model="gpt-image-2",
                provider="openai",
                prompt="p",
                aspect_ratio="9:16",
                segment_id="E1S01",
                output_path="storyboards/scene_E1S01.png",
                cost_amount=pytest.approx(7.05),
                input_tokens=150_000,
                output_tokens=210_000,
                image_input_tokens=100_000,
                image_output_tokens=200_000,
                text_input_tokens=50_000,
                text_output_tokens=10_000,
            ),
        )

    @pytest.mark.parametrize(("image_size", "expected_cost"), [("4K", 0.24), ("2K", 0.134), (None, 0.134)])
    async def test_builtin_call_priced_by_resolution_sent_in_request(
        self, tmp_path: Path, acct: _AccountingDb, image_size: str | None, expected_cost: float
    ) -> None:
        # 调用通道按请求里的 image_size 上报分辨率（与 GeminiImageBackend 同一函数），单价按它落档；
        # 没发分辨率时按价目表的默认档（1K）计
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="gemini-aistudio",
                model="gemini-3-pro-image",
                usage_from_request=lambda request: GeminiImageBackend.estimate_usage(
                    ImageRequestFacts(
                        model="gemini-3-pro-image", aspect_ratio=request.aspect_ratio, image_size=request.image_size
                    )
                ),
            ),
        )

        await gen.generate_image_async(
            prompt="p", resource_type="characters", resource_id="婉儿", image_size=image_size
        )

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="generation_task",
                call_type="image",
                model="gemini-3-pro-image",
                provider="gemini-aistudio",
                prompt="p",
                resolution=image_size,
                aspect_ratio="9:16",
                output_path="characters/婉儿.png",
                cost_amount=pytest.approx(expected_cost),
            ),
        )

    async def test_builtin_openai_tokens_priced_from_catalog(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="openai",
                model="gpt-image-2",
                usage_from_request=lambda _request: Usage(
                    quantities={
                        "uncached_input_tokens": 1_000,
                        "uncached_image_input_tokens": 2_000,
                        "image_output_tokens": 1_756,
                    },
                    dimensions={"resolution": "1024x1024", "quality": "medium"},
                ),
            ),
        )

        await gen.generate_image_async(prompt="p", resource_type="characters", resource_id="婉儿", image_size="1K")

        row = await acct.fetch_only_row()
        assert row["cost_amount"] == pytest.approx((1_000 * 5 + 2_000 * 8 + 1_756 * 30) / 1_000_000)
        assert row["unpriced_reason"] is None

    async def test_failure_flips_failed_with_truncated_error(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="gemini",
                model="gemini-3-pro-image",
                error=ValueError("x" * 600),
            ),
        )

        with pytest.raises(ValueError, match="x" * 600):
            await gen.generate_image_async(prompt="p", resource_type="characters", resource_id="婉儿")

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="generation_task",
                call_type="image",
                model="gemini-3-pro-image",
                prompt="p",
                aspect_ratio="9:16",
                status="failed",
                error_message="x" * 500,
            ),
        )

    async def test_cancellation_settles_cancelled_and_reraises(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="gemini",
                model="gemini-3-pro-image",
                error=asyncio.CancelledError(),
            ),
        )

        with pytest.raises(asyncio.CancelledError):
            await gen.generate_image_async(prompt="p", resource_type="characters", resource_id="婉儿")

        # CancelledError 结算为 cancelled（零费用）后重抛：不留 pending，也不计入失败
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="generation_task",
                call_type="image",
                model="gemini-3-pro-image",
                prompt="p",
                aspect_ratio="9:16",
                status="cancelled",
            ),
        )


# ---------------------------------------------------------------------------
# 通道二：audio 生成括号
# ---------------------------------------------------------------------------


class TestAudioChannel:
    async def test_chinese_tts_counts_each_han_character_twice(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            audio_backend=_FakeAudioBackend(provider="dashscope", model="qwen3-tts-flash"),
        )
        text = "念" * 6000 + "a" * 3000

        await gen.generate_audio_async(text=text, resource_id="E1S01", voice="Cherry", task_id="T-1")

        # 条目的字符数计量项带「汉字计 2」：6000 × 2 + 3000 = 15000 字符，按每万字符 0.8 CNY 计 1.2；
        # usage_tokens 记原文字符数
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="generation_task",
                call_type="audio",
                model="qwen3-tts-flash",
                provider="dashscope",
                prompt=text,
                task_id="T-1",
                segment_id="E1S01",
                inputs={"voice": "Cherry", "parameters": {"language_type": "Chinese"}},
                output_path="audio/segment_E1S01.wav",
                cost_amount=pytest.approx(1.2),
                currency="CNY",
                usage_tokens=9000,
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
                purpose="generation_task",
                call_type="audio",
                model="qwen3-tts-flash",
                provider="dashscope",
                prompt="t",
                segment_id="E1S01",
                inputs={"voice": "Cherry", "parameters": {"language_type": "Chinese"}},
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


def _expected_video_row(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "purpose": "generation_task",
        "call_type": "video",
        "model": "veo-3.1-generate-preview",
        "prompt": "p",
        "resolution": "720p",
        "duration_seconds": 8,
        "aspect_ratio": "9:16",
        "generate_audio": True,
        "segment_id": "E1S01",
        "output_path": "videos/scene_E1S01.mp4",
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
            _expected_video_row(cost_amount=pytest.approx(3.2), usage_tokens=0),
        )

    async def test_backend_reported_audio_flag_overrides_request(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0, generate_audio=False))

        await _generate_video(gen)

        # provider 回报无声覆盖请求有声：行落 False，按 (720p, 无声) 0.20 USD/s 计费
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(generate_audio=False, cost_amount=pytest.approx(1.6), usage_tokens=0),
        )

    async def test_billed_duration_overrides_requested_duration(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0, duration_seconds=6))

        await _generate_video(gen)

        # 实际计费时长 6s 覆盖请求 8s，账本与自动费用同口径
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(duration_seconds=6, cost_amount=pytest.approx(2.4), usage_tokens=0),
        )

    async def test_billed_duration_over_limit_falls_back_to_request(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0, duration_seconds=86_401))

        await _generate_video(gen)

        # 超出 24h 上限的计费时长视同未提供，回落请求时长
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(duration_seconds=8, cost_amount=pytest.approx(3.2), usage_tokens=0),
        )

    async def test_ark_per_token_billing(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            video_backend=_FakeVideoBackend(
                provider="ark",
                model="doubao-seedance-2-0-260128",
                result_fields={"usage_tokens": 1_000_000},
            ),
        )

        await _generate_video(gen)

        # 46.00 CNY/百万 token × 1M token
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(
                model="doubao-seedance-2-0-260128",
                provider="ark",
                cost_amount=pytest.approx(46.0),
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
                status="failed",
                error_message="v" * 500,
                output_path=None,
            ),
        )

    async def test_cancellation_settles_cancelled_and_reraises(self, tmp_path: Path, acct: _AccountingDb) -> None:
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
            _expected_video_row(status="cancelled", output_path=None),
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
            purpose=CallPurpose.SCRIPT_GENERATION,
        )

        await gen.generate(TextGenerationRequest(prompt="文" * 700), project_name="demo")

        # 0.50 + 3.00 USD/百万 token；记账 provider 取解析层 id（gemini-aistudio），非 backend.name（gemini）
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
                call_type="text",
                model="gemini-3-flash-preview",
                prompt="文" * 700,
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
            purpose=CallPurpose.SCRIPT_GENERATION,
        )

        await gen.generate(TextGenerationRequest(prompt="p"))

        # 无 token 数据无从计费（成功仍 0 费用）；project_name 缺省落空串
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
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
            purpose=CallPurpose.SCRIPT_GENERATION,
        )

        with pytest.raises(ValueError, match="t" * 600):
            await gen.generate(TextGenerationRequest(prompt="p"), project_name="demo")

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
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
            purpose=CallPurpose.SCRIPT_GENERATION,
        )

        await gen.generate(TextGenerationRequest(prompt="p"), project_name="demo")

        # (1M × 2.0 + 0.5M × 4.0) / 1M = 4.0，币种取模型行声明
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
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


class _PromptFallbackTextBackend(_FakeTextBackend):
    """结构化输出走 prompt 注入降级：按 ``replies`` 依次回复 (文本, 输入 token, 输出 token)，每次请求都已计费。"""

    def __init__(self, *, provider: str, model: str, replies: list[tuple[str, int, int]]) -> None:
        super().__init__(provider=provider, model=model)
        self._replies = replies

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        replies = iter(self._replies)

        async def generate_once(_request: TextGenerationRequest) -> TextGenerationResult:
            text, input_tokens, output_tokens = next(replies)
            return TextGenerationResult(
                text=text,
                provider=self._provider,
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        return await prompt_json_fallback(request, generate_once, provider=self._provider, model=self._model)


def _text_entry(name: str, *meters: Meter) -> CatalogEntry:
    return CatalogEntry(
        maker="acme",
        name=name,
        media_type="text",
        display_name=name,
        facts=TextFacts(),
        price=PriceTable(currency="USD", meters=meters),
    )


def _meter(meter: str, *rows: tuple[dict[str, object], float], dims: tuple[str, ...] = ()) -> Meter:
    return Meter(meter=meter, dims=dims, rows=tuple(PriceRow(when=when, price=price) for when, price in rows))


def _text_generator(acct: _AccountingDb, provider_id: str, model: str, **usage: int) -> TextGenerator:
    return TextGenerator(
        _FakeTextBackend(provider=provider_id, model=model, **usage),
        Ledger(session_factory=acct.factory),
        provider_id,
        purpose=CallPurpose.SCRIPT_GENERATION,
    )


async def _set_provider_multiplier(acct: _AccountingDb, provider_id: str, value: float) -> None:
    async with acct.factory() as session:
        await set_provider_multiplier(session, provider_id, value)
        await session.commit()


class TestCatalogPricedText:
    """文本按模型目录的价目表结算：计量项互不重叠、上下文档、未定价与两级倍率。"""

    async def test_context_tier_and_cache_meters_priced_separately(
        self, acct: _AccountingDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tiered = ("context_tier",)
        entry = _text_entry(
            "tiered",
            _meter("uncached_input_tokens", ({"context_tier": 0}, 2.0), ({"context_tier": 200_000}, 4.0), dims=tiered),
            _meter("cache_read_tokens", ({"context_tier": 0}, 0.2), ({"context_tier": 200_000}, 0.4), dims=tiered),
            _meter("cache_write_5m_tokens", ({"context_tier": 0}, 2.5), ({"context_tier": 200_000}, 5.0), dims=tiered),
            _meter("cache_write_1h_tokens", ({}, 4.0)),
            _meter("output_tokens", ({"context_tier": 0}, 10.0), ({"context_tier": 200_000}, 15.0), dims=tiered),
        )
        install_catalog(monkeypatch, catalog_of((entry,), {"openai": ("acme/tiered",)}))

        await _text_generator(
            acct,
            "openai",
            "tiered",
            input_tokens=300_000,
            output_tokens=20_000,
            cache_read_tokens=100_000,
            cache_write_5m_tokens=40_000,
            cache_write_1h_tokens=10_000,
        ).generate(TextGenerationRequest(prompt="p"), project_name="demo")

        # 总输入 30 万（含缓存读写）超过 20 万，整个请求落入第二档：未缓存输入 15 万 × 4 + 缓存读 10 万 × 0.4
        # + 5 分钟缓存写 4 万 × 5 + 1 小时缓存写 1 万 × 4（不分档）+ 输出 2 万 × 15，按每百万 token 计 = 1.18
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
                call_type="text",
                model="tiered",
                prompt="p",
                provider="openai",
                cost_amount=pytest.approx(1.18),
                input_tokens=300_000,
                output_tokens=20_000,
                cache_read_tokens=100_000,
                cache_write_5m_tokens=40_000,
                cache_write_1h_tokens=10_000,
            ),
        )

    async def test_fallback_requests_each_fall_into_their_own_context_tier(
        self, acct: _AccountingDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tiered = ("context_tier",)
        entry = _text_entry(
            "tiered",
            _meter("uncached_input_tokens", ({"context_tier": 0}, 1.0), ({"context_tier": 200_000}, 2.0), dims=tiered),
            _meter("output_tokens", ({"context_tier": 0}, 10.0), ({"context_tier": 200_000}, 20.0), dims=tiered),
        )
        install_catalog(monkeypatch, catalog_of((entry,), {"openai": ("acme/tiered",)}))
        backend = _PromptFallbackTextBackend(
            provider="openai",
            model="tiered",
            replies=[("不是 JSON", 150_000, 1_000), ('{"ok": true}', 150_000, 1_000)],
        )

        await TextGenerator(
            backend, Ledger(session_factory=acct.factory), "openai", purpose=CallPurpose.SCRIPT_GENERATION
        ).generate(TextGenerationRequest(prompt="p", response_schema={"type": "object"}), project_name="demo")

        # 两次请求各 15 万输入，合计 30 万超过 20 万，但每次单独看都在首档：
        # 2 × (15 万 × 1 + 1000 × 10) / 百万 = 0.32；按合计落档会是 0.64
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
                call_type="text",
                model="tiered",
                prompt="p",
                provider="openai",
                cost_amount=pytest.approx(0.32),
                input_tokens=300_000,
                output_tokens=2_000,
            ),
        )

    async def test_usage_on_meter_missing_from_price_table_is_unpriced(
        self, acct: _AccountingDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry = _text_entry(
            "no-cache-price", _meter("uncached_input_tokens", ({}, 1.0)), _meter("output_tokens", ({}, 5.0))
        )
        install_catalog(monkeypatch, catalog_of((entry,), {"openai": ("acme/no-cache-price",)}))

        await _text_generator(
            acct, "openai", "no-cache-price", input_tokens=10_000, output_tokens=1_000, cache_read_tokens=8_000
        ).generate(TextGenerationRequest(prompt="p"), project_name="demo")

        # 供应商回报了缓存命中，价目表却没有缓存读价：这部分查不到单价，整笔记为未定价，不按 0 计
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
                call_type="text",
                model="no-cache-price",
                prompt="p",
                provider="openai",
                cost_amount=None,
                unpriced_reason="missing_rate",
                input_tokens=10_000,
                output_tokens=1_000,
                cache_read_tokens=8_000,
            ),
        )

    async def test_missing_rate_without_default_is_unpriced(
        self, acct: _AccountingDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry = _text_entry(
            "tiered-only",
            _meter("uncached_input_tokens", ({}, 1.0)),
            _meter("output_tokens", ({"service_tier": "flex"}, 5.0), dims=("service_tier",)),
        )
        install_catalog(monkeypatch, catalog_of((entry,), {"openai": ("acme/tiered-only",)}))

        await _text_generator(acct, "openai", "tiered-only", input_tokens=1_000, output_tokens=1_000).generate(
            TextGenerationRequest(prompt="p"), project_name="demo"
        )

        # 请求没有发送服务档、计量项也没有默认值：不回落到 flex 档，记为未定价并带原因
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
                call_type="text",
                model="tiered-only",
                prompt="p",
                provider="openai",
                cost_amount=None,
                unpriced_reason="missing_rate",
                input_tokens=1_000,
                output_tokens=1_000,
            ),
        )

    async def test_provider_and_model_multipliers_multiply(
        self, acct: _AccountingDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry = _text_entry("flat", _meter("uncached_input_tokens", ({}, 2.0)), _meter("output_tokens", ({}, 8.0)))
        data = catalog_of((entry,), {"openai": ("acme/flat",)})
        listing = data.builtin_providers["openai"]
        discounted = replace(listing, models=(replace(listing.models[0], multiplier=0.8),))
        install_catalog(monkeypatch, replace(data, builtin_providers={"openai": discounted}))
        await _set_provider_multiplier(acct, "openai", 0.5)

        await _text_generator(acct, "openai", "flat", input_tokens=1_000_000, output_tokens=1_000_000).generate(
            TextGenerationRequest(prompt="p"), project_name="demo"
        )

        # (2 + 8) USD × 供应商倍率 0.5 × 供应商模型倍率 0.8 = 4.0
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
                call_type="text",
                model="flat",
                prompt="p",
                provider="openai",
                cost_amount=pytest.approx(4.0),
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            ),
        )

    async def test_zero_multiplier_records_zero_without_pricing(
        self, acct: _AccountingDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 条目没有价目表：倍率不为 0 时会是未定价；ark-agent-plan 的供应商倍率默认 0，直接记 0
        entry = replace(_text_entry("subscription"), price=None)
        install_catalog(monkeypatch, catalog_of((entry,), {"ark-agent-plan": ("acme/subscription",)}))

        await _text_generator(acct, "ark-agent-plan", "subscription", input_tokens=1_000, output_tokens=1_000).generate(
            TextGenerationRequest(prompt="p"), project_name="demo"
        )

        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
                call_type="text",
                model="subscription",
                prompt="p",
                provider="ark-agent-plan",
                cost_amount=0.0,
                input_tokens=1_000,
                output_tokens=1_000,
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
            image_backend=_FakeImageBackend(provider="gemini", model="gemini-3-pro-image"),
            image_provider_id="gemini-aistudio",
        )

        await gen.generate_image_async(prompt="p", resource_type="characters", resource_id="婉儿", image_size="2K")

        row = await acct.fetch_only_row()
        assert row["provider"] == "gemini-aistudio"

    def test_media_backend_without_provider_id_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=r"image backend 与 provider_id 必须成对提供"):
            MediaGenerator(
                tmp_path / "projects" / "demo",
                image_backend=_FakeImageBackend(provider="gemini", model="m"),
                image_provider_id=None,
            )

    def test_media_provider_id_without_backend_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=r"video backend 与 provider_id 必须成对提供"):
            MediaGenerator(
                tmp_path / "projects" / "demo",
                video_provider_id="gemini-aistudio",
            )

    def test_text_missing_provider_id_raises(self, acct: _AccountingDb) -> None:
        with pytest.raises(ValueError, match=r"text backend 与 provider_id 必须成对提供"):
            TextGenerator(
                _FakeTextBackend(provider="gemini", model="m"),
                Ledger(session_factory=acct.factory),
                cast(str, None),
                purpose=CallPurpose.SCRIPT_GENERATION,
            )


# ---------------------------------------------------------------------------
# 通道五：resume 补账
# ---------------------------------------------------------------------------


_RESUME_TASK_ID = "T-resume"


async def _pending_video_call(acct: _AccountingDb, *, task_id: str | None = _RESUME_TASK_ID) -> int:
    """模拟 submit 侧已记账的 pending 行（resume 的补账锚点）。

    直接经 UsageRepository 落 pending 行 —— submit 侧生产入口是记账括号的 start，此处仅需
    锚点行，不必开括号。``task_id`` 是 resume 反查这条行的唯一凭据。
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
            task_id=task_id,
            purpose=CallPurpose.GENERATION_TASK,
        )


async def _resume_video(gen: MediaGenerator, task_id: str | None = _RESUME_TASK_ID) -> None:
    await gen.resume_video_async(
        job_id="job-1",
        resource_type="videos",
        resource_id="E1S01",
        prompt="p",
        aspect_ratio="9:16",
        duration_seconds="8",
        resolution="720p",
        task_id=task_id,
    )


def _expected_resume_row(**overrides: Any) -> dict[str, Any]:
    # resume 补账不回写 output_path：finalize 只翻状态与费用口径字段
    defaults: dict[str, Any] = {
        "purpose": "generation_task",
        "task_id": _RESUME_TASK_ID,
        "call_type": "video",
        "model": "veo-3.1-generate-preview",
        "prompt": "p",
        "resolution": "720p",
        "duration_seconds": 8,
        "aspect_ratio": "9:16",
        "generate_audio": True,
        "segment_id": "E1S01",
        "output_path": None,
    }
    defaults.update(overrides)
    return _expected_row(**defaults)


class TestResumeChannel:
    async def test_success_finalizes_pending_with_auto_cost(self, tmp_path: Path, acct: _AccountingDb) -> None:
        await _pending_video_call(acct)
        gen = _media_generator(
            tmp_path,
            acct,
            video_backend=_veo_backend(usage_tokens=0, generate_audio=False, duration_seconds=6),
        )

        await _resume_video(gen)

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
        await _pending_video_call(acct)
        gen = _media_generator(
            tmp_path,
            acct,
            video_backend=_veo_backend(usage_tokens=0, generate_audio=None, duration_seconds=86_401),
        )

        await _resume_video(gen)

        # 超限计费时长回落请求 8s；backend 未回报音频标志（None）保留行内请求值 True
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_resume_row(cost_amount=pytest.approx(3.2), usage_tokens=0),
        )

    async def test_expired_flips_pending_to_failed_without_billing(self, tmp_path: Path, acct: _AccountingDb) -> None:
        await _pending_video_call(acct)
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
            await _resume_video(gen)

        # 过期补账翻 failed、零费用；过期异常的原文随补账落行，分类不认得这一类故无码
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_resume_row(
                status="failed",
                error_message="resume job job-1 expired or not found on provider gemini",
            ),
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
        await _resume_video(gen)

        # WHERE status='pending' 幂等守卫：已终态行整行原样（含 created_at/updated_at）
        assert await acct.fetch_only_row() == terminal_snapshot

    async def test_unlinked_call_row_leaves_row_pending(self, tmp_path: Path, acct: _AccountingDb) -> None:
        await _pending_video_call(acct, task_id=None)
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0))

        await _resume_video(gen)

        # 历史行没有 task_id 可反查：不做模糊匹配补账，行保持 pending
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_pending_row(
                purpose="generation_task",
                call_type="video",
                model="veo-3.1-generate-preview",
                prompt="p",
                resolution="720p",
                duration_seconds=8,
                aspect_ratio="9:16",
                generate_audio=True,
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
                purpose="assistant_session",
                session_id="s1",
                call_type="text",
                model="claude-sonnet-4",
                provider="anthropic",
                prompt="u" * 700,
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
                purpose="assistant_session",
                session_id="s1",
                call_type="text",
                model="claude-sonnet-4",
                provider="anthropic",
                prompt="u" * 700,
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
                purpose="assistant_session",
                session_id="s1",
                call_type="text",
                model="claude-sonnet-4",
                provider="anthropic",
                prompt="u" * 700,
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
