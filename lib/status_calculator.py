"""
状态和统计字段的实时计算器

提供读时计算的统计字段，避免存储冗余数据。
配合 ProjectManager 使用，在 API 响应时注入计算字段。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class StatusCalculator:
    """状态和统计字段的实时计算器"""

    def __init__(self, project_manager):
        """
        初始化状态计算器

        Args:
            project_manager: ProjectManager 实例
        """
        self.pm = project_manager

    @classmethod
    def _select_content_mode_and_items(cls, script: dict) -> tuple[str, list[dict]]:
        content_mode = script.get("content_mode")
        if content_mode == "reference_video" and isinstance(script.get("video_units"), list):
            return "reference_video", script.get("video_units", [])
        if content_mode in {"narration", "drama"}:
            if content_mode == "narration" and isinstance(script.get("segments"), list):
                return "narration", script.get("segments", [])
            if content_mode == "drama" and isinstance(script.get("scenes"), list):
                return "drama", script.get("scenes", [])

        if isinstance(script.get("video_units"), list):
            return "reference_video", script.get("video_units", [])
        if isinstance(script.get("segments"), list):
            return "narration", script.get("segments", [])
        if isinstance(script.get("scenes"), list):
            return "drama", script.get("scenes", [])

        return ("narration" if content_mode not in {"narration", "drama"} else content_mode), []

    def calculate_episode_stats(self, project_name: str, script: dict) -> dict:
        """计算单集的统计信息 — 按 content_mode 分派。"""
        content_mode, items = self._select_content_mode_and_items(script)

        if content_mode == "reference_video":
            return self._calculate_reference_video_stats(items)

        default_duration = 4 if content_mode == "narration" else 8
        storyboard_done = sum(1 for i in items if i.get("generated_assets", {}).get("storyboard_image"))
        video_done = sum(1 for i in items if i.get("generated_assets", {}).get("video_clip"))
        total = len(items)

        if video_done == total and total > 0:
            status = "completed"
        elif storyboard_done > 0 or video_done > 0:
            status = "in_production"
        else:
            status = "draft"

        return {
            "scenes_count": total,
            "status": status,
            "duration_seconds": sum(i.get("duration_seconds", default_duration) for i in items),
            "storyboards": {"total": total, "completed": storyboard_done},
            "videos": {"total": total, "completed": video_done},
        }

    @staticmethod
    def _calculate_reference_video_stats(units: list[dict]) -> dict:
        """Reference-video scripts are scored by video_units[].generated_assets.video_clip."""
        total = len(units)
        video_done = sum(1 for u in units if u.get("generated_assets", {}).get("video_clip"))

        if total == 0:
            status = "draft"
        elif video_done == total:
            status = "completed"
        elif video_done > 0:
            status = "in_production"
        else:
            status = "draft"

        return {
            "scenes_count": total,
            "units_count": total,
            "status": status,
            "duration_seconds": sum(u.get("duration_seconds", 0) for u in units),
            "storyboards": {"total": total, "completed": 0},
            "videos": {"total": total, "completed": video_done},
        }

    @staticmethod
    def _safe_exists(base: Path, rel_path: str) -> bool:
        """检查 rel_path 是否为 base 目录内的合法相对路径且文件存在（防止路径穿越）"""
        if not rel_path:
            return False
        try:
            full = (base / rel_path).resolve()
            return full.is_relative_to(base.resolve()) and full.exists()
        except (OSError, ValueError):
            return False

    def _load_episode_script(
        self, project_name: str, episode_num: int, script_file: str, *, content_mode: str = "narration"
    ) -> tuple:
        """加载单集剧本，返回 (script_status, script|None)，避免重复读取文件。
        script_status: 'generated' | 'segmented' | 'none'
        """
        try:
            script = self.pm.load_script(project_name, script_file)
            return "generated", script
        except FileNotFoundError:
            project_dir = self.pm.get_project_path(project_name)
            try:
                safe_num = int(episode_num)
            except (ValueError, TypeError):
                return "none", None
            draft_filename = "step1_segments.md" if content_mode == "narration" else "step1_normalized_script.md"
            draft_file = project_dir / f"drafts/episode_{safe_num}/{draft_filename}"
            return ("segmented" if draft_file.exists() else "none"), None
        except ValueError as e:
            logger.warning(
                "剧本 JSON 损坏或路径无效，跳过状态计算 project=%s file=%s: %s",
                project_name,
                script_file,
                e,
            )
            return "generated", None

    def calculate_current_phase(self, project: dict, episodes_stats: list[dict]) -> str:
        """根据项目和集状态推断当前阶段"""
        if not project.get("overview"):
            return "setup"
        if not episodes_stats:
            return "worldbuilding"
        any_generated = any(s["script_status"] == "generated" for s in episodes_stats)
        all_generated = all(s["script_status"] == "generated" for s in episodes_stats)
        if not any_generated:
            return "worldbuilding"
        if not all_generated:
            return "scripting"
        all_completed = all(s["status"] == "completed" for s in episodes_stats)
        return "completed" if all_completed else "production"

    def _calculate_phase_progress(self, project: dict, phase: str, episodes_stats: list[dict]) -> float:
        """计算当前阶段完成率 0.0–1.0"""
        if phase == "setup":
            return 0.0
        if phase == "worldbuilding":
            return 0.0
        if phase == "scripting":
            total = len(episodes_stats)
            if total == 0:
                return 0.0
            done = sum(1 for s in episodes_stats if s["script_status"] == "generated")
            return done / total
        if phase == "production":
            total_videos = sum(s.get("videos", {}).get("total", 0) for s in episodes_stats)
            done_videos = sum(s.get("videos", {}).get("completed", 0) for s in episodes_stats)
            return done_videos / total_videos if total_videos > 0 else 0.0
        return 1.0  # completed

    @staticmethod
    def _make_fallback_ep_stats(script_status: str) -> dict:
        """构造未生成/无剧本集数的默认统计字典。"""
        return {
            "script_status": script_status,
            "status": "draft",
            "storyboards": {"total": 0, "completed": 0},
            "videos": {"total": 0, "completed": 0},
            "scenes_count": 0,
            "duration_seconds": 0,
        }

    def _build_episodes_stats(self, project_name: str, project: dict) -> list[dict]:
        """遍历所有集数，加载剧本并计算每集统计。"""
        content_mode = project.get("content_mode", "narration")
        episodes_stats = []
        for ep in project.get("episodes", []):
            script_file = ep.get("script_file", "")
            episode_num = ep.get("episode", 0)

            if script_file:
                script_status, script = self._load_episode_script(
                    project_name, episode_num, script_file, content_mode=content_mode
                )
            else:
                script_status, script = "none", None

            if script_status == "generated" and script is not None:
                ep_stats = self.calculate_episode_stats(project_name, script)
                if ep_stats["status"] == "draft":
                    ep_stats["status"] = "scripted"
                ep_stats["script_status"] = "generated"
            else:
                ep_stats = self._make_fallback_ep_stats(script_status)
            episodes_stats.append(ep_stats)
        return episodes_stats

    def calculate_project_status(
        self, project_name: str, project: dict, *, _preloaded_episodes_stats: list[dict] | None = None
    ) -> dict:
        """
        计算项目整体状态（用于列表 API）。

        Args:
            _preloaded_episodes_stats: 若已由 enrich_project 预先计算，直接传入以避免重复 I/O。

        Returns:
            ProjectStatus 字典：current_phase, phase_progress, characters, scenes, props, episodes_summary
        """
        project_dir = self.pm.get_project_path(project_name)

        # 角色统计
        chars = project.get("characters", {})
        chars_total = len(chars)
        chars_done = sum(1 for c in chars.values() if self._safe_exists(project_dir, c.get("character_sheet", "")))

        # 场景统计
        scenes = project.get("scenes", {})
        scenes_total = len(scenes)
        scenes_done = sum(1 for s in scenes.values() if self._safe_exists(project_dir, s.get("scene_sheet", "")))

        # 道具统计
        props = project.get("props", {})
        props_total = len(props)
        props_done = sum(1 for p in props.values() if self._safe_exists(project_dir, p.get("prop_sheet", "")))

        # 每集状态：优先使用预加载数据，否则自行加载
        if _preloaded_episodes_stats is not None:
            episodes_stats = _preloaded_episodes_stats
        else:
            episodes_stats = self._build_episodes_stats(project_name, project)

        phase = self.calculate_current_phase(project, episodes_stats)
        phase_progress = self._calculate_phase_progress(project, phase, episodes_stats)
        if phase == "worldbuilding":
            total_assets = chars_total + scenes_total + props_total
            completed_assets = chars_done + scenes_done + props_done
            phase_progress = completed_assets / total_assets if total_assets > 0 else 0.0

        return {
            "current_phase": phase,
            "phase_progress": phase_progress,
            "characters": {"total": chars_total, "completed": chars_done},
            "scenes": {"total": scenes_total, "completed": scenes_done},
            "props": {"total": props_total, "completed": props_done},
            "episodes_summary": {
                "total": len(episodes_stats),
                "scripted": sum(1 for s in episodes_stats if s["script_status"] == "generated"),
                "in_production": sum(1 for s in episodes_stats if s["status"] == "in_production"),
                "completed": sum(1 for s in episodes_stats if s["status"] == "completed"),
            },
        }

    def enrich_project(self, project_name: str, project: dict) -> dict:
        """
        为项目数据注入所有计算字段（用于详情 API）。
        不修改原始 JSON 文件，仅用于 API 响应。
        """
        # 计算每集明细（注入到 episode 对象）并收集统计
        episodes_stats = self._build_episodes_stats(project_name, project)

        for ep, ep_stats in zip(project.get("episodes", []), episodes_stats):
            ep.update(ep_stats)

        # 传入预加载的 episodes_stats，避免 calculate_project_status 重复加载剧本
        project["status"] = self.calculate_project_status(
            project_name, project, _preloaded_episodes_stats=episodes_stats
        )
        return project

    def enrich_script(self, script: dict) -> dict:
        """
        为剧本数据注入计算字段

        不会修改原始 JSON 文件，仅用于 API 响应。

        Args:
            script: 原始剧本数据

        Returns:
            注入计算字段后的剧本数据
        """
        content_mode, items = self._select_content_mode_and_items(script)
        default_duration = 4 if content_mode == "narration" else 8

        total_duration = sum(i.get("duration_seconds", default_duration) for i in items)

        # 注入 metadata 计算字段
        if "metadata" not in script:
            script["metadata"] = {}

        script["metadata"]["total_scenes"] = len(items)
        script["metadata"]["estimated_duration_seconds"] = total_duration
        script["duration_seconds"] = total_duration  # 读时注入，与 metadata 保持同步

        # 聚合 characters_in_episode / scenes_in_episode / props_in_episode（仅用于 API 响应，不存储）
        chars_set = set()
        scenes_set = set()
        props_set = set()

        if content_mode == "reference_video":
            for item in items:
                for ref in item.get("references", []):
                    kind = ref.get("type")
                    name = ref.get("name")
                    if not name:
                        continue
                    if kind == "character":
                        chars_set.add(name)
                    elif kind == "scene":
                        scenes_set.add(name)
                    elif kind == "prop":
                        props_set.add(name)
        else:
            char_field = "characters_in_segment" if content_mode == "narration" else "characters_in_scene"
            for item in items:
                chars_set.update(item.get(char_field, []))
                scenes_set.update(item.get("scenes", []))
                props_set.update(item.get("props", []))

        script["characters_in_episode"] = sorted(chars_set)
        script["scenes_in_episode"] = sorted(scenes_set)
        script["props_in_episode"] = sorted(props_set)

        return script
