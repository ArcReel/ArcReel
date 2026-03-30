"""剪映草稿导出服务

将 ArcReel 单集已生成的视频片段导出为剪映草稿 ZIP。
使用 pyJianYingDraft 库生成 draft_content.json，
后处理路径替换使草稿指向用户本地剪映目录。
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
    """剪映草稿导出服务"""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    # ------------------------------------------------------------------
    # 内部方法：数据提取
    # ------------------------------------------------------------------

    def _find_episode_script(self, project_name: str, project: dict, episode: int) -> tuple[dict, str]:
        """定位指定集的剧本文件，返回 (script_dict, filename)"""
        episodes = project.get("episodes", [])
        ep_entry = next((e for e in episodes if e.get("episode") == episode), None)
        if ep_entry is None:
            raise FileNotFoundError(f"第 {episode} 集不存在")

        script_file = ep_entry.get("script_file", "")
        filename = Path(script_file).name
        script_data = self.pm.load_script(project_name, filename)
        return script_data, filename

    def _collect_video_clips(self, script: dict, project_dir: Path) -> list[dict[str, Any]]:
        """从剧本中提取已完成视频的片段列表"""
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
                logger.warning("video_clip 路径越界，已跳过: %s", video_clip)
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
        """根据项目 aspect_ratio 确定画布尺寸，缺失时从首个视频自动检测"""
        aspect = project.get("aspect_ratio", {}).get("video")
        if aspect is None and first_video_path is not None:
            mat = VideoMaterial(str(first_video_path))
            aspect = "9:16" if mat.height > mat.width else "16:9"
        if aspect == "9:16":
            return 1080, 1920
        return 1920, 1080

    # ------------------------------------------------------------------
    # 内部方法：草稿生成
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
        """使用 pyJianYingDraft 在 draft_dir 中生成草稿文件"""
        draft_dir.parent.mkdir(parents=True, exist_ok=True)
        folder = draft.DraftFolder(str(draft_dir.parent))
        script_file = folder.create_draft(draft_name, width=width, height=height, allow_replace=True)

        # 视频轨
        script_file.add_track(TrackType.video)

        # 字幕轨（仅 narration 模式）
        has_subtitle = content_mode == "narration"
        text_style: TextStyle | None = None
        text_border: TextBorder | None = None
        text_shadow: TextShadow | None = None
        subtitle_position: ClipSettings | None = None
        is_portrait = height > width
        if has_subtitle:
            script_file.add_track(TrackType.text, "字幕")
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

        # 逐片段添加
        offset_us = 0
        for clip in clips:
            # 预读实际视频时长
            material = VideoMaterial(clip["local_path"])
            actual_duration_us = material.duration

            # 视频片段
            video_seg = VideoSegment(
                material,
                trange(offset_us, actual_duration_us),
            )
            script_file.add_segment(video_seg)

            # 字幕片段
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
        """JSON 安全地替换 draft_content.json 中的临时路径"""
        real = os.path.realpath(json_path)
        tmp = os.path.realpath(tempfile.gettempdir()) + os.sep
        if not real.startswith(tmp):
            raise ValueError(f"路径越界，拒绝写入: {real}")

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
    # 公开方法
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
        导出指定集的剪映草稿 ZIP。

        Returns:
            ZIP 文件路径（临时文件，调用方负责清理）

        Raises:
            FileNotFoundError: 项目或剧本不存在
            ValueError: 无可导出的视频片段
        """
        project = self.pm.load_project(project_name)
        project_dir = self.pm.get_project_path(project_name)

        # 1. 定位剧本
        script_data, _ = self._find_episode_script(project_name, project, episode)

        # 2. 收集已完成视频
        content_mode = script_data.get("content_mode", "narration")
        clips = self._collect_video_clips(script_data, project_dir)
        if not clips:
            raise ValueError(f"第 {episode} 集没有已完成的视频片段，请先生成视频")

        # 3. 画布尺寸（项目未设 aspect_ratio 时从首个视频自动检测）
        width, height = self._resolve_canvas_size(project, clips[0]["abs_path"])

        # 4. 创建临时目录 + 复制素材到暂存区
        raw_title = project.get("title", project_name)
        safe_title = raw_title.replace("/", "_").replace("\\", "_").replace("..", "_")
        draft_name = f"{safe_title}_第{episode}集"
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

            # 5. 生成草稿（create_draft 会重建 draft_dir）
            draft_dir = tmp_dir / draft_name
            self._generate_draft(
                draft_dir=draft_dir,
                draft_name=draft_name,
                clips=local_clips,
                width=width,
                height=height,
                content_mode=content_mode,
            )

            # 6. 将素材移入草稿目录
            assets_dir = draft_dir / "assets"
            assets_dir.mkdir(exist_ok=True)
            for clip in local_clips:
                src = Path(clip["local_path"])
                dst = assets_dir / src.name
                shutil.move(str(src), str(dst))

            # 7. 路径后处理：staging 路径 → 用户本地路径
            draft_content_path = draft_dir / "draft_content.json"
            self._replace_paths_in_draft(
                json_path=draft_content_path,
                tmp_prefix=str(staging_dir),
                target_prefix=f"{draft_path}/{draft_name}/assets",
            )

            # 8. 剪映 6+ 使用 draft_info.json，低版本使用 draft_content.json
            if use_draft_info_name:
                draft_content_path.rename(draft_dir / "draft_info.json")

            # 9. 打包 ZIP
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
