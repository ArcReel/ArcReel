"""JianyingDraft export service.

Exports the generated video clips of a single ArcReel episode as a JianyingDraft ZIP.
Uses the pyJianYingDraft library to produce draft_content.json,
then performs a post-processing path replacement so the draft points to the user's local Jianying directory.
"""

import json
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft
from pyJianYingDraft import (
    ClipSettings,
    TextBorder,
    TextSegment,
    TextShadow,
    TextStyle,
    TrackType,
    VideoMaterial,
    VideoSegment,
    trange,
)

from lib.project_manager import ProjectManager

logger = logging.getLogger(__name__)


class JianyingDraftService:
    """JianyingDraft export service."""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    # ------------------------------------------------------------------
    # Internal methods: data extraction
    # ------------------------------------------------------------------

    def _find_episode_script(self, project_name: str, project: dict, episode: int) -> tuple[dict, str]:
        """Locate the script file for the specified episode; returns (script_dict, filename)."""
        episodes = project.get("episodes", [])
        ep_entry = next((e for e in episodes if e.get("episode") == episode), None)
        if ep_entry is None:
            raise FileNotFoundError(f"Episode {episode} does not exist")

        script_file = ep_entry.get("script_file", "")
        filename = Path(script_file).name
        script_data = self.pm.load_script(project_name, filename)
        return script_data, filename

    def _collect_video_clips(self, script: dict, project_dir: Path) -> list[dict[str, Any]]:
        """Extract the list of completed video clips from the script."""
        content_mode = script.get("content_mode", "narration")
        items = script.get("segments" if content_mode == "narration" else "scenes", [])
        id_field = "segment_id" if content_mode == "narration" else "scene_id"

        clips = []
        for item in items:
            assets = item.get("generated_assets") or {}
            video_clip = assets.get("video_clip")
            if not video_clip:
                continue

            abs_path = (project_dir / video_clip).resolve()
            if not abs_path.is_relative_to(project_dir.resolve()):
                logger.warning("video_clip path traversal detected; skipping: %s", video_clip)
                continue
            if not abs_path.exists():
                continue

            clips.append(
                {
                    "id": item.get(id_field, ""),
                    "duration_seconds": item.get("duration_seconds", 8),
                    "video_clip": video_clip,
                    "abs_path": abs_path,
                    "novel_text": item.get("novel_text", ""),
                }
            )

        return clips

    def _resolve_canvas_size(self, project: dict, first_video_path: Path | None = None) -> tuple[int, int]:
        """Determine canvas dimensions from the project's aspect_ratio; auto-detect from the first video when absent."""
        ar = project.get("aspect_ratio")
        aspect = ar if isinstance(ar, str) else (ar.get("video") if isinstance(ar, dict) else None)
        if aspect is None and first_video_path is not None:
            mat = VideoMaterial(str(first_video_path))
            aspect = "9:16" if mat.height > mat.width else "16:9"
        if aspect == "9:16":
            return 1080, 1920
        return 1920, 1080

    # ------------------------------------------------------------------
    # Internal methods: draft generation
    # ------------------------------------------------------------------

    def _generate_draft(
        self,
        *,
        draft_dir: Path,
        draft_name: str,
        clips: list[dict],
        width: int,
        height: int,
        content_mode: str,
    ) -> None:
        """Generate draft files in draft_dir using pyJianYingDraft."""
        draft_dir.parent.mkdir(parents=True, exist_ok=True)
        folder = draft.DraftFolder(str(draft_dir.parent))
        script_file = folder.create_draft(draft_name, width=width, height=height, allow_replace=True)

        # Video track
        script_file.add_track(TrackType.video)

        # Subtitle track (narration mode only)
        has_subtitle = content_mode == "narration"
        text_style: TextStyle | None = None
        text_border: TextBorder | None = None
        text_shadow: TextShadow | None = None
        subtitle_position: ClipSettings | None = None
        is_portrait = height > width
        if has_subtitle:
            script_file.add_track(TrackType.text, "Subtitles")
            text_style = TextStyle(
                size=12.0 if is_portrait else 8.0,
                color=(1.0, 1.0, 1.0),
                align=1,
                bold=True,
                auto_wrapping=True,
                max_line_width=0.82 if is_portrait else 0.6,
            )
            text_border = TextBorder(
                color=(0.0, 0.0, 0.0),
                width=30.0,
            )
            text_shadow = TextShadow(
                color=(0.0, 0.0, 0.0),
                alpha=0.7,
                diffuse=8.0,
                distance=3.0,
                angle=-45.0,
            )
            subtitle_position = ClipSettings(
                transform_y=-0.75 if is_portrait else -0.8,
            )

        # Add clips one by one
        offset_us = 0
        for clip in clips:
            # Pre-read actual video duration
            material = VideoMaterial(clip["local_path"])
            actual_duration_us = material.duration

            # Video segment
            video_seg = VideoSegment(
                material,
                trange(offset_us, actual_duration_us),
            )
            script_file.add_segment(video_seg)

            # Subtitle segment
            if has_subtitle and clip.get("novel_text"):
                text_seg = TextSegment(
                    text=clip["novel_text"],
                    timerange=trange(offset_us, actual_duration_us),
                    style=text_style,
                    border=text_border,
                    shadow=text_shadow,
                    clip_settings=subtitle_position,
                )
                script_file.add_segment(text_seg)

            offset_us += actual_duration_us

        script_file.save()

    def _replace_paths_in_draft(self, *, json_path: Path, tmp_prefix: str, target_prefix: str) -> None:
        """Safely replace temporary paths in draft_content.json via JSON serialization."""
        real = os.path.realpath(json_path)
        tmp = os.path.realpath(tempfile.gettempdir()) + os.sep
        if not real.startswith(tmp):
            raise ValueError(f"Path traversal detected; refusing to write: {real}")

        with open(real, encoding="utf-8") as f:  # noqa: PTH123
            data = json.load(f)

        def _walk(obj: Any) -> Any:
            if isinstance(obj, str) and tmp_prefix in obj:
                return obj.replace(tmp_prefix, target_prefix)
            if isinstance(obj, dict):
                return {k: _walk(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_walk(v) for v in obj]
            return obj

        data = _walk(data)
        with open(real, "w", encoding="utf-8") as f:  # noqa: PTH123
            json.dump(data, f, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def export_episode_draft(
        self,
        project_name: str,
        episode: int,
        draft_path: str,
        *,
        use_draft_info_name: bool = True,
    ) -> Path:
        """
        Export a JianyingDraft ZIP for the specified episode.

        Returns:
            ZIP file path (temporary file; caller is responsible for cleanup)

        Raises:
            FileNotFoundError: Project or script does not exist
            ValueError: No video clips available to export
        """
        project = self.pm.load_project(project_name)
        project_dir = self.pm.get_project_path(project_name)

        # 1. Locate the script
        script_data, _ = self._find_episode_script(project_name, project, episode)

        # 2. Collect completed videos
        content_mode = script_data.get("content_mode", "narration")
        clips = self._collect_video_clips(script_data, project_dir)
        if not clips:
            raise ValueError(f"Episode {episode} has no completed video clips; please generate videos first")

        # 3. Canvas dimensions (auto-detect from first video when project has no aspect_ratio)
        width, height = self._resolve_canvas_size(project, clips[0]["abs_path"])

        # 4. Create temp directory + copy assets to staging area
        raw_title = project.get("title", project_name)
        safe_title = raw_title.replace("/", "_").replace("\\", "_").replace("..", "_")
        draft_name = f"{safe_title}_episode{episode}"
        tmp_dir = Path(tempfile.mkdtemp(prefix="arcreel_jy_"))
        try:
            staging_dir = tmp_dir / "staging"
            staging_dir.mkdir()

            local_clips = []
            for clip in clips:
                src = clip["abs_path"]
                dst = staging_dir / src.name
                try:
                    dst.hardlink_to(src)
                except OSError:
                    shutil.copy2(src, dst)
                local_clips.append({**clip, "local_path": str(dst)})

            # 5. Generate draft (create_draft rebuilds draft_dir)
            draft_dir = tmp_dir / draft_name
            self._generate_draft(
                draft_dir=draft_dir,
                draft_name=draft_name,
                clips=local_clips,
                width=width,
                height=height,
                content_mode=content_mode,
            )

            # 6. Move assets into the draft directory
            assets_dir = draft_dir / "assets"
            assets_dir.mkdir(exist_ok=True)
            for clip in local_clips:
                src = Path(clip["local_path"])
                dst = assets_dir / src.name
                shutil.move(str(src), str(dst))

            # 7. Post-process paths: staging path → user's local path
            draft_content_path = draft_dir / "draft_content.json"
            self._replace_paths_in_draft(
                json_path=draft_content_path,
                tmp_prefix=str(staging_dir),
                target_prefix=f"{draft_path}/{draft_name}/assets",
            )

            # 8. Jianying 6+ uses draft_info.json; older versions use draft_content.json
            if use_draft_info_name:
                draft_content_path.rename(draft_dir / "draft_info.json")

            # 9. Package ZIP
            zip_path = tmp_dir / f"{draft_name}.zip"
            video_suffixes = {".mp4", ".webm", ".mov", ".avi", ".mkv"}
            with zipfile.ZipFile(zip_path, "w") as zf:
                for file in draft_dir.rglob("*"):
                    if file.is_file():
                        arcname = f"{draft_name}/{file.relative_to(draft_dir)}"
                        compress = zipfile.ZIP_STORED if file.suffix.lower() in video_suffixes else zipfile.ZIP_DEFLATED
                        zf.write(file, arcname, compress_type=compress)

            return zip_path
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
