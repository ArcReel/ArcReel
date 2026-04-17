from datetime import datetime
from pathlib import Path

import pytest

import scripts.verify_reference_video_sdks as mod
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


@pytest.mark.parametrize("bad", ["0", "-1", "-9", "abc"])
def test_parse_args_rejects_non_positive_refs(bad: str):
    with pytest.raises(SystemExit):
        parse_args(["--provider", "ark", "--refs", bad])


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_parse_args_rejects_non_positive_duration(bad: str):
    with pytest.raises(SystemExit):
        parse_args(["--provider", "ark", "--duration", bad])


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
        # 模拟落盘：写入非空 dummy 字节，让 run_once 的落盘校验通过
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"\x00fake-video-payload")
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self.name,
            model=self.model,
            duration_seconds=request.duration_seconds,
        )


class _NoWriteFakeBackend(_FakeBackend):
    """模拟：backend 返回成功但未落盘文件，用于 false-positive 校验。"""

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # 不写 output_path
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
async def test_run_once_flags_missing_output_as_failure(tmp_path: Path):
    """backend.generate 成功返回但文件未落盘 → 应判 FAIL 而非 false-PASS。"""
    result = await run_once(
        provider=Provider.ARK,
        backend=_NoWriteFakeBackend(),
        refs=2,
        duration=5,
        multi_shot=False,
        work_dir=tmp_path,
    )
    assert result.success is False
    assert "missing or empty" in (result.error or "")


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


def test_clamp_refs_respects_backend_max():
    from scripts.verify_reference_video_sdks import clamp_refs_for_backend

    caps = VideoCapabilities(reference_images=True, max_reference_images=3)
    clamped, note = clamp_refs_for_backend(requested=7, caps=caps)
    assert clamped == 3
    assert "clamped" in note.lower()


def test_clamp_refs_under_limit_passthrough():
    from scripts.verify_reference_video_sdks import clamp_refs_for_backend

    caps = VideoCapabilities(reference_images=True, max_reference_images=9)
    clamped, note = clamp_refs_for_backend(requested=3, caps=caps)
    assert clamped == 3
    assert note == ""


def test_clamp_refs_backend_without_reference_support():
    from scripts.verify_reference_video_sdks import clamp_refs_for_backend

    caps = VideoCapabilities(reference_images=False, max_reference_images=0)
    with pytest.raises(ValueError, match="does not support reference_images"):
        clamp_refs_for_backend(requested=1, caps=caps)


def test_resolve_backend_delegates_to_create_backend(monkeypatch):
    """resolve_backend 应把 Provider 映射到 lib.video_backends 名称并调用 create_backend。"""
    called: list[str] = []
    monkeypatch.setattr(mod, "create_backend", lambda name: called.append(name) or _FakeBackend())

    backend = mod.resolve_backend(Provider.VEO)

    assert called == [mod.PROVIDER_GEMINI]
    assert isinstance(backend, _FakeBackend)


@pytest.mark.asyncio
async def test_run_with_backend_writes_report(tmp_path: Path, monkeypatch):
    fake = _FakeBackend()
    report_dir = tmp_path / "reports"
    work_dir = tmp_path / "work"

    monkeypatch.setattr(mod, "resolve_backend", lambda p: fake)

    code = await mod.run_with_backend(
        provider=mod.Provider.ARK,
        refs=3,
        duration=5,
        multi_shot=True,
        report_dir=report_dir,
        work_dir=work_dir,
    )
    assert code == 0
    reports = list(report_dir.glob("reference-video-sdks-*.md"))
    assert len(reports) == 1
    content = reports[0].read_text(encoding="utf-8")
    assert "| ark |" in content
    assert "PASS" in content


@pytest.mark.asyncio
async def test_run_with_backend_returns_2_on_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mod, "resolve_backend", lambda p: _FailBackend())
    code = await mod.run_with_backend(
        provider=mod.Provider.ARK,
        refs=3,
        duration=5,
        multi_shot=False,
        report_dir=tmp_path / "reports",
        work_dir=tmp_path / "work",
    )
    assert code == 2
    # 失败时也应写报告（记录错误）
    reports = list((tmp_path / "reports").glob("reference-video-sdks-*.md"))
    assert len(reports) == 1
    content = reports[0].read_text(encoding="utf-8")
    assert "FAIL" in content


class _CappedFakeBackend(_FakeBackend):
    """max_reference_images=2，会触发 clamp note 分支。"""

    video_capabilities = VideoCapabilities(reference_images=True, max_reference_images=2)


@pytest.mark.asyncio
async def test_run_with_backend_note_written_when_clamped(tmp_path: Path, monkeypatch):
    """请求 5 refs 但 backend max=2，note 应被写入报告。"""
    monkeypatch.setattr(mod, "resolve_backend", lambda p: _CappedFakeBackend())
    code = await mod.run_with_backend(
        provider=mod.Provider.ARK,
        refs=5,
        duration=5,
        multi_shot=False,
        report_dir=tmp_path / "reports",
        work_dir=tmp_path / "work",
    )
    assert code == 0
    content = (list((tmp_path / "reports").glob("reference-video-sdks-*.md"))[0]).read_text(encoding="utf-8")
    assert "clamped" in content


@pytest.mark.asyncio
async def test_run_with_backend_appends_existing_rows(tmp_path: Path, monkeypatch):
    """第二次运行时，旧数据行应被保留（追加模式）。"""
    monkeypatch.setattr(mod, "resolve_backend", lambda p: _FakeBackend())
    report_dir = tmp_path / "reports"
    work_dir = tmp_path / "work"

    # 第一次跑
    await mod.run_with_backend(
        provider=mod.Provider.ARK,
        refs=3,
        duration=5,
        multi_shot=False,
        report_dir=report_dir,
        work_dir=work_dir,
    )
    # 第二次跑（同一天，会追加）
    await mod.run_with_backend(
        provider=mod.Provider.GROK,
        refs=3,
        duration=5,
        multi_shot=False,
        report_dir=report_dir,
        work_dir=work_dir,
    )
    reports = list(report_dir.glob("reference-video-sdks-*.md"))
    assert len(reports) == 1
    content = reports[0].read_text(encoding="utf-8")
    # 第一次和第二次的 provider 行都应存在
    assert "| ark |" in content
    assert "| grok |" in content


def test_extract_data_rows_skips_header_and_separator():
    lines = [
        "# Title",
        "",
        "| Provider | Model | Refs | Duration | Multi-shot | Result | Elapsed | Bytes | Note |",
        "|---|---|---|---|---|---|---|---|---|",
        "| ark | m1 | 3 | 5s | no | PASS | 1.0s | 0 |  |",
        "| grok | m2 | 7 | 5s | no | FAIL: 413 | 0.5s | 0 |  |",
    ]
    rows = mod._extract_data_rows(lines)
    assert len(rows) == 2
    assert rows[0].startswith("| ark |")
    assert rows[1].startswith("| grok |")


def test_extract_data_rows_keeps_error_with_provider_keyword():
    """回归：Result 字段含 'Provider' 关键字的数据行不应被误过滤。"""
    lines = [
        "| Provider | Model | Refs | Duration | Multi-shot | Result | Elapsed | Bytes | Note |",
        "|---|---|---|---|---|---|---|---|---|",
        "| ark | m1 | 3 | 5s | no | FAIL: Provider timeout after 30s | 30.0s | 0 |  |",
    ]
    rows = mod._extract_data_rows(lines)
    assert len(rows) == 1
    assert "Provider timeout" in rows[0]


def test_extract_data_rows_keeps_note_with_dashes():
    """回归：Note 字段含 '---' 的数据行不应被误过滤。"""
    lines = [
        "| Provider | Model | Refs | Duration | Multi-shot | Result | Elapsed | Bytes | Note |",
        "|---|---|---|---|---|---|---|---|---|",
        "| ark | m1 | 3 | 5s | no | PASS | 1.0s | 0 | --- legacy --- |",
    ]
    rows = mod._extract_data_rows(lines)
    assert len(rows) == 1
    assert "legacy" in rows[0]


def test_extract_data_rows_empty_input():
    assert mod._extract_data_rows([]) == []
