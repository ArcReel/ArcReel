"""
项目文件管理器

管理视频项目的目录结构、分镜剧本读写、状态追踪。
"""

import fcntl
import json
import logging
import os
import re
import secrets
import unicodedata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lib.project_change_hints import emit_project_change_hint

logger = logging.getLogger(__name__)

PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
PROJECT_SLUG_SANITIZER = re.compile(r"[^a-zA-Z0-9]+")

# ==================== 数据模型 ====================


class ProjectOverview(BaseModel):
    """项目概述数据模型，用于 Gemini Structured Outputs"""

    synopsis: str = Field(description="故事梗概，200-300字，概括主线剧情")
    genre: str = Field(description="题材类型，如：古装宫斗、现代悬疑、玄幻修仙")
    theme: str = Field(description="核心主题，如：复仇与救赎、成长与蜕变")
    world_setting: str = Field(description="时代背景和世界观设定，100-200字")


class ProjectManager:
    """视频项目管理器"""

    # 项目子目录结构
    SUBDIRS = [
        "source",
        "scripts",
        "drafts",
        "characters",
        "clues",
        "storyboards",
        "videos",
        "output",
    ]

    # 项目元数据文件名
    PROJECT_FILE = "project.json"

    @staticmethod
    def normalize_project_name(name: str) -> str:
        """Validate and normalize a project identifier."""
        normalized = str(name).strip()
        if not normalized:
            raise ValueError("项目标识不能为空")
        if not PROJECT_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("项目标识仅允许英文字母、数字和中划线")
        return normalized

    @staticmethod
    def _slugify_project_title(title: str) -> str:
        """Build a filesystem-safe slug prefix from the project title."""
        ascii_text = unicodedata.normalize("NFKD", str(title).strip()).encode("ascii", "ignore").decode("ascii")
        slug = PROJECT_SLUG_SANITIZER.sub("-", ascii_text).strip("-_").lower()
        return slug[:24] or "project"

    def generate_project_name(self, title: str | None = None) -> str:
        """Generate a unique internal project identifier."""
        prefix = self._slugify_project_title(title or "")
        while True:
            candidate = f"{prefix}-{secrets.token_hex(4)}"
            if not (self.projects_root / candidate).exists():
                return candidate

    @classmethod
    def from_cwd(cls) -> tuple["ProjectManager", str]:
        """从当前工作目录推断 ProjectManager 和项目名称。

        假定 cwd 为 ``projects/{project_name}/`` 格式。
        返回 ``(ProjectManager, project_name)`` 元组。
        """
        cwd = Path.cwd().resolve()
        project_name = cwd.name
        projects_root = cwd.parent
        pm = cls(projects_root)
        if not (projects_root / project_name / cls.PROJECT_FILE).exists():
            raise FileNotFoundError(f"当前目录不是有效的项目目录: {cwd}")
        return pm, project_name

    def __init__(self, projects_root: str | None = None):
        """
        初始化项目管理器

        Args:
            projects_root: 项目根目录，默认为当前目录下的 projects/
        """
        if projects_root is None:
            # 尝试从环境变量或默认路径获取
            projects_root = os.environ.get("AI_ANIME_PROJECTS", "projects")

        self.projects_root = Path(projects_root)
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def list_projects(self) -> list[str]:
        """列出所有项目"""
        return [d.name for d in self.projects_root.iterdir() if d.is_dir() and not d.name.startswith(".")]

    def create_project(self, name: str) -> Path:
        """
        创建新项目

        Args:
            name: 项目标识（全局唯一，用于 URL 和文件系统）

        Returns:
            项目目录路径
        """
        name = self.normalize_project_name(name)
        project_dir = self.projects_root / name

        if project_dir.exists():
            raise FileExistsError(f"项目 '{name}' 已存在")

        # 创建所有子目录
        for subdir in self.SUBDIRS:
            (project_dir / subdir).mkdir(parents=True, exist_ok=True)

        self.repair_claude_symlink(project_dir)

        return project_dir

    def repair_claude_symlink(self, project_dir: Path) -> dict:
        """修复项目目录的 .claude 和 CLAUDE.md 软连接。

        对每条软连接执行：
        - 损坏（is_symlink but not exists）→ 删除并重建
        - 缺失（not exists and not is_symlink）→ 创建
        - 正常（exists）→ 跳过

        Returns:
            {"created": int, "repaired": int, "skipped": int, "errors": int}
        """
        project_root = self.projects_root.parent
        profile_dir = project_root / "agent_runtime_profile"

        SYMLINKS = {
            ".claude": profile_dir / ".claude",
            "CLAUDE.md": profile_dir / "CLAUDE.md",
        }
        REL_TARGETS = {
            ".claude": Path("../../agent_runtime_profile/.claude"),
            "CLAUDE.md": Path("../../agent_runtime_profile/CLAUDE.md"),
        }

        stats = {"created": 0, "repaired": 0, "skipped": 0, "errors": 0}
        for name, target_source in SYMLINKS.items():
            if not target_source.exists():
                continue
            symlink_path = project_dir / name
            if symlink_path.is_symlink() and not symlink_path.exists():
                # 损坏的软连接
                try:
                    symlink_path.unlink()
                    symlink_path.symlink_to(REL_TARGETS[name])
                    stats["repaired"] += 1
                except OSError as e:
                    logger.warning("无法修复项目 %s 的 %s 符号链接: %s", project_dir.name, name, e)
                    stats["errors"] += 1
            elif not symlink_path.exists() and not symlink_path.is_symlink():
                # 缺失
                try:
                    symlink_path.symlink_to(REL_TARGETS[name])
                    stats["created"] += 1
                except OSError as e:
                    logger.warning("无法为项目 %s 创建 %s 符号链接: %s", project_dir.name, name, e)
                    stats["errors"] += 1
            else:
                stats["skipped"] += 1
        return stats

    def repair_all_symlinks(self) -> dict:
        """扫描所有项目目录，修复软连接。

        Returns:
            {"created": int, "repaired": int, "skipped": int, "errors": int}
        """
        totals = {"created": 0, "repaired": 0, "skipped": 0, "errors": 0}
        if not self.projects_root.exists():
            return totals
        for project_dir in sorted(self.projects_root.iterdir()):
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue
            try:
                result = self.repair_claude_symlink(project_dir)
                for key in ("created", "repaired", "skipped", "errors"):
                    totals[key] += result.get(key, 0)
            except Exception as e:
                logger.warning("修复项目 %s 软连接时出错: %s", project_dir.name, e)
                totals["errors"] += 1
        return totals

    def get_project_path(self, name: str) -> Path:
        """获取项目路径（含路径遍历防护）"""
        name = self.normalize_project_name(name)
        real = os.path.realpath(self.projects_root / name)
        base = os.path.realpath(self.projects_root) + os.sep
        if not real.startswith(base):
            raise ValueError(f"非法项目名称: '{name}'")
        project_dir = Path(real)
        if not project_dir.exists():
            raise FileNotFoundError(f"项目 '{name}' 不存在")
        return project_dir

    @staticmethod
    def _safe_subpath(base_dir: Path, filename: str) -> str:
        """校验 filename 拼接后不逃出 base_dir，返回 realpath 字符串。"""
        real = os.path.realpath(base_dir / filename)
        bound = os.path.realpath(base_dir) + os.sep
        if not real.startswith(bound):
            raise ValueError(f"非法文件名: '{filename}'")
        return real

    def get_project_status(self, name: str) -> dict[str, Any]:
        """
        获取项目状态

        Returns:
            包含各阶段完成情况的字典
        """
        project_dir = self.get_project_path(name)

        status = {
            "name": name,
            "path": str(project_dir),
            "source_files": [],
            "scripts": [],
            "characters": [],
            "clues": [],
            "storyboards": [],
            "videos": [],
            "outputs": [],
            "current_stage": "empty",
        }

        # 检查各目录内容
        for subdir in self.SUBDIRS:
            subdir_path = project_dir / subdir
            if subdir_path.exists():
                files = list(subdir_path.glob("*"))
                if subdir == "source":
                    status["source_files"] = [f.name for f in files if f.is_file()]
                elif subdir == "scripts":
                    status["scripts"] = [f.name for f in files if f.suffix == ".json"]
                elif subdir == "characters":
                    status["characters"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
                elif subdir == "clues":
                    status["clues"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
                elif subdir == "storyboards":
                    status["storyboards"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
                elif subdir == "videos":
                    status["videos"] = [f.name for f in files if f.suffix in [".mp4", ".webm"]]
                elif subdir == "output":
                    status["outputs"] = [f.name for f in files if f.suffix in [".mp4", ".webm"]]

        # 确定当前阶段
        if status["outputs"]:
            status["current_stage"] = "completed"
        elif status["videos"]:
            status["current_stage"] = "videos_generated"
        elif status["storyboards"]:
            status["current_stage"] = "storyboards_generated"
        elif status["characters"]:
            status["current_stage"] = "characters_generated"
        elif status["scripts"]:
            status["current_stage"] = "script_created"
        elif status["source_files"]:
            status["current_stage"] = "source_ready"
        else:
            status["current_stage"] = "empty"

        return status

    # ==================== 分镜剧本操作 ====================

    def create_script(self, project_name: str, title: str, chapter: str, source_file: str) -> dict:
        """
        创建新的分镜剧本模板

        Args:
            project_name: 项目名称
            title: 小说标题
            chapter: 章节名称
            source_file: 源文件路径

        Returns:
            剧本字典
        """
        script = {
            "novel": {"title": title, "chapter": chapter, "source_file": source_file},
            "scenes": [],
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "total_scenes": 0,
                "estimated_duration_seconds": 0,
                "status": "draft",
            },
        }

        return script

    def save_script(self, project_name: str, script: dict, filename: str | None = None) -> Path:
        """
        保存分镜剧本

        Args:
            project_name: 项目名称
            script: 剧本字典
            filename: 可选的文件名，默认使用章节名

        Returns:
            保存的文件路径
        """
        project_dir = self.get_project_path(project_name)
        scripts_dir = project_dir / "scripts"

        if filename is not None and filename.startswith("scripts/"):
            filename = filename[len("scripts/") :]

        if filename is None:
            chapter = script["novel"].get("chapter", "chapter_01")
            filename = f"{chapter.replace(' ', '_')}_script.json"

        # 更新元数据（兼容旧脚本：可能缺少 metadata，或 narration 使用 segments）
        now = datetime.now().isoformat()
        metadata = script.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            script["metadata"] = metadata
        metadata.setdefault("created_at", now)
        metadata.setdefault("status", "draft")
        metadata["updated_at"] = now

        scenes = script.get("scenes", [])
        if not isinstance(scenes, list):
            scenes = []
        segments = script.get("segments", [])
        if not isinstance(segments, list):
            segments = []

        content_mode = script.get("content_mode", "narration")
        if content_mode == "narration" and segments:
            items = segments
            items_type = "segments"
        elif scenes:
            items = scenes
            items_type = "scenes"
        else:
            items = segments
            items_type = "segments"

        metadata["total_scenes"] = len(items)

        # 计算总时长：按当前选中的数据结构决定回退值，避免 content_mode 缺失时误判
        default_duration = 4 if items_type == "segments" else 8
        total_duration = sum(item.get("duration_seconds", default_duration) for item in items)
        metadata["estimated_duration_seconds"] = total_duration

        # 保存文件（含路径遍历防护）
        real = self._safe_subpath(scripts_dir, filename)
        with open(real, "w", encoding="utf-8") as f:  # noqa: PTH123
            json.dump(script, f, ensure_ascii=False, indent=2)
        output_path = Path(real)

        emit_project_change_hint(
            project_name,
            changed_paths=[f"scripts/{output_path.name}"],
        )

        # 自动同步到 project.json
        if self.project_exists(project_name) and isinstance(script.get("episode"), int):
            self.sync_episode_from_script(project_name, filename)

        return output_path

    def sync_episode_from_script(self, project_name: str, script_filename: str) -> dict:
        """
        从剧本文件同步集数信息到 project.json

        Agent 写入剧本后必须调用此方法以确保 WebUI 能正确显示剧集列表。

        Args:
            project_name: 项目名称
            script_filename: 剧本文件名（如 episode_1.json）

        Returns:
            更新后的 project 字典
        """
        script = self.load_script(project_name, script_filename)
        project = self.load_project(project_name)

        episode_num = script.get("episode", 1)
        episode_title = script.get("title", "")
        script_file = f"scripts/{script_filename}"

        # 查找或创建 episode 条目
        episodes = project.setdefault("episodes", [])
        episode_entry = next((ep for ep in episodes if ep["episode"] == episode_num), None)

        if episode_entry is None:
            episode_entry = {"episode": episode_num}
            episodes.append(episode_entry)

        # 同步核心元数据（不包含统计字段，统计字段由 StatusCalculator 读时计算）
        episode_entry["title"] = episode_title
        episode_entry["script_file"] = script_file

        # 排序并保存
        episodes.sort(key=lambda x: x["episode"])
        self.save_project(project_name, project)

        logger.info("已同步剧集信息: Episode %d - %s", episode_num, episode_title)
        return project

    def load_script(self, project_name: str, filename: str) -> dict:
        """
        加载分镜剧本

        Args:
            project_name: 项目名称
            filename: 剧本文件名

        Returns:
            剧本字典
        """
        project_dir = self.get_project_path(project_name)
        if filename.startswith("scripts/"):
            filename = filename[len("scripts/") :]
        real = self._safe_subpath(project_dir / "scripts", filename)

        if not os.path.exists(real):
            raise FileNotFoundError(f"剧本文件不存在: {real}")

        with open(real, encoding="utf-8") as f:  # noqa: PTH123
            return json.load(f)

    def list_scripts(self, project_name: str) -> list[str]:
        """列出项目中的所有剧本"""
        project_dir = self.get_project_path(project_name)
        scripts_dir = project_dir / "scripts"
        return [f.name for f in scripts_dir.glob("*.json")]

    # ==================== 角色管理 ====================

    def update_character_sheet(self, project_name: str, script_filename: str, name: str, sheet_path: str) -> dict:
        """更新角色设计图路径"""
        script = self.load_script(project_name, script_filename)

        if name not in script["characters"]:
            raise KeyError(f"角色 '{name}' 不存在")

        script["characters"][name]["character_sheet"] = sheet_path
        self.save_script(project_name, script, script_filename)
        return script

    # ==================== 数据结构标准化 ====================

    @staticmethod
    def create_generated_assets(content_mode: str = "narration") -> dict:
        """
        创建标准的 generated_assets 结构

        Args:
            content_mode: 内容模式（'narration' 或 'drama'）

        Returns:
            标准的 generated_assets 字典
        """
        return {
            "storyboard_image": None,
            "video_clip": None,
            "video_thumbnail": None,
            "video_uri": None,
            "status": "pending",
        }

    @staticmethod
    def create_scene_template(scene_id: str, episode: int = 1, duration_seconds: int = 8) -> dict:
        """
        创建标准场景对象模板

        Args:
            scene_id: 场景 ID（如 "E1S01"）
            episode: 集数编号
            duration_seconds: 场景时长（秒）

        Returns:
            标准的场景字典
        """
        return {
            "scene_id": scene_id,
            "episode": episode,
            "title": "",
            "scene_type": "剧情",
            "duration_seconds": duration_seconds,
            "segment_break": False,
            "characters_in_scene": [],
            "clues_in_scene": [],
            "visual": {
                "description": "",
                "shot_type": "medium shot",
                "camera_movement": "static",
                "lighting": "",
                "mood": "",
            },
            "action": "",
            "dialogue": {"speaker": "", "text": "", "emotion": "neutral"},
            "audio": {"dialogue": [], "narration": "", "sound_effects": []},
            "transition_to_next": "cut",
            "generated_assets": ProjectManager.create_generated_assets(),
        }

    def normalize_scene(self, scene: dict, episode: int = 1) -> dict:
        """
        补全单个场景中缺失的字段

        Args:
            scene: 场景字典
            episode: 集数编号（用于补全 episode 字段）

        Returns:
            补全后的场景字典
        """
        template = self.create_scene_template(
            scene_id=scene.get("scene_id", "000"),
            episode=episode,
            duration_seconds=scene.get("duration_seconds", 8),
        )

        # 合并 visual 字段
        if "visual" not in scene:
            scene["visual"] = template["visual"]
        else:
            for key in template["visual"]:
                if key not in scene["visual"]:
                    scene["visual"][key] = template["visual"][key]

        # 合并 audio 字段
        if "audio" not in scene:
            scene["audio"] = template["audio"]
        else:
            for key in template["audio"]:
                if key not in scene["audio"]:
                    scene["audio"][key] = template["audio"][key]

        # 补全 generated_assets 字段
        if "generated_assets" not in scene:
            scene["generated_assets"] = self.create_generated_assets()
        else:
            assets_template = self.create_generated_assets()
            for key in assets_template:
                if key not in scene["generated_assets"]:
                    scene["generated_assets"][key] = assets_template[key]

        # 补全其他顶层字段
        top_level_defaults = {
            "episode": episode,
            "title": "",
            "scene_type": "剧情",
            "segment_break": False,
            "characters_in_scene": [],
            "clues_in_scene": [],
            "action": "",
            "dialogue": template["dialogue"],
            "transition_to_next": "cut",
        }

        for key, default_value in top_level_defaults.items():
            if key not in scene:
                scene[key] = default_value

        # 更新状态
        self.update_scene_status(scene)

        return scene

    def update_scene_status(self, scene: dict) -> str:
        """
        根据 generated_assets 内容更新并返回场景状态

        状态值:
        - pending: 未开始
        - storyboard_ready: 分镜图完成
        - completed: 视频完成

        Args:
            scene: 场景字典

        Returns:
            更新后的状态值
        """
        assets = scene.get("generated_assets", {})

        has_image = bool(assets.get("storyboard_image"))
        has_video = bool(assets.get("video_clip"))

        if has_video:
            status = "completed"
        elif has_image:
            status = "storyboard_ready"
        else:
            status = "pending"

        assets["status"] = status
        return status

    def normalize_script(self, project_name: str, script_filename: str, save: bool = True) -> dict:
        """
        补全现有 script.json 中缺失的字段

        Args:
            project_name: 项目名称
            script_filename: 剧本文件名
            save: 是否保存修改后的剧本

        Returns:
            补全后的剧本字典
        """
        import re

        script = self.load_script(project_name, script_filename)

        # 从文件名或现有数据推断 episode
        episode = script.get("episode", 1)
        if not episode:
            match = re.search(r"episode[_\s]*(\d+)", script_filename, re.IGNORECASE)
            if match:
                episode = int(match.group(1))
            else:
                episode = 1

        # 补全顶层字段
        script_defaults = {
            "episode": episode,
            "title": script.get("novel", {}).get("chapter", ""),
            "duration_seconds": 0,
            "summary": "",
        }

        for key, default_value in script_defaults.items():
            if key not in script:
                script[key] = default_value

        # 确保必要的顶层结构存在
        if "novel" not in script:
            script["novel"] = {"title": "", "chapter": "", "source_file": ""}

        # 处理旧格式：如果有 characters 对象，同步到 project.json
        if "characters" in script and isinstance(script["characters"], dict) and script["characters"]:
            logger.warning("检测到旧格式 characters 对象，自动同步到 project.json")
            self.sync_characters_from_script(project_name, script_filename)
            # sync_characters_from_script 会重新加载和保存 script，所以需要重新加载
            script = self.load_script(project_name, script_filename)

        # 处理旧格式：如果有 clues 对象，同步到 project.json
        if "clues" in script and isinstance(script["clues"], dict) and script["clues"]:
            logger.warning("检测到旧格式 clues 对象，自动同步到 project.json")
            self.sync_clues_from_script(project_name, script_filename)
            script = self.load_script(project_name, script_filename)

        # 注意：characters_in_episode 和 clues_in_episode 已改为读时计算
        # 不再在 normalize_script 中创建这些字段

        if "scenes" not in script:
            script["scenes"] = []

        if "metadata" not in script:
            script["metadata"] = {
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "total_scenes": 0,
                "estimated_duration_seconds": 0,
                "status": "draft",
            }

        # 规范化每个场景
        for scene in script["scenes"]:
            self.normalize_scene(scene, episode)

        # 更新统计信息
        script["metadata"]["total_scenes"] = len(script["scenes"])
        script["metadata"]["estimated_duration_seconds"] = sum(s.get("duration_seconds", 8) for s in script["scenes"])
        script["duration_seconds"] = script["metadata"]["estimated_duration_seconds"]

        if save:
            self.save_script(project_name, script, script_filename)
            logger.info("剧本已规范化并保存: %s", script_filename)

        return script

    # ==================== 场景管理 ====================

    def add_scene(self, project_name: str, script_filename: str, scene: dict) -> dict:
        """
        向剧本添加场景

        Args:
            project_name: 项目名称
            script_filename: 剧本文件名
            scene: 场景字典

        Returns:
            更新后的剧本
        """
        script = self.load_script(project_name, script_filename)

        # 自动生成场景 ID
        existing_ids = [s["scene_id"] for s in script["scenes"]]
        next_id = f"{len(existing_ids) + 1:03d}"
        scene["scene_id"] = next_id

        # 确保有 generated_assets 字段
        if "generated_assets" not in scene:
            scene["generated_assets"] = {
                "storyboard_image": None,
                "video_clip": None,
                "status": "pending",
            }

        script["scenes"].append(scene)
        self.save_script(project_name, script, script_filename)
        return script

    def update_scene_asset(
        self,
        project_name: str,
        script_filename: str,
        scene_id: str,
        asset_type: str,
        asset_path: str,
    ) -> dict:
        """
        更新场景的生成资源路径

        Args:
            project_name: 项目名称
            script_filename: 剧本文件名
            scene_id: 场景/片段 ID
            asset_type: 资源类型 ('storyboard_image' 或 'video_clip')
            asset_path: 资源路径

        Returns:
            更新后的剧本
        """
        script = self.load_script(project_name, script_filename)

        # 根据内容模式选择正确的数据结构
        content_mode = script.get("content_mode", "narration")
        if content_mode == "narration" and "segments" in script:
            items = script["segments"]
            id_field = "segment_id"
        else:
            items = script.get("scenes", [])
            id_field = "scene_id"

        for item in items:
            if str(item.get(id_field)) == str(scene_id):
                assets = item.get("generated_assets")
                if not isinstance(assets, dict):
                    assets = {}
                    item["generated_assets"] = assets

                assets_template = self.create_generated_assets(content_mode)
                for key, default_value in assets_template.items():
                    if key not in assets:
                        assets[key] = default_value

                assets[asset_type] = asset_path

                # 使用 update_scene_status 更新状态
                self.update_scene_status(item)

                self.save_script(project_name, script, script_filename)
                return script

        raise KeyError(f"场景 '{scene_id}' 不存在")

    def get_pending_scenes(self, project_name: str, script_filename: str, asset_type: str) -> list[dict]:
        """
        获取待处理的场景/片段列表

        Args:
            project_name: 项目名称
            script_filename: 剧本文件名
            asset_type: 资源类型

        Returns:
            待处理场景/片段列表
        """
        script = self.load_script(project_name, script_filename)

        # 根据内容模式选择正确的数据结构
        content_mode = script.get("content_mode", "narration")
        if content_mode == "narration" and "segments" in script:
            items = script["segments"]
        else:
            items = script.get("scenes", [])

        return [item for item in items if not item["generated_assets"].get(asset_type)]

    # ==================== 文件路径工具 ====================

    def get_source_path(self, project_name: str, filename: str) -> Path:
        """获取源文件路径"""
        return self.get_project_path(project_name) / "source" / filename

    def get_character_path(self, project_name: str, filename: str) -> Path:
        """获取角色设计图路径"""
        return self.get_project_path(project_name) / "characters" / filename

    def get_storyboard_path(self, project_name: str, filename: str) -> Path:
        """获取分镜图片路径"""
        return self.get_project_path(project_name) / "storyboards" / filename

    def get_video_path(self, project_name: str, filename: str) -> Path:
        """获取视频路径"""
        return self.get_project_path(project_name) / "videos" / filename

    def get_output_path(self, project_name: str, filename: str) -> Path:
        """获取输出路径"""
        return self.get_project_path(project_name) / "output" / filename

    def get_scenes_needing_storyboard(self, project_name: str, script_filename: str) -> list[dict]:
        """
        获取需要生成分镜图的场景/片段列表（两种模式统一逻辑）

        Args:
            project_name: 项目名称
            script_filename: 剧本文件名

        Returns:
            需要生成分镜图的场景/片段列表
        """
        script = self.load_script(project_name, script_filename)

        content_mode = script.get("content_mode", "narration")
        if content_mode == "narration" and "segments" in script:
            items = script["segments"]
        else:
            items = script.get("scenes", [])

        return [item for item in items if not item.get("generated_assets", {}).get("storyboard_image")]

    # ==================== 项目级元数据管理 ====================

    def _get_project_file_path(self, project_name: str) -> Path:
        """获取项目元数据文件路径"""
        return self.get_project_path(project_name) / self.PROJECT_FILE

    def project_exists(self, project_name: str) -> bool:
        """检查项目元数据文件是否存在"""
        try:
            return self._get_project_file_path(project_name).exists()
        except FileNotFoundError:
            return False

    def load_project(self, project_name: str) -> dict:
        """
        加载项目元数据

        Args:
            project_name: 项目名称

        Returns:
            项目元数据字典
        """
        project_file = self._get_project_file_path(project_name)

        if not project_file.exists():
            raise FileNotFoundError(f"项目元数据文件不存在: {project_file}")

        with open(project_file, encoding="utf-8") as f:
            return json.load(f)

    def save_project(self, project_name: str, project: dict) -> Path:
        """
        保存项目元数据

        Args:
            project_name: 项目名称
            project: 项目元数据字典

        Returns:
            保存的文件路径
        """
        project_file = self._get_project_file_path(project_name)

        self._touch_metadata(project)

        with open(project_file, "w", encoding="utf-8") as f:
            json.dump(project, f, ensure_ascii=False, indent=2)

        emit_project_change_hint(
            project_name,
            changed_paths=[self.PROJECT_FILE],
        )

        return project_file

    def update_project(
        self,
        project_name: str,
        mutate_fn: Callable[[dict], None],
    ) -> Path:
        """原子性地更新 project.json：加文件锁 → 读 → 修改 → 写回。

        避免并发任务（如同时生成多张角色图片）之间的 lost-update 竞态。

        Args:
            project_name: 项目名称
            mutate_fn: 接收 project dict 并就地修改的回调函数
        """
        project_file = self._get_project_file_path(project_name)

        with open(project_file, "r+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                project = json.load(f)
                mutate_fn(project)
                self._touch_metadata(project)

                f.seek(0)
                json.dump(project, f, ensure_ascii=False, indent=2)
                f.truncate()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

        emit_project_change_hint(
            project_name,
            changed_paths=[self.PROJECT_FILE],
        )

        return project_file

    @staticmethod
    def _touch_metadata(project: dict) -> None:
        now = datetime.now().isoformat()
        if "metadata" not in project:
            project["metadata"] = {"created_at": now, "updated_at": now}
        else:
            project["metadata"]["updated_at"] = now

    def create_project_metadata(
        self,
        project_name: str,
        title: str | None = None,
        style: str | None = None,
        content_mode: str = "narration",
    ) -> dict:
        """
        创建新的项目元数据文件

        Args:
            project_name: 项目标识
            title: 项目标题，留空时默认使用项目标识
            style: 整体视觉风格描述
            content_mode: 内容模式 ('narration' 或 'drama')

        Returns:
            项目元数据字典
        """
        project_name = self.normalize_project_name(project_name)
        project_title = str(title).strip() if title is not None else ""

        project = {
            "title": project_title or project_name,
            "content_mode": content_mode,
            "style": style or "",
            "episodes": [],
            "characters": {},
            "clues": {},
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            },
        }

        self.save_project(project_name, project)
        return project

    def add_episode(self, project_name: str, episode: int, title: str, script_file: str) -> dict:
        """
        向项目添加剧集

        Args:
            project_name: 项目名称
            episode: 集数
            title: 剧集标题
            script_file: 剧本文件相对路径

        Returns:
            更新后的项目元数据
        """
        project = self.load_project(project_name)

        # 检查是否已存在
        for ep in project["episodes"]:
            if ep["episode"] == episode:
                ep["title"] = title
                ep["script_file"] = script_file
                self.save_project(project_name, project)
                return project

        # 添加新剧集（不包含统计字段，由 StatusCalculator 读时计算）
        project["episodes"].append({"episode": episode, "title": title, "script_file": script_file})

        # 按集数排序
        project["episodes"].sort(key=lambda x: x["episode"])

        self.save_project(project_name, project)
        return project

    def sync_project_status(self, project_name: str) -> dict:
        """
        [已废弃] 同步项目状态

        此方法已废弃。status、progress、scenes_count 等统计字段
        现在由 StatusCalculator 读时计算，不再存储在 JSON 文件中。

        保留此方法仅为向后兼容，实际不执行任何写入操作。

        Args:
            project_name: 项目名称

        Returns:
            项目元数据（不含统计字段，统计字段由 StatusCalculator 注入）
        """
        import warnings

        warnings.warn(
            "sync_project_status() 已废弃。status 等统计字段现由 StatusCalculator 读时计算。",
            DeprecationWarning,
            stacklevel=2,
        )
        # 仅返回项目数据，不执行任何写入
        return self.load_project(project_name)

    # ==================== 项目级角色管理 ====================

    def add_project_character(
        self,
        project_name: str,
        name: str,
        description: str,
        voice_style: str | None = None,
        character_sheet: str | None = None,
    ) -> dict:
        """
        向项目添加角色（项目级）

        Args:
            project_name: 项目名称
            name: 角色名称
            description: 角色描述
            voice_style: 声音风格
            character_sheet: 角色设计图路径

        Returns:
            更新后的项目元数据
        """
        project = self.load_project(project_name)

        project["characters"][name] = {
            "description": description,
            "voice_style": voice_style or "",
            "character_sheet": character_sheet or "",
        }

        self.save_project(project_name, project)
        return project

    def update_project_character_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        """更新项目级角色设计图路径"""
        project = self.load_project(project_name)

        if name not in project["characters"]:
            raise KeyError(f"角色 '{name}' 不存在")

        project["characters"][name]["character_sheet"] = sheet_path
        self.save_project(project_name, project)
        return project

    def update_character_reference_image(self, project_name: str, char_name: str, ref_path: str) -> dict:
        """
        更新角色的参考图路径

        Args:
            project_name: 项目名称
            char_name: 角色名称
            ref_path: 参考图相对路径

        Returns:
            更新后的项目数据
        """
        project = self.load_project(project_name)

        if "characters" not in project or char_name not in project["characters"]:
            raise KeyError(f"角色 '{char_name}' 不存在")

        project["characters"][char_name]["reference_image"] = ref_path
        self.save_project(project_name, project)
        return project

    def get_project_character(self, project_name: str, name: str) -> dict:
        """获取项目级角色定义"""
        project = self.load_project(project_name)

        if name not in project["characters"]:
            raise KeyError(f"角色 '{name}' 不存在")

        return project["characters"][name]

    # ==================== 线索管理 ====================

    def update_clue_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        """
        更新线索设计图路径

        Args:
            project_name: 项目名称
            name: 线索名称
            sheet_path: 设计图路径

        Returns:
            更新后的项目元数据
        """
        project = self.load_project(project_name)

        if name not in project["clues"]:
            raise KeyError(f"线索 '{name}' 不存在")

        project["clues"][name]["clue_sheet"] = sheet_path
        self.save_project(project_name, project)
        return project

    def get_clue(self, project_name: str, name: str) -> dict:
        """
        获取线索定义

        Args:
            project_name: 项目名称
            name: 线索名称

        Returns:
            线索定义字典
        """
        project = self.load_project(project_name)

        if name not in project["clues"]:
            raise KeyError(f"线索 '{name}' 不存在")

        return project["clues"][name]

    def get_pending_characters(self, project_name: str) -> list[dict]:
        """
        获取待生成设计图的角色列表

        Args:
            project_name: 项目名称

        Returns:
            待处理角色列表（无 character_sheet 或文件不存在）
        """
        project = self.load_project(project_name)
        project_dir = self.get_project_path(project_name)

        pending = []
        for name, char in project.get("characters", {}).items():
            sheet = char.get("character_sheet")
            if not sheet or not (project_dir / sheet).exists():
                pending.append({"name": name, **char})

        return pending

    def get_pending_clues(self, project_name: str) -> list[dict]:
        """
        获取待生成设计图的线索列表

        Args:
            project_name: 项目名称

        Returns:
            待处理线索列表（importance='major' 且无 clue_sheet）
        """
        project = self.load_project(project_name)
        project_dir = self.get_project_path(project_name)

        pending = []
        for name, clue in project["clues"].items():
            if clue.get("importance") == "major":
                sheet = clue.get("clue_sheet")
                if not sheet or not (project_dir / sheet).exists():
                    pending.append({"name": name, **clue})

        return pending

    def get_clue_path(self, project_name: str, filename: str) -> Path:
        """获取线索设计图路径"""
        return self.get_project_path(project_name) / "clues" / filename

    # ==================== 角色/线索直接写入工具 ====================

    def add_character(self, project_name: str, name: str, description: str, voice_style: str = "") -> bool:
        """
        直接添加角色到 project.json

        如果角色已存在，跳过不覆盖。

        Args:
            project_name: 项目名称
            name: 角色名称
            description: 角色描述
            voice_style: 声音风格（可选）

        Returns:
            True 如果新增成功，False 如果已存在
        """
        project = self.load_project(project_name)

        if name in project.get("characters", {}):
            logger.debug("角色 '%s' 已存在于 project.json，跳过", name)
            return False

        if "characters" not in project:
            project["characters"] = {}

        project["characters"][name] = {
            "description": description,
            "character_sheet": "",
            "voice_style": voice_style,
        }

        self.save_project(project_name, project)
        logger.info("添加角色: %s", name)
        return True

    def add_clue(
        self,
        project_name: str,
        name: str,
        clue_type: str,
        description: str,
        importance: str = "minor",
    ) -> bool:
        """
        直接添加线索到 project.json

        如果线索已存在，跳过不覆盖。

        Args:
            project_name: 项目名称
            name: 线索名称
            clue_type: 线索类型（prop 或 location）
            description: 线索描述
            importance: 重要性（major 或 minor，默认 minor）

        Returns:
            True 如果新增成功，False 如果已存在
        """
        project = self.load_project(project_name)

        if name in project.get("clues", {}):
            logger.debug("线索 '%s' 已存在于 project.json，跳过", name)
            return False

        if "clues" not in project:
            project["clues"] = {}

        project["clues"][name] = {
            "type": clue_type,
            "description": description,
            "importance": importance,
            "clue_sheet": "",
        }

        self.save_project(project_name, project)
        logger.info("添加线索: %s", name)
        return True

    def add_characters_batch(self, project_name: str, characters: dict[str, dict]) -> int:
        """
        批量添加角色到 project.json

        Args:
            project_name: 项目名称
            characters: 角色字典 {name: {description, voice_style}}

        Returns:
            新增的角色数量
        """
        project = self.load_project(project_name)

        if "characters" not in project:
            project["characters"] = {}

        added = 0
        for name, data in characters.items():
            if name not in project["characters"]:
                project["characters"][name] = {
                    "description": data.get("description", ""),
                    "character_sheet": data.get("character_sheet", ""),
                    "voice_style": data.get("voice_style", ""),
                }
                added += 1
                logger.info("添加角色: %s", name)
            else:
                logger.debug("角色 '%s' 已存在，跳过", name)

        if added > 0:
            self.save_project(project_name, project)

        return added

    def add_clues_batch(self, project_name: str, clues: dict[str, dict]) -> int:
        """
        批量添加线索到 project.json

        Args:
            project_name: 项目名称
            clues: 线索字典 {name: {type, description, importance}}

        Returns:
            新增的线索数量
        """
        project = self.load_project(project_name)

        if "clues" not in project:
            project["clues"] = {}

        added = 0
        for name, data in clues.items():
            if name not in project["clues"]:
                project["clues"][name] = {
                    "type": data.get("type", "prop"),
                    "description": data.get("description", ""),
                    "importance": data.get("importance", "minor"),
                    "clue_sheet": data.get("clue_sheet", ""),
                }
                added += 1
                logger.info("添加线索: %s", name)
            else:
                logger.debug("线索 '%s' 已存在，跳过", name)

        if added > 0:
            self.save_project(project_name, project)

        return added

    # ==================== 参考图收集工具 ====================

    def collect_reference_images(self, project_name: str, scene: dict) -> list[Path]:
        """
        收集场景所需的所有参考图

        Args:
            project_name: 项目名称
            scene: 场景字典

        Returns:
            参考图路径列表
        """
        project = self.load_project(project_name)
        project_dir = self.get_project_path(project_name)
        refs = []

        # 角色参考图
        for char in scene.get("characters_in_scene", []):
            char_data = project["characters"].get(char, {})
            sheet = char_data.get("character_sheet")
            if sheet:
                sheet_path = project_dir / sheet
                if sheet_path.exists():
                    refs.append(sheet_path)

        # 线索参考图
        for clue in scene.get("clues_in_scene", []):
            clue_data = project["clues"].get(clue, {})
            sheet = clue_data.get("clue_sheet")
            if sheet:
                sheet_path = project_dir / sheet
                if sheet_path.exists():
                    refs.append(sheet_path)

        return refs

    # ==================== 项目概述生成 ====================

    def _read_source_files(self, project_name: str, max_chars: int = 50000) -> str:
        """
        读取项目 source 目录下的所有文本文件内容

        Args:
            project_name: 项目名称
            max_chars: 最大读取字符数（避免超出 API 限制）

        Returns:
            合并后的文本内容
        """
        project_dir = self.get_project_path(project_name)
        source_dir = project_dir / "source"

        if not source_dir.exists():
            return ""

        contents = []
        total_chars = 0

        # 按文件名排序，确保顺序一致
        for file_path in sorted(source_dir.glob("*")):
            if file_path.is_file() and file_path.suffix.lower() in [".txt", ".md"]:
                try:
                    with open(file_path, encoding="utf-8") as f:
                        content = f.read()
                        remaining = max_chars - total_chars
                        if remaining <= 0:
                            break
                        if len(content) > remaining:
                            content = content[:remaining]
                        contents.append(f"--- {file_path.name} ---\n{content}")
                        total_chars += len(content)
                except Exception as e:
                    logger.error("读取文件失败 %s: %s", file_path.name, e)

        return "\n\n".join(contents)

    async def generate_overview(self, project_name: str) -> dict:
        """
        使用 Gemini API 异步生成项目概述

        Args:
            project_name: 项目名称

        Returns:
            生成的 overview 字典，包含 synopsis, genre, theme, world_setting, generated_at
        """
        from .text_backends.base import TextGenerationRequest, TextTaskType
        from .text_generator import TextGenerator

        # 读取源文件内容
        source_content = self._read_source_files(project_name)
        if not source_content:
            raise ValueError("source 目录为空，无法生成概述")

        # 创建 TextGenerator（自动追踪用量）
        generator = await TextGenerator.create(TextTaskType.OVERVIEW, project_name)

        # 调用 TextGenerator（Structured Outputs）
        prompt = f"请分析以下小说内容，提取关键信息：\n\n{source_content}"

        result = await generator.generate(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=ProjectOverview,
            ),
            project_name=project_name,
        )
        response_text = result.text

        # 解析并验证响应
        overview = ProjectOverview.model_validate_json(response_text)
        overview_dict = overview.model_dump()
        overview_dict["generated_at"] = datetime.now().isoformat()

        # 保存到 project.json
        project = self.load_project(project_name)
        project["overview"] = overview_dict
        self.save_project(project_name, project)

        logger.info("项目概述已生成并保存")
        return overview_dict
