"""Execute final-cut render plans with bundled ffmpeg and accept rendered results.

Render pictures in segments separated by hard cuts. Scale/pad each clip to project canvas and
normalize frame rate, take fixed source-frame counts, then fill holds/transition borrowing
shortfalls with edge-frame freezes. Nonoverlapping transitions fade clip ends; overlapping
transitions use ``xfade`` within cut windows. For subtitles, shift segment timestamps to final-cut
time and let libass render the episode-wide ASS document. Encode every segment identically and
concatenate losslessly with ``-c copy``. Never segment audio: mix episode source audio by clip
volume/frame boundaries, voiceovers (narration variants only) from host-clip starts, and BGM from
clip starts at loudness gain × clip volume with fades. Encode once, then mux with concatenated
pictures, avoiding segment AAC priming gaps and accumulated drift. Limit the summed tracks before
encoding so high-gain BGM plus source audio/voiceovers never exceed full scale. Preserve voiceover
overruns truthfully: overlaps sound simultaneously; portions beyond final-cut end are truncated.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.final_cut.errors import FinalCutError
from lib.final_cut.render_plan import PlannedClip, RenderPlan, RenderSegment
from lib.final_cut.subtitles import BurnedSubtitle, ass_document
from lib.infra.async_thread import run_sync_transaction
from lib.infra.ffmpeg import local_file_input
from lib.infra.media_probe import MediaProbeError, probe_media
from lib.infra.subprocess_deadline import (
    DEFAULT_TERMINATE_GRACE_SECONDS,
    Spawner,
    SubprocessDeadlineExceeded,
    run_with_deadline,
)
from lib.subtitle_style.font import SUBTITLE_FONT_FILE

AUDIO_SAMPLE_RATE = 48_000

MIX_LIMIT = 0.891251
"""Mix limiter ceiling, linear amplitude approximately −1 dBFS, leaving room for AAC peak overshoot."""

ACCEPTANCE_TOLERANCE_SECONDS = 0.1
"""Maximum acceptance deviation between audio/video stream durations and editing-timeline duration."""

SUBTITLE_DOCUMENT = "subtitles.ass"
SUBTITLE_FONTS_DIR = "fonts"
"""Workspace-relative ASS/font paths for burned subtitles; render with workspace as current directory, avoiding filter-path escaping."""

_STDERR_TAIL = 2000


@dataclass(frozen=True, slots=True)
class RenderDeadlines:
    """Per-step ffmpeg subprocess deadlines: fixed floors plus duration-scaled headroom, in seconds."""

    floor: float = 120.0
    per_output_second: float = 20.0
    grace: float = DEFAULT_TERMINATE_GRACE_SECONDS

    def for_seconds(self, seconds: float) -> float:
        return self.floor + self.per_output_second * seconds


DEFAULT_RENDER_DEADLINES = RenderDeadlines()


def _base_args(ffmpeg: str) -> list[str]:
    return [ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y"]


def _seconds(microseconds: int) -> str:
    return f"{microseconds / 1_000_000:.6f}"


def _frames_seconds(frames: int, fps: int) -> str:
    return f"{frames / fps:.6f}"


def _clip_chain(index: int, planned: PlannedClip, plan: RenderPlan) -> str:
    """Clip picture filter chain: normalize canvas/frame rate, trim source frames, fill both ends with edge-frame holds, then add fades."""
    profile = plan.profile
    width, height = profile.width, profile.height
    source_frames = planned.lead.source_frames + planned.source_frames + planned.trail.source_frames
    filters = [
        f"fps={profile.fps}",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        "setsar=1",
        "format=yuv420p",
        f"trim=end_frame={max(source_frames, 1)}",
        f"tpad=start_mode=clone:start={planned.lead.freeze_frames}:stop_mode=clone:stop=-1",
        # tpad output does not declare frame rate, but xfade requires constant-rate input; redeclare on the endless padded stream, then stop by frame count.
        f"fps={profile.fps}",
        f"trim=end_frame={planned.stream_frames}",
    ]
    if planned.fade_in is not None:
        filters.append(f"fade=t=in:s=0:n={planned.fade_in.frames}:c={planned.fade_in.color}")
    if planned.fade_out is not None:
        start = planned.stream_frames - planned.fade_out.frames
        filters.append(f"fade=t=out:s={start}:n={planned.fade_out.frames}:c={planned.fade_out.color}")
    return f"[{index}:v:0]{','.join(filters)}[v{index}]"


@dataclass(frozen=True, slots=True)
class NarrationInput:
    """Voiceover mixed into episode audio, playing the entire recording from final-cut ``start_us``."""

    path: Path
    start_us: int


@dataclass(frozen=True, slots=True)
class BgmInput:
    """BGM mixed into episode audio: take ``duration_us`` from BGM ``source_in_us`` and place at final-cut ``start_us``.

    ``volume`` already combines loudness gain × clip volume; fade durations are microseconds, with 0 disabling fades.
    """

    path: Path
    start_us: int
    source_in_us: int
    duration_us: int
    volume: float
    fade_in_us: int
    fade_out_us: int


def _bgm_chain(index: int, bgm: BgmInput, start_sample: int) -> str:
    filters = [
        f"aresample={AUDIO_SAMPLE_RATE}",
        "aformat=sample_fmts=fltp:channel_layouts=stereo",
        f"atrim=end_sample={bgm.duration_us * AUDIO_SAMPLE_RATE // 1_000_000}",
        "asetpts=PTS-STARTPTS",
        f"volume={bgm.volume:.6f}",
    ]
    if bgm.fade_in_us > 0:
        filters.append(f"afade=t=in:st=0:d={_seconds(bgm.fade_in_us)}")
    if bgm.fade_out_us > 0:
        filters.append(f"afade=t=out:st={_seconds(bgm.duration_us - bgm.fade_out_us)}:d={_seconds(bgm.fade_out_us)}")
    filters.append(f"adelay=delays={start_sample}S:all=1")
    return f"[{index}:a:0]{','.join(filters)}[a{index}]"


def segment_args(
    ffmpeg: str, segment: RenderSegment, plan: RenderPlan, output: Path, *, burn_subtitles: bool = False
) -> list[str]:
    """Picture-only segment render arguments, stopping at exact frame counts.

    With ``burn_subtitles``, ASS/font paths are relative to the workspace; run with workspace as current directory.
    """
    fps = plan.profile.fps
    inputs: list[str] = []
    chains: list[str] = []
    for index, planned in enumerate(segment.clips):
        lead_us = planned.lead.source_frames * 1_000_000 // fps
        inputs += [
            "-ss",
            _seconds(max(planned.clip.source_in_us - lead_us, 0)),
            *local_file_input(planned.clip.video_path),
        ]
        chains.append(_clip_chain(index, planned, plan))
    label = "[v0]"
    for index, crossfade in enumerate(segment.crossfades, start=1):
        joined = f"[x{index}]"
        chains.append(
            f"{label}[v{index}]xfade=transition={crossfade.effect}:duration={_frames_seconds(crossfade.frames, fps)}"
            f":offset={_frames_seconds(crossfade.offset_frames, fps)}{joined}"
        )
        label = joined
    if burn_subtitles:
        # libass selects subtitles by picture timestamps: shift segment time to final-cut time before burning, then shift back.
        offset = _frames_seconds(segment.start_frame, fps)
        chains.append(
            f"{label}setpts=PTS+{offset}/TB,subtitles=f={SUBTITLE_DOCUMENT}:fontsdir={SUBTITLE_FONTS_DIR},"
            "setpts=PTS-STARTPTS[subtitled]"
        )
        label = "[subtitled]"
    return [
        *_base_args(ffmpeg),
        *inputs,
        "-filter_complex",
        ";".join(chains),
        "-map",
        label,
        "-an",
        "-sn",
        "-dn",
        "-frames:v",
        str(segment.frames),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-f",
        "mp4",
        str(output),
    ]


def _samples(frames: int, fps: int) -> int:
    return frames * AUDIO_SAMPLE_RATE // fps


def _audible(planned: PlannedClip) -> bool:
    return planned.clip.has_audio and planned.clip.source_volume > 0 and planned.source_frames > 0


def audio_mix_args(
    ffmpeg: str,
    plan: RenderPlan,
    output: Path,
    *,
    narrations: Sequence[NarrationInput] = (),
    bgm: Sequence[BgmInput] = (),
) -> list[str]:
    """Episode mix arguments: silence underneath, voiced clips placed at frame boundaries and scaled by source volume; voiceovers
    overlay at original volume from their starting frame boundaries. BGM starts at its frame boundary with gain/volume and fades;
    limit the sum and trim the whole mix to final-cut duration."""
    fps = plan.profile.fps
    total_samples = _samples(plan.total_frames, fps)
    inputs: list[str] = ["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=stereo"]
    chains = [f"[0:a]atrim=end_sample={total_samples}[base]"]
    labels = ["[base]"]
    for planned in (item for item in plan.clips if _audible(item)):
        index = len(labels)
        inputs += ["-ss", _seconds(planned.clip.source_in_us), *local_file_input(planned.clip.video_path)]
        chains.append(
            f"[{index}:a:0]aresample={AUDIO_SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"atrim=end_sample={_samples(planned.source_frames, fps)},asetpts=PTS-STARTPTS,"
            f"volume={planned.clip.source_volume:.4f},"
            f"adelay=delays={_samples(planned.start_frame, fps)}S:all=1[a{index}]"
        )
        labels.append(f"[a{index}]")
    for narration in narrations:
        index = len(labels)
        inputs += local_file_input(narration.path)
        start = _samples(plan.profile.frame_at(narration.start_us), fps)
        chains.append(
            f"[{index}:a:0]aresample={AUDIO_SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS,adelay=delays={start}S:all=1[a{index}]"
        )
        labels.append(f"[a{index}]")
    for item in bgm:
        index = len(labels)
        inputs += ["-ss", _seconds(item.source_in_us), *local_file_input(item.path)]
        chains.append(_bgm_chain(index, item, _samples(plan.profile.frame_at(item.start_us), fps)))
        labels.append(f"[a{index}]")
    if len(labels) == 1:
        chains = [f"[0:a]atrim=end_sample={total_samples}[mix]"]
    else:
        chains.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0:dropout_transition=0,"
            # Limit only excessive peaks: disable automatic leveling and compensate lookahead delay to avoid shifting the whole track.
            f"alimiter=limit={MIX_LIMIT}:level=false:latency=true,"
            f"atrim=end_sample={total_samples}[mix]"
        )
    return [
        *_base_args(ffmpeg),
        *inputs,
        "-filter_complex",
        ";".join(chains),
        "-map",
        "[mix]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-f",
        "mp4",
        str(output),
    ]


def mux_args(ffmpeg: str, concat_list: Path, audio: Path, output: Path) -> list[str]:
    """Losslessly concatenate segment pictures and mux with episode audio into a final cut."""
    return [
        *_base_args(ffmpeg),
        "-f",
        "concat",
        "-safe",
        "0",
        *local_file_input(concat_list),
        *local_file_input(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(output),
    ]


async def _run(
    args: list[str], *, deadline: float, grace: float, spawn: Spawner | None, output: Path, cwd: Path | None = None
) -> None:
    try:
        result = await run_with_deadline(
            args,
            deadline_seconds=deadline,
            grace=grace,
            capture_stderr=True,
            cleanup_paths=(output,),
            spawn=spawn,
            cwd=cwd,
        )
    except SubprocessDeadlineExceeded:
        raise FinalCutError("final_cut_render_failed", f"ffmpeg 渲染超时：{output.name}") from None
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()[-_STDERR_TAIL:]
        raise FinalCutError(
            "final_cut_render_failed",
            f"ffmpeg 渲染失败（退出码 {result.returncode}）：{stderr}",
            returncode=result.returncode,
        )


async def render_plan_to_file(
    ffmpeg: str,
    plan: RenderPlan,
    output: Path,
    workspace: Path,
    *,
    narrations: Sequence[NarrationInput] = (),
    bgm: Sequence[BgmInput] = (),
    subtitles: Sequence[BurnedSubtitle] | None = None,
    deadlines: RenderDeadlines = DEFAULT_RENDER_DEADLINES,
    spawn: Spawner | None = None,
) -> None:
    """Execute plans: render segment pictures, mix episode audio, then concatenate/mux into ``output``; intermediates stay in ``workspace``.

    Non-None ``subtitles`` burns subtitles (possibly empty); ``narrations`` / ``bgm`` provide episode audio inputs.
    """
    fps = plan.profile.fps
    burn = subtitles is not None
    if subtitles is not None:
        await run_sync_transaction(_stage_subtitles, workspace, ass_document(subtitles, plan.profile))
    segment_files: list[Path] = []
    for index, segment in enumerate(plan.segments):
        segment_file = workspace / f"segment_{index:04d}.mp4"
        await _run(
            segment_args(ffmpeg, segment, plan, segment_file, burn_subtitles=burn),
            deadline=deadlines.for_seconds(segment.frames / fps),
            grace=deadlines.grace,
            spawn=spawn,
            output=segment_file,
            cwd=workspace if burn else None,
        )
        segment_files.append(segment_file)
    audio_file = workspace / "audio.m4a"
    await _run(
        audio_mix_args(ffmpeg, plan, audio_file, narrations=narrations, bgm=bgm),
        deadline=deadlines.for_seconds(plan.duration_seconds),
        grace=deadlines.grace,
        spawn=spawn,
        output=audio_file,
    )
    concat_list = workspace / "segments.txt"
    await run_sync_transaction(
        concat_list.write_text, "".join(f"file '{path.name}'\n" for path in segment_files), encoding="utf-8"
    )
    await _run(
        mux_args(ffmpeg, concat_list, audio_file, output),
        deadline=deadlines.for_seconds(plan.duration_seconds / 10),
        grace=deadlines.grace,
        spawn=spawn,
        output=output,
    )


def _stage_subtitles(workspace: Path, document: str) -> None:
    """Place ASS/font files into the workspace; prefer font hard links, copying across volumes."""
    (workspace / SUBTITLE_DOCUMENT).write_text(document, encoding="utf-8")
    fonts = workspace / SUBTITLE_FONTS_DIR
    fonts.mkdir(exist_ok=True)
    target = fonts / SUBTITLE_FONT_FILE.name
    try:
        target.hardlink_to(SUBTITLE_FONT_FILE)
    except OSError:
        shutil.copyfile(SUBTITLE_FONT_FILE, target)


@dataclass(frozen=True, slots=True)
class FinalCutAcceptance:
    """Final-cut acceptance results: expected editing-timeline duration and measured audio/video stream durations, in seconds."""

    expected_duration: float
    video_duration: float
    audio_duration: float
    tolerance: float = ACCEPTANCE_TOLERANCE_SECONDS


async def accept_final_cut(path: Path, plan: RenderPlan, *, spawn: Spawner | None = None) -> FinalCutAcceptance:
    """Accept through media probes: both audio/video streams exist and each duration differs from the timeline by no more than tolerance."""
    try:
        probe = await probe_media(path, spawn=spawn)
    except MediaProbeError as exc:
        raise FinalCutError("final_cut_acceptance_failed", f"成片无法探测：{exc}") from exc
    video = probe.first_stream("video")
    audio = probe.first_stream("audio")
    if video is None or video.duration_seconds is None or audio is None or audio.duration_seconds is None:
        raise FinalCutError(
            "final_cut_acceptance_failed",
            "成片缺少视频流或音频流",
            has_video=video is not None,
            has_audio=audio is not None,
        )
    acceptance = FinalCutAcceptance(
        expected_duration=round(plan.duration_seconds, 3),
        video_duration=round(video.duration_seconds, 3),
        audio_duration=round(audio.duration_seconds, 3),
    )
    worst = max(
        abs(video.duration_seconds - plan.duration_seconds), abs(audio.duration_seconds - plan.duration_seconds)
    )
    if worst > ACCEPTANCE_TOLERANCE_SECONDS:
        raise FinalCutError(
            "final_cut_acceptance_failed",
            f"成片时长偏差超出容差：应为 {acceptance.expected_duration} 秒，"
            f"视频 {acceptance.video_duration} 秒、音频 {acceptance.audio_duration} 秒",
            expected_duration=acceptance.expected_duration,
            video_duration=acceptance.video_duration,
            audio_duration=acceptance.audio_duration,
        )
    return acceptance


__all__ = [
    "ACCEPTANCE_TOLERANCE_SECONDS",
    "AUDIO_SAMPLE_RATE",
    "DEFAULT_RENDER_DEADLINES",
    "BgmInput",
    "FinalCutAcceptance",
    "NarrationInput",
    "RenderDeadlines",
    "accept_final_cut",
    "audio_mix_args",
    "mux_args",
    "render_plan_to_file",
    "segment_args",
]
