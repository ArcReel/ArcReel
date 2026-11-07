"""Accounting characterization matrix pins five-channel persisted behavior.

Drive public generators through Protocol fake backends with controlled results/errors, real
in-memory SQLite, and frozen clocks. Pin every ApiCall column across Ledger → UsageRepository →
settlement → catalog pricing, without internal mocks, immediately detecting accidental persisted changes.

Five channels (Agent-turn accounting: ``tests/integration/server/agent_runtime/test_session_manager_usage_billing.py``):
- Image/audio/video/text brackets through public MediaGenerator/TextGenerator methods;
- Resume settlement through MediaGenerator.resume_video_async: success, expired failure, and pending-idempotency guards.

Replace usage_repo utc_now with five-second step clocks, exactly asserting started_at/finished_at.
SQLite reads timezone=True DateTime as naive; cross-session finish/finalize restores UTC tzinfo
before subtracting aware finished_at. Persist actual duration_ms, 5000ms for one start/finish step,
matching PostgreSQL behavior. ORM created_at/updated_at defaults use real clocks outside accounting
semantics; assert datetime types only.
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

from arcreel_catalog_core import CatalogEntry, ImageFacts, Meter, PriceRow, PriceTable, TextFacts, VideoFacts
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
    per_image_usage,
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
from lib.backends.video_backends.gemini import GeminiVideoBackend
from lib.billing.ledger import Ledger
from lib.billing.metering import Usage
from lib.billing.multipliers import set_provider_multiplier
from lib.config.resolver import ConfigResolver
from lib.db.models.api_call import ApiCall
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.usage_repo import SettlementInput, UsageRepository
from lib.generation.media_generator import MediaGenerator
from tests.catalog_support import catalog_of, install_catalog
from tests.fakes import FakeConfigResolver, write_backend_output, write_generation_targets

# ---------------------------------------------------------------------------
# Frozen write clocks are aware usage_repo.utc_now; SQLite reads naive datetime.
# ---------------------------------------------------------------------------

_T0_AWARE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_CLOCK_STEP = timedelta(seconds=5)
# Naive ApiCall snapshots: T0=start, T1=immediately following finish/finalize.
T0 = _T0_AWARE.replace(tzinfo=None)
T1 = T0 + _CLOCK_STEP


class _SteppingClock:
    """Deterministic clocks advance fixed steps per read."""

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
# Real in-memory SQLite accounting destination
# ---------------------------------------------------------------------------


@dataclass
class _AccountingDb:
    engine: AsyncEngine
    factory: async_sessionmaker

    async def fetch_rows(self) -> list[dict[str, Any]]:
        """Read all ApiCall rows by ascending ID as complete table-column snapshots.

        New columns automatically change snapshot dictionaries, intentionally forcing conscious
        matrix updates alongside schema evolution.
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
    """Assert complete rows: created_at/updated_at types only, every other column exactly."""
    actual = dict(actual)
    for column in ("created_at", "updated_at"):
        value = actual.pop(column)
        assert isinstance(value, datetime), f"{column} 应为 datetime"
    assert actual == expected


def _expected_row(**overrides: Any) -> dict[str, Any]:
    """Full ApiCall expected defaults: success, zero cost, default user, empty token columns."""
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
        # One start/finish advances five seconds; see module docstring.
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
    """Unfinished start_call rows lack finished_at/duration_ms and retain server-default costs."""
    return _expected_row(
        status="pending",
        finished_at=None,
        duration_ms=None,
        **overrides,
    )


# ---------------------------------------------------------------------------
# Protocol fake backends
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
        metered: Callable[[VideoGenerationRequest, VideoGenerationResult | None], Usage] | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        # result_fields duration_seconds simulates provider-reported actual billed duration;
        # default echoes requested durations, as most backends do.
        self._result_fields = result_fields or {}
        self._error = error
        self._resume_error = resume_error
        # Built-ins report ``metered_usage`` contracts; omission simulates channels without declarations.
        if metered is not None:
            self.metered_usage = lambda _model, request, result=None, *, entry=None: metered(request, result)

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
    # Paired invariants require provider_id with backends. Real media names match resolved IDs
    # (gemini-aistudio/ark/dashscope), so default backend.name keeps matrix unchanged; inversion tests
    # explicitly differ them to prove accounting uses provider_id, not names.
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
    # Only replace accounting destination with test DBs; ledger/repository/settlement/pricing stay real.
    gen.ledger = Ledger(session_factory=db.factory)
    return gen


# ---------------------------------------------------------------------------
# Channel 1: image brackets
# ---------------------------------------------------------------------------


class TestImageChannel:
    async def test_success_auto_billing_full_row(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="gemini-aistudio",
                model="gemini-3-pro-image",
                result_fields={"usage_tokens": 8},
                usage_from_request=lambda request: GeminiImageBackend.estimate_usage(
                    ImageRequestFacts(
                        model="gemini-3-pro-image", aspect_ratio=request.aspect_ratio, image_size=request.image_size
                    )
                ),
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
                provider="gemini-aistudio",
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
                usage_from_request=lambda _request: Usage(
                    quantities={
                        "uncached_input_tokens": 50_000,
                        "uncached_image_input_tokens": 100_000,
                        "image_output_tokens": 200_000,
                        "output_tokens": 10_000,
                    },
                    dimensions={"resolution": "1024x1536", "quality": "medium"},
                ),
            ),
        )

        await gen.generate_image_async(prompt="p", resource_type="storyboards", resource_id="E1S01")

        # input/output_tokens sum image_* + text_*; storyboard resources use segment_id.
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
        # Report actual image_size resolution using GeminiImageBackend’s helper; pricing follows it,
        # or default 1K when no resolution was sent.
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

    async def test_reference_images_absent_from_per_image_price_table_are_not_billed(
        self, tmp_path: Path, acct: _AccountingDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry = CatalogEntry(
            maker="acme",
            name="pic",
            media_type="image",
            display_name="pic",
            facts=ImageFacts(text_to_image=True, image_to_image=True),
            price=PriceTable(currency="USD", meters=(_meter("output_images", ({}, 0.04)),)),
        )
        install_catalog(monkeypatch, catalog_of((entry,), {"openai": ("acme/pic",)}))
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="openai",
                model="pic",
                usage_from_request=lambda _request: per_image_usage(output_images=1, input_images=3),
            ),
        )

        await gen.generate_image_async(prompt="p", resource_type="characters", resource_id="婉儿")

        # I2I reports reference counts, but absent catalog reference prices mean no provider charges; bill output counts only.
        row = await acct.fetch_only_row()
        assert row["cost_amount"] == pytest.approx(0.04)
        assert row["unpriced_reason"] is None

    async def test_openai_without_usage_is_unpriced(self, tmp_path: Path, acct: _AccountingDb) -> None:
        # Missing OpenAI usage reports output counts only through _save_and_return; gpt-image-2
        # prices tokens, so no metered quantities means unpriced, never accounting from estimated tokens.
        gen = _media_generator(
            tmp_path,
            acct,
            image_backend=_FakeImageBackend(
                provider="openai",
                model="gpt-image-2",
                usage_from_request=lambda _request: per_image_usage(
                    output_images=1, input_images=None, dimensions={"resolution": "1024x1024", "quality": "medium"}
                ),
            ),
        )

        await gen.generate_image_async(prompt="p", resource_type="characters", resource_id="婉儿", image_size="1K")

        row = await acct.fetch_only_row()
        assert row["cost_amount"] is None
        assert row["unpriced_reason"] == "missing_rate"

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

        # CancelledError settles cancelled/zero then reraises, leaving neither pending nor failed charges.
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
# Channel 2: audio brackets
# ---------------------------------------------------------------------------


class TestAudioChannel:
    async def test_chinese_tts_counts_each_han_character_twice(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            audio_backend=_FakeAudioBackend(provider="dashscope", model="qwen3-tts-flash"),
        )
        text = "念" * 6000 + "a" * 3000
        write_generation_targets(gen.project_path, segments=("E1S01",))

        await gen.generate_audio_async(text=text, resource_id="E1S01", voice="Cherry", task_id="T-1")

        # Catalog character meters double Han: 6000×2+3000=15000 characters; 0.8 CNY/10000 yields 1.2;
        # usage_tokens records original character counts.
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
# Channel 3: video brackets
# ---------------------------------------------------------------------------


def _veo_backend(**result_fields: Any) -> _FakeVideoBackend:
    """AI Studio Veo 3.1 follows GeminiVideoBackend usage contracts; price output seconds by resolution/audio."""
    return _FakeVideoBackend(
        provider="gemini-aistudio",
        model="veo-3.1-generate-preview",
        result_fields=result_fields,
        metered=lambda request, result: GeminiVideoBackend.metered_usage("veo-3.1-generate-preview", request, result),
    )


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

        # Audible 720p 0.40 USD/s×8s.
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(provider="gemini-aistudio", cost_amount=pytest.approx(3.2), usage_tokens=0),
        )

    async def test_backend_reported_audio_flag_overrides_request(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0, generate_audio=False))

        await _generate_video(gen)

        # Provider silence overrides requested audio: persist False and bill silent 720p 0.20 USD/s.
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(
                provider="gemini-aistudio", generate_audio=False, cost_amount=pytest.approx(1.6), usage_tokens=0
            ),
        )

    async def test_billed_duration_overrides_requested_duration(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0, duration_seconds=6))

        await _generate_video(gen)

        # Actual billed 6s overrides requested 8s, shared by ledger/automatic costs.
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(
                provider="gemini-aistudio", duration_seconds=6, cost_amount=pytest.approx(2.4), usage_tokens=0
            ),
        )

    async def test_billed_duration_over_limit_falls_back_to_request(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0, duration_seconds=86_401))

        await _generate_video(gen)

        # Billed duration above 24h falls back to requested duration.
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(
                provider="gemini-aistudio", duration_seconds=8, cost_amount=pytest.approx(3.2), usage_tokens=0
            ),
        )

    async def test_ark_per_token_billing(self, tmp_path: Path, acct: _AccountingDb) -> None:
        gen = _media_generator(
            tmp_path,
            acct,
            video_backend=_FakeVideoBackend(
                provider="ark",
                model="doubao-seedance-2-0-260128",
                result_fields={"usage_tokens": 1_000_000},
                metered=lambda request, result: Usage(
                    quantities={"video_tokens": result.usage_tokens if result is not None else 0},
                    dimensions={"resolution": request.resolution, "input_video": False},
                ),
            ),
        )

        await _generate_video(gen)

        # 46.00 CNY/million tokens × 1M tokens.
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


def _clip_entry() -> CatalogEntry:
    """Test video entries tier output seconds by resolution/audio, charge input-video seconds, and allow two free references per request."""
    return CatalogEntry(
        maker="acme",
        name="clip",
        media_type="video",
        display_name="clip",
        facts=VideoFacts(durations=(8,), resolutions=("720p", "1080p")),
        price=PriceTable(
            currency="USD",
            meters=(
                _meter(
                    "output_seconds",
                    ({"resolution": "720p", "audio": True}, 0.4),
                    ({"resolution": "720p", "audio": False}, 0.2),
                    ({"resolution": "1080p", "audio": True}, 0.6),
                    ({"resolution": "1080p", "audio": False}, 0.3),
                    dims=("resolution", "audio"),
                ),
                _meter("input_video_seconds", ({}, 0.1)),
                replace(_meter("input_images", ({}, 0.05)), free_allowance=2),
            ),
        ),
    )


def _clip_usage(request: VideoGenerationRequest, result: VideoGenerationResult | None) -> Usage:
    """Simulate built-in metering: output seconds/audio from results, four input-video seconds/five references, unpriced call-count meters."""
    assert result is not None
    return Usage(
        quantities={
            "output_seconds": result.duration_seconds or request.duration_seconds,
            "input_video_seconds": 4,
            "input_images": 5,
            "requests": 1,
        },
        dimensions={"resolution": request.resolution, "audio": result.generate_audio, "input_video": True},
    )


class TestCatalogPricedVideo:
    """Video catalog settlement covers reported meters/selections, free allowances, and count-based units."""

    @pytest.mark.parametrize(
        ("resolution", "audio", "per_second"),
        [("720p", True, 0.4), ("720p", False, 0.2), ("1080p", True, 0.6), ("1080p", False, 0.3)],
    )
    async def test_resolution_audio_matrix_with_input_video_and_extra_reference_images(
        self,
        tmp_path: Path,
        acct: _AccountingDb,
        monkeypatch: pytest.MonkeyPatch,
        resolution: str,
        audio: bool,
        per_second: float,
    ) -> None:
        install_catalog(monkeypatch, catalog_of((_clip_entry(),), {"openai": ("acme/clip",)}))
        backend = _FakeVideoBackend(
            provider="openai",
            model="clip",
            result_fields={"generate_audio": audio},
            metered=_clip_usage,
        )
        gen = _media_generator(tmp_path, acct, video_backend=backend)

        await gen.generate_video_async(
            prompt="p", resource_type="videos", resource_id="E1S01", duration_seconds="8", resolution=resolution
        )

        # 8 seconds×tier price + 4 input seconds×0.1 + 3 references beyond 2 free×0.05; unlisted call counts are free.
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(
                model="clip",
                provider="openai",
                resolution=resolution,
                generate_audio=audio,
                cost_amount=pytest.approx(8 * per_second + 4 * 0.1 + 3 * 0.05),
            ),
        )

    async def test_channel_without_metered_usage_is_unpriced(
        self, tmp_path: Path, acct: _AccountingDb, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_catalog(monkeypatch, catalog_of((_clip_entry(),), {"openai": ("acme/clip",)}))
        gen = _media_generator(tmp_path, acct, video_backend=_FakeVideoBackend(provider="openai", model="clip"))

        await _generate_video(gen)

        # Missing built-in metering leaves every priced quantity absent; mark unpriced, never infer from seconds.
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_video_row(model="clip", provider="openai", cost_amount=None, unpriced_reason="missing_rate"),
        )


# ---------------------------------------------------------------------------
# Channel 4: text brackets
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

        # 0.50+3.00 USD/million; provider comes from resolved gemini-aistudio, never backend.name gemini.
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

    async def test_no_tokens_and_no_project_is_unpriced(self, acct: _AccountingDb) -> None:
        gen = TextGenerator(
            _FakeTextBackend(provider="gemini", model="gemini-3-flash-preview"),
            Ledger(session_factory=acct.factory),
            "gemini-aistudio",
            purpose=CallPurpose.SCRIPT_GENERATION,
        )

        await gen.generate(TextGenerationRequest(prompt="p"))

        # No metered quantities means unknown costs/unpriced, never silent 0; absent project_name becomes empty.
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
                call_type="text",
                model="gemini-3-flash-preview",
                project_name="",
                prompt="p",
                provider="gemini-aistudio",
                cost_amount=None,
                unpriced_reason="missing_rate",
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

    async def _seed_custom_model(self, acct: _AccountingDb, model_id: str, *, multiplier: float = 1.0) -> str:
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
                    model_id=model_id,
                    display_name="My Model",
                    endpoint="openai-chat",
                    multiplier=multiplier,
                )
            )
            await session.commit()
            return f"custom-{provider.id}"

    async def _generate(self, acct: _AccountingDb, provider_key: str, model_id: str) -> None:
        gen = TextGenerator(
            _FakeTextBackend(provider=provider_key, model=model_id, input_tokens=200_000, output_tokens=100_000),
            Ledger(session_factory=acct.factory),
            provider_key,
            purpose=CallPurpose.SCRIPT_GENERATION,
        )
        await gen.generate(TextGenerationRequest(prompt="p"), project_name="demo")

    async def test_custom_provider_priced_from_the_matched_entry(self, acct: _AccountingDb) -> None:
        provider_key = await self._seed_custom_model(acct, "relay/GPT-5.4", multiplier=0.5)

        await self._generate(acct, provider_key, "relay/GPT-5.4")

        # gpt-5.4 name match: (0.2M×2.5+0.1M×15)/1M=2.0 USD, then model multiplier 0.5.
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_row(
                purpose="script_generation",
                call_type="text",
                model="relay/GPT-5.4",
                provider=provider_key,
                prompt="p",
                cost_amount=pytest.approx(1.0),
                currency="USD",
                input_tokens=200_000,
                output_tokens=100_000,
            ),
        )

    async def test_custom_model_without_an_entry_is_unpriced(self, acct: _AccountingDb) -> None:
        provider_key = await self._seed_custom_model(acct, "my-private-llm")

        await self._generate(acct, provider_key, "my-private-llm")

        row = await acct.fetch_only_row()
        assert (row["cost_amount"], row["unpriced_reason"]) == (None, "no_entry")


class _PromptFallbackTextBackend(_FakeTextBackend):
    """Prompt-injection structured fallback responds from ``replies`` as (text,input tokens,output tokens); every request is paid."""

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
    """Text catalog settlement covers disjoint meters, context tiers, unpriced results, and two-level multipliers."""

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

        # 300000 total inputs including cache exceed 200000, tiering the whole request: 150000 uncached×4+100000 cache reads×0.4
        # + 40000 five-minute cache writes×5+10000 one-hour writes×4, untiered,+20000 outputs×15; per million=1.18.
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

        # Two requests each 150000 inputs total 300000, above 200000, but each individually uses tier 1:
        # 2×(150000×1+1000×10)/million=0.32; aggregate tiering would be 0.64.
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

        # Provider cache hits without catalog cache-read prices make the whole call unpriced, never zero.
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

        # Unsent service tiers without meter defaults never fall back to flex; mark unpriced with reasons.
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

        # (2+8) USD×provider 0.5×provider-model 0.8=4.0.
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
        # No price table normally means unpriced with nonzero multipliers; ark-agent-plan defaults provider multiplier 0 and directly records 0.
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
# Identity invariant: accounting uses resolver provider_id, paired at construction.
# ---------------------------------------------------------------------------


class TestIdentityInvariant:
    async def test_media_records_resolver_provider_id_not_backend_name(
        self, tmp_path: Path, acct: _AccountingDb
    ) -> None:
        # backend.name gemini differs from resolver gemini-aistudio; persist resolver IDs.
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
# Channel 5: resume settlement
# ---------------------------------------------------------------------------


_RESUME_TASK_ID = "T-resume"


async def _pending_video_call(acct: _AccountingDb, *, task_id: str | None = _RESUME_TASK_ID) -> int:
    """Seed submit-side pending accounting rows as resume anchors.

    Persist directly through UsageRepository; production submit starts accounting brackets, but
    these tests need only rows. ``task_id`` alone identifies resume lookups.
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
            provider="gemini-aistudio",
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
    # Resume finalize changes states/cost fields only, never output_path.
    defaults: dict[str, Any] = {
        "purpose": "generation_task",
        "provider": "gemini-aistudio",
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

        # Backend results override requests: silent 6s → silent 720p 0.20 USD/s×6s.
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

        # Excessive billed duration falls back to 8s; absent audio flags None preserve requested True.
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

        # Expiry settles failed/zero; persist original expiry text, without codes for unrecognized classifications.
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_resume_row(
                status="failed",
                error_message="resume job job-1 expired or not found on provider gemini",
            ),
        )

    async def test_pending_guard_never_touches_terminal_row(self, tmp_path: Path, acct: _AccountingDb) -> None:
        call_id = await _pending_video_call(acct)
        # Seed already-successful terminal rows, simulating finished generate, to verify resume idempotency.
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

        # WHERE status='pending' guards preserve whole terminal rows, including created_at/updated_at.
        assert await acct.fetch_only_row() == terminal_snapshot

    async def test_unlinked_call_row_leaves_row_pending(self, tmp_path: Path, acct: _AccountingDb) -> None:
        await _pending_video_call(acct, task_id=None)
        gen = _media_generator(tmp_path, acct, video_backend=_veo_backend(usage_tokens=0))

        await _resume_video(gen)

        # Legacy missing task_id never uses fuzzy settlement; rows stay pending.
        _assert_full_row(
            await acct.fetch_only_row(),
            _expected_pending_row(
                purpose="generation_task",
                provider="gemini-aistudio",
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
