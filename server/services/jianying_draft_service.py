"""剪映草稿导出服务

将 ArcReel 单集已生成的视频片段导出为剪映草稿 ZIP。
使用 pyJianYingDraft 库生成 draft_content.json，
后处理路径替换使草稿指向用户本地剪映目录。
"""

import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft
from pyJianYingDraft import TextSegment, TextStyle, TrackType, VideoMaterial, VideoSegment, trange

from lib.project_manager import ProjectManager

logger = logging.getLogger(__name__)


class JianyingDraftService:
    """剪映草稿导出服务"""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    # ------------------------------------------------------------------
    # 内部方法：数据提取
    # ------------------------------------------------------------------

    def _find_episode_script(
        self, project_name: str, project: dict, episode: int
    ) -> tuple[dict, str]:
        """定位指定集的剧本文件，返回 (script_dict, filename)"""
        episodes = project.get("episodes", [])
        ep_entry = next(
            (e for e in episodes if e.get("episode") == episode), None
        )
        if ep_entry is None:
            raise FileNotFoundError(f"第 {episode} 集不存在")

        script_file = ep_entry.get("script_file", "")
        filename = Path(script_file).name
        script_data = self.pm.load_script(project_name, filename)
        return script_data, filename

    def _collect_video_clips(
        self, script: dict, project_dir: Path
    ) -> list[dict[str, Any]]:
        """从剧本中提取已完成视频的片段列表"""
        content_mode = script.get("content_mode", "narration")
        items = script.get(
            "segments" if content_mode == "narration" else "scenes", []
        )
        id_field = "segment_id" if content_mode == "narration" else "scene_id"

        clips = []
        for item in items:
            assets = item.get("generated_assets") or {}
            video_clip = assets.get("video_clip")
            if not video_clip:
                continue

            abs_path = project_dir / video_clip
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

    def _resolve_canvas_size(self, project: dict) -> tuple[int, int]:
        """根据项目 aspect_ratio 确定画布尺寸"""
        aspect = project.get("aspect_ratio", {}).get("video", "16:9")
        if aspect == "9:16":
            return 1080, 1920
        return 1920, 1080
