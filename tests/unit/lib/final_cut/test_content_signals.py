"""成片内容信号告警：黑场 / 卡帧按阈值复核，静默段只在预期有声窗口内告警。"""

from __future__ import annotations

from lib.final_cut.content_signals import (
    BLACK_WARN_SECONDS,
    FREEZE_WARN_SECONDS,
    SILENCE_WARN_SECONDS,
    SignalWarningKind,
    content_signal_warnings,
)
from lib.video_review.signals import TimeSpan, VideoSignals


def _signals(*, black: tuple[TimeSpan, ...] = (), freeze: tuple[TimeSpan, ...] = ()) -> VideoSignals:
    return VideoSignals(duration_seconds=10.0, black=black, freeze=freeze, cuts=())


def test_black_spans_over_the_threshold_warn() -> None:
    signals = _signals(black=(TimeSpan(1.0, 1.0 + BLACK_WARN_SECONDS + 0.5), TimeSpan(5.0, 5.8)))

    warnings = content_signal_warnings(signals, ())

    assert [warning.kind for warning in warnings] == [SignalWarningKind.BLACK]
    assert (warnings[0].start, warnings[0].end) == (1.0, 1.0 + BLACK_WARN_SECONDS + 0.5)


def test_black_span_below_the_threshold_does_not_warn() -> None:
    signals = _signals(black=(TimeSpan(1.0, 1.0 + BLACK_WARN_SECONDS - 0.1),))

    assert content_signal_warnings(signals, ()) == ()


def test_freeze_spans_over_the_threshold_warn_like_black() -> None:
    signals = _signals(freeze=(TimeSpan(2.0, 2.0 + FREEZE_WARN_SECONDS + 0.2), TimeSpan(6.0, 6.5)))

    warnings = content_signal_warnings(signals, ())

    assert [warning.kind for warning in warnings] == [SignalWarningKind.FREEZE]
    assert (warnings[0].start, warnings[0].end) == (2.0, 2.0 + FREEZE_WARN_SECONDS + 0.2)


def test_silence_warns_only_inside_an_expected_audio_window() -> None:
    span = TimeSpan(4.0, 4.0 + SILENCE_WARN_SECONDS + 0.5)

    assert content_signal_warnings(_signals(), (span,)) == ()

    warnings = content_signal_warnings(_signals(), (span,), expected_audio=(TimeSpan(3.0, 9.0),))

    assert [warning.kind for warning in warnings] == [SignalWarningKind.SILENCE]
    assert (warnings[0].start, warnings[0].end) == (span.start, span.end)


def test_short_silence_inside_an_expected_audio_window_does_not_warn() -> None:
    span = TimeSpan(4.0, 4.0 + SILENCE_WARN_SECONDS - 0.2)

    assert content_signal_warnings(_signals(), (span,), expected_audio=(TimeSpan(3.0, 9.0),)) == ()


def test_warnings_are_sorted_by_start() -> None:
    signals = _signals(
        black=(TimeSpan(8.0, 10.5),),
        freeze=(TimeSpan(1.0, 1.0 + FREEZE_WARN_SECONDS),),
    )
    silence = (TimeSpan(4.0, 4.0 + SILENCE_WARN_SECONDS),)
    expected_audio = (TimeSpan(3.5, 5.0),)

    warnings = content_signal_warnings(signals, silence, expected_audio=expected_audio)

    assert [warning.kind for warning in warnings] == [
        SignalWarningKind.FREEZE,
        SignalWarningKind.SILENCE,
        SignalWarningKind.BLACK,
    ]
