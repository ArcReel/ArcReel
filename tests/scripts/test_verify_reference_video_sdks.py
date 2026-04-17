from datetime import datetime
from pathlib import Path

import pytest

from lib.video_backends.base import (
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from scripts.verify_reference_video_sdks import Provider, RunResult, parse_args, render_report, run_once


def test_parse_args_provider_required():
    with pytest.raises(SystemExit):
        parse_args([])


def test_parse_args_provider_accepts_all_four():
    for name in ("ark", "grok", "veo", "sora"):
        args = parse_args(["--provider", name])
        assert args.provider == Provider(name)


def test_parse_args_rejects_unknown_provider():
    with pytest.raises(SystemExit):
        parse_args(["--provider", "unknown"])


def test_parse_args_defaults():
    args = parse_args(["--provider", "ark"])
    assert args.refs == 3
    assert args.duration == 5
    assert args.multi_shot is False
    assert args.report_dir.name == "verification-reports"


def test_parse_args_override():
    args = parse_args(
        [
            "--provider",
            "grok",
            "--refs",
            "7",
            "--duration",
            "10",
            "--multi-shot",
        ]
    )
    assert args.refs == 7
    assert args.duration == 10
    assert args.multi_shot is True


def _ok(provider: Provider, refs: int, duration: int, note: str = "") -> RunResult:
    return RunResult(
        provider=provider,
        model="test-model",
        refs=refs,
        duration=duration,
        multi_shot=False,
        success=True,
        elapsed_sec=12.3,
        request_bytes=1024,
        error=None,
        video_path=None,
        note=note,
    )


def _fail(provider: Provider, error: str) -> RunResult:
    return RunResult(
        provider=provider,
        model="test-model",
        refs=3,
        duration=5,
        multi_shot=False,
        success=False,
        elapsed_sec=0.0,
        request_bytes=0,
        error=error,
        video_path=None,
        note="",
    )


def test_render_report_contains_header_and_rows():
    results = [_ok(Provider.ARK, 9, 10, note="fast mode"), _fail(Provider.SORA, "422 Unprocessable")]
    md = render_report(results, generated_at=datetime(2026, 4, 20, 12, 0))

    assert "# Reference-to-Video SDK 验证报告" in md
    assert "2026-04-20" in md
    # Table header
    assert "| Provider | Model | Refs | Duration | Multi-shot | Result | Elapsed | Bytes | Note |" in md
    # Rows
    assert "| ark |" in md
    assert "| sora |" in md
    assert "FAIL" in md
    assert "422 Unprocessable" in md


def test_render_report_empty_results_still_emits_header():
    md = render_report([], generated_at=datetime(2026, 4, 20, 12, 0))
    assert "# Reference-to-Video SDK 验证报告" in md
    assert "_no results_" in md


class _FakeBackend:
    name = "fake"
    model = "fake-v1"
    capabilities = {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}
    video_capabilities = VideoCapabilities(reference_images=True, max_reference_images=9)
    _calls: list[VideoGenerationRequest] = []

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        type(self)._calls.append(request)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self.name,
            model=self.model,
            duration_seconds=request.duration_seconds,
        )


class _FailBackend(_FakeBackend):
    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        raise RuntimeError("boom: payload too large")


@pytest.mark.asyncio
async def test_run_once_success(tmp_path: Path):
    _FakeBackend._calls.clear()
    result = await run_once(
        provider=Provider.ARK,
        backend=_FakeBackend(),
        refs=3,
        duration=5,
        multi_shot=False,
        work_dir=tmp_path,
    )
    assert isinstance(result, RunResult)
    assert result.success is True
    assert result.refs == 3
    assert result.duration == 5
    assert result.error is None
    # Fake backend 收到 3 张 ref
    req = _FakeBackend._calls[-1]
    assert req.reference_images is not None
    assert len(req.reference_images) == 3


@pytest.mark.asyncio
async def test_run_once_multi_shot_prompt(tmp_path: Path):
    _FakeBackend._calls.clear()
    await run_once(
        provider=Provider.ARK,
        backend=_FakeBackend(),
        refs=2,
        duration=8,
        multi_shot=True,
        work_dir=tmp_path,
    )
    req = _FakeBackend._calls[-1]
    assert "Shot 1" in req.prompt
    assert "Shot 2" in req.prompt


@pytest.mark.asyncio
async def test_run_once_failure_captures_error(tmp_path: Path):
    result = await run_once(
        provider=Provider.ARK,
        backend=_FailBackend(),
        refs=3,
        duration=5,
        multi_shot=False,
        work_dir=tmp_path,
    )
    assert result.success is False
    assert "payload too large" in (result.error or "")
