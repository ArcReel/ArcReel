"""用随包 ffmpeg 执行成片的渲染规划，并验收渲染结果。

画面按硬切边界分段渲染：段内每个片段按项目画布缩放补边、规整到固定帧率，取定帧数的源画面后用
边缘帧的静帧补足定格延长与转场借帧的余量缺口，非重叠型转场在片段两端淡入淡出，重叠型转场用
``xfade`` 在切点窗口内交叉过渡；烧入字幕时各段把画面时间平移回成片时间，交给 libass 按整集的 ASS 文档
渲染。各段用同一组编码参数输出，最后以 ``-c copy`` 无损拼接。音频不分段：整集原声按片段音量与帧边界、
旁白配音（仅带旁白版本）按承载片段的起点、BGM 按片段起点以响度增益乘片段音量并做淡入淡出，一次混音、
一次编码，再与拼好的画面封装，避免各段 AAC 编码的前置填充在段边界产生缝隙与累积偏差。旁白越界如实渲染：
重叠处同时响起，超出末尾的部分随成片截止。
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.final_cut.errors import FinalCutError
from lib.final_cut.render_plan import PlannedClip, RenderPlan, RenderSegment
from lib.final_cut.subtitles import BurnedSubtitle, ass_document
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

ACCEPTANCE_TOLERANCE_SECONDS = 0.1
"""验收时音视频流时长与剪辑时间线时长之间允许的最大偏差。"""

SUBTITLE_DOCUMENT = "subtitles.ass"
SUBTITLE_FONTS_DIR = "fonts"
"""烧入字幕时 ASS 文档与字体在工作目录里的相对路径；画面渲染以工作目录为当前目录，滤镜参数不必转义路径。"""

_STDERR_TAIL = 2000


@dataclass(frozen=True, slots=True)
class RenderDeadlines:
    """各步 ffmpeg 子进程的 deadline：固定下限加按时长放大的余量（秒）。"""

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
    """一个片段的画面滤镜链：规整画布与帧率，截出所需源画面，两端用边缘帧定格补齐，再加淡入淡出。"""
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
        # tpad 的输出不声明帧率，xfade 要求输入是恒定帧率；在无尽的补帧流上重新声明，再按帧数截止。
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
    """混进整集音频的一段旁白配音：从成片时间 ``start_us`` 起整段播放。"""

    path: Path
    start_us: int


@dataclass(frozen=True, slots=True)
class BgmInput:
    """混进整集音频的一段 BGM：从 BGM 的 ``source_in_us`` 起取 ``duration_us``，放在成片时间 ``start_us``。

    ``volume`` 已是响度增益乘以片段音量；淡入淡出以微秒计，0 表示不做。
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
    """一段片段的画面渲染参数：只含画面，按帧数精确截止。

    ``burn_subtitles`` 时按相对路径引用工作目录里的 ASS 文档与字体，须以工作目录为当前目录运行。
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
        # libass 按画面时间取字幕：先把段内时间平移到成片时间，烧入后再移回段内时间。
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
    """整集混音参数：以静音垫底，每个有声片段按帧边界落位、按原声音量缩放；旁白配音从起点所在的帧边界起
    原音量叠加；BGM 从起点所在的帧边界起按增益与音量叠加并淡入淡出，整体截到成片时长。"""
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
    """把各段画面无损拼接，并与整集混音封装成成片。"""
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
    """执行渲染规划：逐段渲染画面、整集混音，再拼接封装到 ``output``；中间文件只写在 ``workspace``。

    ``subtitles`` 不为 None 时烧入字幕（可以为空），``narrations`` 与 ``bgm`` 是要混进整集音频的旁白配音与 BGM。
    """
    fps = plan.profile.fps
    burn = subtitles is not None
    if subtitles is not None:
        await asyncio.to_thread(_stage_subtitles, workspace, ass_document(subtitles, plan.profile))
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
    await asyncio.to_thread(
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
    """把 ASS 文档与随包字体放进工作目录；字体优先硬链接，跨卷时复制。"""
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
    """成片验收结果：剪辑时间线应有的时长与实测的音视频流时长（秒）。"""

    expected_duration: float
    video_duration: float
    audio_duration: float
    tolerance: float = ACCEPTANCE_TOLERANCE_SECONDS


async def accept_final_cut(path: Path, plan: RenderPlan, *, spawn: Spawner | None = None) -> FinalCutAcceptance:
    """经媒体探测接口验收成片：音视频流齐全，两者时长都与剪辑时间线时长相差不超过容差。"""
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
