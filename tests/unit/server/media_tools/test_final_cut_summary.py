"""成片工具摘要：时间线级警告只报条数，内容信号告警逐条带时间区间。"""

from __future__ import annotations

from lib.final_cut.content_signals import FinalCutSignalWarning, SignalWarningKind
from lib.final_cut.ffmpeg_render import FinalCutAcceptance
from lib.final_cut.service import FinalCutRender
from server.media_tools.final_cuts import FinalCutToolResult, final_cut_summary


def _final_cut(warnings: tuple) -> FinalCutRender:
    return FinalCutRender(
        episode=1,
        timeline_id="tl-demo",
        revision=1,
        narration="without_narration",
        subtitles="burned_subtitles",
        artifact_path="renders/episode_1/tl-demo/final_cut.without_narration.burned_subtitles.mp4",
        version=1,
        rendered_at="2026-01-01T00:00:00Z",
        acceptance=FinalCutAcceptance(expected_duration=8.0, video_duration=8.0, audio_duration=8.0),
        warnings=warnings,
    )


def _summary(final_cut: FinalCutRender) -> str:
    value = FinalCutToolResult(final_cut=final_cut, download_url="/api/v1/files/demo/a.mp4?v=1", batch_id="batch-1")
    summary = final_cut_summary(value)
    assert summary is not None
    return summary


def test_content_signal_warnings_are_listed_with_their_spans() -> None:
    final_cut = _final_cut(
        (
            FinalCutSignalWarning(kind=SignalWarningKind.BLACK, start=2.0, end=5.0),
            FinalCutSignalWarning(kind=SignalWarningKind.SILENCE, start=6.0, end=8.5),
        )
    )

    assert "画面警告：黑场 3.0 秒（2.0–5.0 秒）、静音 2.5 秒（6.0–8.5 秒）" in _summary(final_cut)


def test_clean_render_mentions_no_warnings() -> None:
    summary = _summary(_final_cut(()))

    assert "警告" not in summary
    assert "画面警告" not in summary
