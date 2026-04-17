from datetime import datetime

import pytest

from scripts.verify_reference_video_sdks import Provider, RunResult, parse_args, render_report


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
