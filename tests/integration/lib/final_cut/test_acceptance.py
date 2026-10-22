"""成片验收使用真实媒体：完整解码失败拒收，黑屏和有意定格只给提示。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.final_cut.errors import FinalCutError
from lib.final_cut.ffmpeg_render import accept_final_cut
from lib.final_cut.render_plan import OutputProfile, RenderPlan
from lib.infra.media_probe import probe_media
from tests.factories import make_signal_clip, make_test_clip, run_bundled_ffmpeg


def _plan(seconds: int) -> RenderPlan:
    return RenderPlan(profile=OutputProfile(width=320, height=180, fps=25), segments=(), total_frames=seconds * 25)


async def test_normal_export_verifies_video_and_audio_without_claiming_creative_review(tmp_path: Path) -> None:
    output = tmp_path / "normal.mp4"
    make_test_clip(output, size="320x180", fps=25, seconds=2, tone=True)

    acceptance = await accept_final_cut(output, _plan(2))

    assert acceptance.expected_duration == 2
    assert acceptance.video_duration == pytest.approx(2, abs=0.05)
    assert acceptance.audio_duration == pytest.approx(2, abs=0.05)
    review = acceptance.technical_review
    assert review is not None
    assert review.status == "PASS"
    assert review.decode_verified is True
    assert review.warnings == ()
    assert (review.signals.black, review.signals.freeze, review.signals.cuts) == ((), (), ())
    assert review.visual_quality_assessed is False
    assert review.audio_quality_assessed is False


async def test_export_without_audio_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "noaudio.mp4"
    make_test_clip(output, size="320x180", fps=25, seconds=2, tone=False)

    with pytest.raises(FinalCutError) as failure:
        await accept_final_cut(output, _plan(2))

    assert failure.value.code == "final_cut_acceptance_failed"
    assert failure.value.params == {"has_video": True, "has_audio": False}


async def test_intentional_black_freeze_and_silence_are_accepted_with_advisory_signals(tmp_path: Path) -> None:
    video = tmp_path / "signals.mp4"
    output = tmp_path / "artistic.mp4"
    make_signal_clip(video)
    run_bundled_ffmpeg(
        "-i",
        str(video),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-t",
        "5",
        str(output),
    )

    acceptance = await accept_final_cut(output, _plan(5))

    review = acceptance.technical_review
    assert review is not None
    assert review.decode_verified is True
    assert review.status == "WARN"
    assert review.warnings == ("BLACK", "FREEZE")
    assert [(span.start, span.end) for span in review.signals.black] == [pytest.approx((1.0, 2.0))]
    assert [(span.start, span.end) for span in review.signals.freeze] == [pytest.approx((3.0, 5.0))]
    assert review.signals.cuts == pytest.approx((1.0, 2.0, 3.0))


@pytest.mark.parametrize("stream", ["v", "a"], ids=["video", "audio"])
async def test_decode_rejects_corrupt_packets_even_when_streams_and_durations_are_valid(
    tmp_path: Path, stream: str
) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "corrupt.mp4"
    make_test_clip(source, size="320x180", fps=25, seconds=2, tone=True)
    # 破坏中间编码包的内容，保留首尾包的时间戳，单靠探测仍能通过。
    run_bundled_ffmpeg(
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c",
        "copy",
        f"-bsf:{stream}",
        r"noise=amount=if(eq(n\,25)\,1\,0)",
        str(output),
    )
    probe = await probe_media(output)
    for kind in ("video", "audio"):
        media_stream = probe.first_stream(kind)
        assert media_stream is not None
        assert media_stream.duration_seconds == pytest.approx(2, abs=0.05)

    with pytest.raises(FinalCutError) as failure:
        await accept_final_cut(output, _plan(2))

    assert failure.value.code == "final_cut_acceptance_failed"
    assert failure.value.params == {"phase": "decode"}
