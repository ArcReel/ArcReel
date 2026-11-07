"""Jianying draft artifact storage, acceptance, and download packaging.

Artifacts are zips containing draft files, generated hold frames, and a material index, without
copying project videos/voiceovers:

- ``draft/``: pyJianYingDraft files with material-path placeholders ``{{ARCREEL_JIANYING_ASSETS}}/<素材名>``;
- ``assets/``: out-point frozen frames for hold extensions;
- ``arcreel_jianying_draft.json``: material-name/source index; sources are immutable project version snapshots or embedded artifact files.

Only on download, substitute local draft directory/name and Jianying version (5.x uses
``draft_content.json``, 6+ ``draft_info.json``), packaging project materials into the download.
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

import pyJianYingDraft as draft
from pyJianYingDraft import (
    AudioMaterial,
    AudioSegment,
    ClipSettings,
    FontType,
    TextBorder,
    TextSegment,
    TextShadow,
    TextStyle,
    TrackSpec,
    TrackType,
    TransitionType,
    VideoMaterial,
    VideoSegment,
    trange,
)

from lib.edit_timeline.transitions import transition_preset
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.jianying_draft.placement import DraftPlacement, PlacedClip, stack_tracks
from lib.subtitle_style.baseline import subtitle_style_baseline

type JianyingVersion = Literal["5", "6"]

ASSETS_PLACEHOLDER = "{{ARCREEL_JIANYING_ASSETS}}"
INDEX_NAME = "arcreel_jianying_draft.json"
DRAFT_DIR = "draft"
ASSETS_DIR = "assets"
CONTENT_NAME = "draft_content.json"
INDEX_FORMAT = 1

SUBTITLE_TRACK = "字幕"
NARRATION_TRACK = "旁白"
BGM_TRACK = "BGM"
SUBTITLE_FONT = FontType.SourceHanSansCN_Bold
EXTRA_SUBTITLE_TRACK_RAISE = 0.2
"""Extra vertical raise per additional subtitle track, in Jianying coordinates (1 equals half the canvas height)."""

_STORED_SUFFIXES = frozenset({".mp4", ".webm", ".mov", ".avi", ".mkv", ".png", ".jpg", ".jpeg"})


class JianyingDraftArchiveError(ValueError):
    """Jianying draft artifact has an invalid shape or references missing project materials."""


def canvas_size(aspect_ratio: str) -> tuple[int, int]:
    """Draft canvas for the project aspect ratio, with short edge 1080."""
    try:
        width_ratio, height_ratio = (float(part) for part in aspect_ratio.split(":"))
    except ValueError:
        width_ratio, height_ratio = 9.0, 16.0
    if width_ratio <= 0 or height_ratio <= 0:
        width_ratio, height_ratio = 9.0, 16.0
    if width_ratio >= height_ratio:
        return round(1080 * width_ratio / height_ratio / 2) * 2, 1080
    return 1080, round(1080 * height_ratio / width_ratio / 2) * 2


def _subtitle_style(width: int, height: int) -> tuple[TextStyle, TextBorder, TextShadow, float]:
    """Subtitle style follows :mod:`lib.subtitle_style.baseline`: white bold text, outline, shadow; the final item is vertical position."""
    baseline = subtitle_style_baseline(width, height)
    return (
        TextStyle(
            size=baseline.size,
            color=(1.0, 1.0, 1.0),
            align=1,
            bold=True,
            auto_wrapping=True,
            max_line_width=baseline.max_line_width,
        ),
        TextBorder(color=(0.0, 0.0, 0.0), width=30.0),
        TextShadow(color=(0.0, 0.0, 0.0), alpha=0.7, diffuse=8.0, distance=3.0, angle=-45.0),
        baseline.transform_y,
    )


def _track_name(base: str, index: int) -> str:
    """First track of a kind uses its base name, followed by numbered names: 旁白, 旁白 2, 旁白 3…"""
    return base if index == 0 else f"{base} {index + 1}"


class _AssetStaging:
    """Stage all draft-referenced files in one directory, with names unique within the draft."""

    def __init__(self, directory: Path, project_dir: Path) -> None:
        self.directory = directory
        self._project_dir = project_dir
        self._by_project_path: dict[str, str] = {}
        self.index: dict[str, dict[str, object]] = {}

    def _unique_name(self, name: str) -> str:
        stem, suffix = os.path.splitext(name)
        candidate, counter = name, 1
        while candidate in self.index:
            candidate = f"{stem}_{counter}{suffix}"
            counter += 1
        return candidate

    def project_file(self, relative_path: str) -> str:
        name = self._by_project_path.get(relative_path)
        if name is None:
            try:
                source = safe_join(self._project_dir, relative_path, require_file=True)
            except (PathTraversalError, FileNotFoundError) as exc:
                raise JianyingDraftArchiveError(f"草稿素材不在项目内或已不存在：{relative_path}") from exc
            name = self._unique_name(source.name)
            try:
                (self.directory / name).hardlink_to(source)
            except OSError:
                shutil.copy2(source, self.directory / name)
            self._by_project_path[relative_path] = name
            self.index[name] = {"project": relative_path}
        return str(self.directory / name)

    def bundled_file(self, source: Path, name: str) -> str:
        name = self._unique_name(name)
        shutil.copy2(source, self.directory / name)
        self.index[name] = {"bundled": True}
        return str(self.directory / name)


def _map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {key: _map_strings(item, transform) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_strings(item, transform) for item in value]
    return value


def _attach_transition(segments: list[VideoSegment], clip: PlacedClip) -> None:
    """Attach transitions to the clip’s final picture segment: out-point frozen frame with holds, otherwise source material."""
    transition = clip.transition_to_next
    if transition is None or not segments:
        return
    segments[-1].add_transition(
        TransitionType[transition_preset(transition.type).jianying], duration=transition.duration_us
    )


def write_jianying_draft(
    placement: DraftPlacement,
    *,
    project_dir: Path,
    width: int,
    height: int,
    hold_frames: Mapping[str, Path],
    with_narration_track: bool,
    output: Path,
    workspace: Path,
) -> None:
    """Write arranged clips as a Jianying draft artifact; ``hold_frames`` maps clip IDs to out-point frozen frames.

    Always include at least one subtitle track and, for narration variants, one narration track.
    Overlapping narration/subtitles add tracks as needed, with no overlaps within tracks. BGM
    adds its own track, storing volume/fades in clip volume/fade fields. Raise each added subtitle
    track: track n sits ``(n - 1) × EXTRA_SUBTITLE_TRACK_RAISE`` above the first.
    Draft directories and material staging stay under ``workspace``; callers clean them up.
    """
    root = workspace
    staging = _AssetStaging(root / ASSETS_DIR, project_dir)
    staging.directory.mkdir()
    (root / "drafts").mkdir()
    script = draft.DraftFolder(str(root / "drafts")).create_draft(DRAFT_DIR, width=width, height=height)
    subtitle_tracks = stack_tracks(placement.subtitles) or ((),)
    narration_tracks = (stack_tracks(placement.narrations) or ((),)) if with_narration_track else ()
    tracks = [
        TrackSpec(TrackType.video),
        *(TrackSpec(TrackType.text, _track_name(SUBTITLE_TRACK, index)) for index in range(len(subtitle_tracks))),
        *(TrackSpec(TrackType.audio, _track_name(NARRATION_TRACK, index)) for index in range(len(narration_tracks))),
        *((TrackSpec(TrackType.audio, BGM_TRACK),) if placement.bgm else ()),
    ]
    script.append_tracks(tracks)

    for clip in placement.clips:
        segments: list[VideoSegment] = []
        if clip.source_duration_us > 0:
            segments.append(
                VideoSegment(
                    VideoMaterial(staging.project_file(clip.video_path)),
                    trange(clip.start_us, clip.source_duration_us),
                    source_timerange=trange(clip.source_in_us, clip.source_duration_us),
                    volume=clip.volume,
                )
            )
        if clip.hold_us > 0:
            still = staging.bundled_file(hold_frames[clip.clip_id], f"hold_{clip.clip_id}.png")
            segments.append(
                VideoSegment(
                    VideoMaterial(still),
                    trange(clip.hold_start_us, clip.hold_us),
                    source_timerange=trange(0, clip.hold_us),
                )
            )
        _attach_transition(segments, clip)
        for segment in segments:
            script.add_segment(segment)

    for index, track in enumerate(narration_tracks):
        for narration in track:
            script.add_segment(
                AudioSegment(
                    AudioMaterial(staging.project_file(narration.audio_path)),
                    trange(narration.start_us, narration.duration_us),
                    source_timerange=trange(0, narration.duration_us),
                ),
                _track_name(NARRATION_TRACK, index),
            )

    for bgm in placement.bgm:
        material = AudioMaterial(staging.project_file(bgm.audio_path))
        # Trust pyJianYingDraft media-duration probes; if they differ from registration-time ffmpeg measurements, clamp to media end.
        duration = min(bgm.duration_us, material.duration - bgm.source_in_us)
        if duration <= 0:
            continue
        fade_out = min(bgm.fade_out_us, duration)
        segment = AudioSegment(
            material,
            trange(bgm.start_us, duration),
            source_timerange=trange(bgm.source_in_us, duration),
            volume=bgm.volume,
        )
        if bgm.fade_in_us > 0 or fade_out > 0:
            segment.add_fade(min(bgm.fade_in_us, duration - fade_out), fade_out)
        script.add_segment(segment, BGM_TRACK)

    style, border, shadow, transform_y = _subtitle_style(width, height)
    for index, track in enumerate(subtitle_tracks):
        position = ClipSettings(transform_y=transform_y + index * EXTRA_SUBTITLE_TRACK_RAISE)
        for subtitle in track:
            script.add_segment(
                TextSegment(
                    text=subtitle.text,
                    timerange=trange(subtitle.start_us, subtitle.duration_us),
                    font=SUBTITLE_FONT,
                    style=style,
                    border=border,
                    shadow=shadow,
                    clip_settings=position,
                ),
                _track_name(SUBTITLE_TRACK, index),
            )
    script.save()

    draft_dir = root / "drafts" / DRAFT_DIR
    content_path = draft_dir / CONTENT_NAME
    staged_prefix = str(staging.directory) + os.sep
    content = _map_strings(
        json.loads(content_path.read_text(encoding="utf-8")),
        lambda text: text.replace(staged_prefix, f"{ASSETS_PLACEHOLDER}/"),
    )
    content_path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")

    index = {"format": INDEX_FORMAT, "duration_us": placement.duration_us, "assets": staging.index}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(INDEX_NAME, json.dumps(index, ensure_ascii=False))
        for file in sorted(draft_dir.rglob("*")):
            if file.is_file():
                archive.write(file, f"{DRAFT_DIR}/{file.relative_to(draft_dir).as_posix()}")
        for name, source in staging.index.items():
            if source.get("bundled") is True:
                archive.write(staging.directory / name, f"{ASSETS_DIR}/{name}", zipfile.ZIP_STORED)


def _read_index(archive: zipfile.ZipFile) -> dict[str, dict[str, object]]:
    try:
        index = json.loads(archive.read(INDEX_NAME).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, ValueError) as exc:
        raise JianyingDraftArchiveError("剪映草稿产物缺少可读的素材索引") from exc
    if not isinstance(index, dict) or index.get("format") != INDEX_FORMAT or not isinstance(index.get("assets"), dict):
        raise JianyingDraftArchiveError("剪映草稿产物的素材索引格式不受支持")
    return index["assets"]


def _read_content(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        content = json.loads(archive.read(f"{DRAFT_DIR}/{CONTENT_NAME}").decode("utf-8"))
    except (KeyError, UnicodeDecodeError, ValueError) as exc:
        raise JianyingDraftArchiveError("剪映草稿产物缺少可读的草稿内容") from exc
    if not isinstance(content, dict):
        raise JianyingDraftArchiveError("剪映草稿产物的草稿内容不是对象")
    return content


def _material_paths(content: Mapping[str, Any]) -> list[str]:
    materials = content.get("materials")
    paths: list[str] = []
    for group in ("videos", "audios"):
        entries = materials.get(group) if isinstance(materials, Mapping) else None
        paths.extend(
            entry["path"]
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, Mapping) and isinstance(entry.get("path"), str)
        )
    return paths


def _resolve_project_asset(project_dir: Path, source: Mapping[str, object]) -> Path:
    relative = source.get("project")
    if not isinstance(relative, str):
        raise JianyingDraftArchiveError("剪映草稿产物的素材来源无效")
    try:
        return safe_join(project_dir, relative, require_file=True)
    except (PathTraversalError, FileNotFoundError) as exc:
        raise JianyingDraftArchiveError(f"剪映草稿引用的项目素材已不存在：{relative}") from exc


def verify_jianying_draft(path: Path, *, project_dir: Path, expected_duration_us: int) -> None:
    """Accept Jianying draft artifacts: all material paths are placeholders with resolvable sources, and main-video duration equals timeline duration."""
    with zipfile.ZipFile(path) as archive:
        assets = _read_index(archive)
        content = _read_content(archive)
        names = set(archive.namelist())
    for material_path in _material_paths(content):
        name = material_path.removeprefix(f"{ASSETS_PLACEHOLDER}/")
        if name == material_path or name not in assets:
            raise JianyingDraftArchiveError(f"草稿素材路径没有写成占位符：{material_path}")
    for name, source in assets.items():
        if source.get("bundled") is True:
            if f"{ASSETS_DIR}/{name}" not in names:
                raise JianyingDraftArchiveError(f"剪映草稿产物缺少自带素材：{name}")
        else:
            _resolve_project_asset(project_dir, source)
    video_end = 0
    for track in content.get("tracks") or []:
        if isinstance(track, Mapping) and track.get("type") == "video":
            for segment in track.get("segments") or []:
                target = segment.get("target_timerange") if isinstance(segment, Mapping) else None
                if isinstance(target, Mapping):
                    video_end = max(video_end, int(target["start"]) + int(target["duration"]))
    if video_end != expected_duration_us:
        raise JianyingDraftArchiveError(
            f"草稿主视频轨时长 {video_end} 微秒与剪辑时间线时长 {expected_duration_us} 微秒不一致"
        )


def package_jianying_draft(
    path: Path,
    *,
    project_dir: Path,
    draft_root: str,
    draft_name: str,
    jianying_version: JianyingVersion,
    output: Path,
) -> None:
    """Substitute local directory/Jianying version and package draft plus project materials as a zip extractable directly into Jianying’s draft directory."""
    assets_dir = f"{draft_root.rstrip('/\\')}/{draft_name}/{ASSETS_DIR}"
    content_name = "draft_info.json" if jianying_version == "6" else CONTENT_NAME
    with zipfile.ZipFile(path) as archive, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as packed:
        assets = _read_index(archive)
        content = _map_strings(
            _read_content(archive), lambda text: text.replace(f"{ASSETS_PLACEHOLDER}/", f"{assets_dir}/")
        )
        packed.writestr(f"{draft_name}/{content_name}", json.dumps(content, ensure_ascii=False))
        for info in archive.infolist():
            if info.is_dir() or not info.filename.startswith(f"{DRAFT_DIR}/"):
                continue
            relative = info.filename.removeprefix(f"{DRAFT_DIR}/")
            if relative != CONTENT_NAME:
                packed.writestr(f"{draft_name}/{relative}", archive.read(info))
        for name, source in assets.items():
            target = f"{draft_name}/{ASSETS_DIR}/{name}"
            compression = zipfile.ZIP_STORED if Path(name).suffix.lower() in _STORED_SUFFIXES else zipfile.ZIP_DEFLATED
            if source.get("bundled") is True:
                packed.writestr(target, archive.read(f"{ASSETS_DIR}/{name}"), compress_type=compression)
            else:
                packed.write(_resolve_project_asset(project_dir, source), target, compress_type=compression)


__all__ = [
    "ASSETS_PLACEHOLDER",
    "BGM_TRACK",
    "NARRATION_TRACK",
    "SUBTITLE_FONT",
    "SUBTITLE_TRACK",
    "JianyingDraftArchiveError",
    "JianyingVersion",
    "canvas_size",
    "package_jianying_draft",
    "verify_jianying_draft",
    "write_jianying_draft",
]
