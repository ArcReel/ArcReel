"""
项目文件管理器

管理视频项目的目录结构、分镜剧本读写、状态追踪。
"""

import copy
import json
import logging
import os
import re
import secrets
import shutil
import unicodedata
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import portalocker
from pydantic import BaseModel, Field

from lib.agent_profile import agent_profile_dir
from lib.asset_types import ASSET_SPECS, validate_asset_name
from lib.episode_ledger import SOURCE_TEXT_SUFFIXES
from lib.episode_paths import episode_script_relpath
from lib.json_io import atomic_write_json, load_json, load_json_or_none
from lib.profile_manifest import (
    VALID_CONTENT_MODES,
    ContentMode,
    ProfileEmptyError,
    ProfileMisconfiguredError,
    ProfileMissingError,
    sync_profile_to_project,
)
from lib.profile_manifest import (
    force_resync_profile as _force_resync_profile,
)
from lib.project_change_hints import emit_project_change_hint
from lib.script_editor import ScriptEditError, resolve_items
from lib.style_templates import LEGACY_STYLE_MAP, resolve_template_prompt

logger = logging.getLogger(__name__)

PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
PROJECT_SLUG_SANITIZER = re.compile(r"[^a-zA-Z0-9]+")

_VALID_GENERATION_MODES = {"storyboard", "grid", "reference_video"}
_DEFAULT_GENERATION_MODE = "storyboard"

# 源文件性质（source_kind）：与 content_mode / generation_mode 正交的第三轴，project.json
# 顶层字段，创建时确定、之后不可变。novel（默认，现状改编链路）/ screenplay（成品剧本，
# drama 链路翻为提取优先）。详见 docs/adr/0036 与 CONTEXT.md「剧本源」词条。
SourceKind = Literal["novel", "screenplay"]
VALID_SOURCE_KINDS: frozenset[str] = frozenset({"novel", "screenplay"})
DEFAULT_SOURCE_KIND: SourceKind = "novel"


class _Unset:
    """哨兵：区分「未传 before」（写盘统一入口自行读盘取改前）与「显式传 None」（无改前）。"""


_UNSET = _Unset()


def effective_mode(*, project: dict, episode: dict) -> str:
    """按 episode → project → 默认 storyboard 回退解析 generation_mode。

    未知值一律回退到默认，兼容脏数据。
    """
    ep_mode = episode.get("generation_mode")
    if ep_mode in _VALID_GENERATION_MODES:
        return ep_mode
    proj_mode = project.get("generation_mode")
    if proj_mode in _VALID_GENERATION_MODES:
        return proj_mode
    return _DEFAULT_GENERATION_MODE


def resolve_source_kind(project: Mapping[str, Any]) -> SourceKind:
    """项目源文件性质（novel / screenplay），缺失或非法值回退默认 novel，兼容脏数据。"""
    value = project.get("source_kind")
    if isinstance(value, str) and value in VALID_SOURCE_KINDS:
        return cast(SourceKind, value)
    return DEFAULT_SOURCE_KIND


def _resolve_items_or_warn(script: dict, *, script_filename: str | None = None) -> list[dict]:
    """读取路径的脏数据降级：基于 `resolve_items` 三模式判别（narration/drama/reference_video），
    脏数据（键存在但值非 list）下 log warning + 返回 []。

    与写入路径（`update_scene_asset` / `batch_update_scene_assets`）共用 `resolve_items` 判别
    保证三模式一致——上一版本用 `_script_items_shape` 在 reference 模式下会静默落到 drama
    兜底返回 []，破坏一致性。写入侧应该 fail-loud（让 ScriptEditError 上冒，worker 显式失败，
    上层 API 5xx 告知数据损坏）；读取侧在脏数据下返回 [] 不阻塞 UI 渲染，但 warning 给运维
    可观测信号去人工修复，不让降级变隐形。
    """
    try:
        items, _id_field, _kind = resolve_items(script)
        return items
    except ScriptEditError as e:
        logger.warning(
            "剧本 %s 数据损坏（%s），读取降级为空列表——请人工修复",
            script_filename or "<unknown>",
            e,
        )
        return []


class EpisodeScriptReboundError(RuntimeError):
    """加锁前后 episode→script_file 绑定发生变化（并发 PATCH 改绑），调用方应重试。"""


# ==================== 数据模型 ====================


class ProjectOverview(BaseModel):
    """项目概述数据模型，用于 Gemini Structured Outputs"""

    synopsis: str = Field(description="故事梗概，200-300字，概括主线剧情")
    genre: str = Field(description="题材类型，如：古装宫斗、现代悬疑、玄幻修仙")
    theme: str = Field(description="核心主题，如：复仇与救赎、成长与蜕变")
    world_setting: str = Field(description="时代背景和世界观设定，100-200字")
    # language 是 LLM 输出存档字段；唯一真相源是顶层 project["source_language"]，
    # 由 generate_overview 在落盘时同步写入。所有度量/切分调用方一律读顶层字段，
    # 不要直接读 overview.language。
    language: Literal["zh", "en", "vi"] = Field(description="小说源语言代码")


class ProjectManager:
    """视频项目管理器"""

    # 项目子目录结构
    SUBDIRS = [
        "source",
        "scripts",
        "drafts",
        "characters",
        "scenes",
        "props",
        "products",
        "storyboards",
        "videos",
        "thumbnails",
        "output",
        "grids",
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
        """Build a filesystem-safe slug prefix from the project title.

        CJK 标题经 NFKD + ascii ignore 后会丢光汉字;若结果不包含任何字母
        （纯空 / 仅夹在标题里的孤立数字,如「第1集」→ "1"),退化为中性
        前缀 ``proj``,避免产生 ``1-<hex>`` 这种看似有意义实则误导的 slug。

        注意 truncate 必须在 letter 校验之前:像 ``"1-2345...23abc"``(>24 字符,
        字母在尾部) 截前 24 后会只剩数字,这种结果同样应塌成 ``proj``。
        """
        ascii_text = unicodedata.normalize("NFKD", str(title).strip()).encode("ascii", "ignore").decode("ascii")
        slug = PROJECT_SLUG_SANITIZER.sub("-", ascii_text).strip("-_").lower()[:24]
        if not slug or not any(c.isalpha() for c in slug):
            return "proj"
        return slug

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

    def __init__(self, projects_root: str | Path | None = None):
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
        return [d.name for d in self.projects_root.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))]

    def get_global_assets_root(self) -> Path:
        """返回全局资产根目录，并确保 character/scene/prop 子目录存在。"""
        root = self.projects_root / "_global_assets"
        root.mkdir(parents=True, exist_ok=True)
        for sub in ("character", "scene", "prop"):
            (root / sub).mkdir(exist_ok=True)
        return root

    def create_project(self, name: str, content_mode: ContentMode = "narration") -> Path:
        """
        创建新项目

        Args:
            name: 项目标识（全局唯一，用于 URL 和文件系统）
            content_mode: 内容模式（narration / drama），影响 profile 物化时选哪份变体

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

        # 持久化 content_mode 到 project.json，让后续 sync_all_agent_profiles 启动遍历能恢复模式。
        # server 路径随后会调 create_project_metadata 覆盖为完整版（也含 content_mode）。
        try:
            atomic_write_json(project_dir / self.PROJECT_FILE, {"content_mode": content_mode})
            self.sync_agent_profile(project_dir, content_mode=content_mode)
        except Exception:
            # sync 失败时回滚 project_dir，避免残缺目录阻塞重试（同名 create 撞 FileExistsError）
            shutil.rmtree(project_dir, ignore_errors=True)
            raise

        return project_dir

    def sync_agent_profile(
        self,
        project_dir: Path,
        *,
        content_mode: ContentMode | None = None,
    ) -> dict:
        """同步 agent_runtime_profile 到项目目录的 .claude / CLAUDE.md。

        ``content_mode=None`` 时从 ``project_dir/project.json`` 读取；
        project.json 缺失或 ``content_mode`` 字段缺失 → 回退到 ``"narration"`` + log info。
        ``content_mode`` 显式非法值 → 抛 ``ValueError``。

        详见 ``lib.profile_manifest.sync_profile_to_project``：manifest-driven
        sync，sha256 区分内置 skill 升级（自动传播）/ 用户修改（保留）/ 用户主动
        删除（不复活）；profile 上游删除时同步删除项目内未改副本；命名碰撞 /
        状态机回流等 15 行决策表完整覆盖。

        Returns:
            含向后兼容 ``created/repaired/skipped/errors`` + 细分 stat key 的字典
        """
        if content_mode is None:
            content_mode = self._resolve_content_mode(project_dir)
        profile_dir = agent_profile_dir()
        return sync_profile_to_project(profile_dir, project_dir, content_mode)

    def force_resync_profile(
        self,
        project_dir: Path,
        *,
        paths: list[str] | None = None,
        content_mode: ContentMode | None = None,
    ) -> dict:
        """强制按 profile 覆盖项目内对应文件并刷新 manifest。

        用于 UI"恢复内置 skill"按钮等显式触发的场景。``paths=None`` 表示全量；
        指定 paths 中若某文件 profile 已删，会 skip + log warn（不算 error）。

        ``content_mode=None`` 时与 ``sync_agent_profile`` 同语义，自动从 project.json 解析。
        """
        if content_mode is None:
            content_mode = self._resolve_content_mode(project_dir)
        profile_dir = agent_profile_dir()
        return _force_resync_profile(profile_dir, project_dir, content_mode, paths=paths)

    def _resolve_content_mode(self, project_dir: Path) -> ContentMode:
        """从 project_dir/project.json 读 content_mode；缺失回退 narration。

        ``project.json`` 不存在或缺 ``content_mode`` 字段 → 回退 narration（兼容
        老项目）。文件存在但读取/解析失败 → raise，让上层 sync_all_agent_profiles
        走 failed_projects 分支；若静默回退到 narration，drama 项目会因 manifest
        记录的 mode 不匹配触发破坏性 reset，把 profile 错误切回说书变体。
        """
        pj_path = project_dir / self.PROJECT_FILE
        try:
            data = load_json(pj_path)
        except FileNotFoundError:
            logger.info("project.json missing under %s, defaulting content_mode=narration", project_dir)
            return "narration"
        mode = data.get("content_mode") if isinstance(data, dict) else None
        if mode is None:
            logger.info("project.json has no content_mode under %s, defaulting narration", project_dir)
            return "narration"
        if not isinstance(mode, str) or mode not in VALID_CONTENT_MODES:
            raise ValueError(
                f"project {project_dir.name}: invalid content_mode={mode!r} "
                f"(must be one of {sorted(VALID_CONTENT_MODES)})"
            )
        return cast(ContentMode, mode)

    def sync_all_agent_profiles(self) -> dict:
        """扫描所有项目目录，同步 agent_runtime_profile（启动 hook 用）。

        单项目失败隔离：捕获普通异常后继续下一项目（``failed_projects`` 计数）。
        ``ProfileMissingError`` / ``ProfileEmptyError`` 是部署级错误，全部跳过
        并设 ``aborted=True``，避免静默把所有项目的 .claude 删空。

        Returns:
            含向后兼容 ``created/repaired/skipped/errors`` + 细分 stat + 兜底
            ``failed_projects`` / ``aborted`` 字段
        """
        totals = {
            "created": 0,
            "repaired": 0,
            "skipped": 0,
            "errors": 0,
            "failed_projects": 0,
            "aborted": False,
        }
        if not self.projects_root.exists():
            return totals
        _STAT_KEYS_TO_AGGREGATE = (
            "created",
            "repaired",
            "skipped",
            "errors",
            "upgraded",
            "user_modified",
            "user_only",
            "pruned",
            "orphaned",
            "deleted_user",
            "tombstoned",
            "unchanged",
            "collision",
            "migrated_total",
        )
        for project_dir in sorted(self.projects_root.iterdir()):
            # 与 ``list_projects`` 同规则：跳过点开头（.git 等）和下划线开头
            # （``_global_assets`` 保留目录 — 跨项目共享 character/scene/prop 库，
            # 不是项目，不该 sync agent profile）
            if not project_dir.is_dir() or project_dir.name.startswith((".", "_")):
                continue
            try:
                result = self.sync_agent_profile(project_dir)
                for key in _STAT_KEYS_TO_AGGREGATE:
                    if key in result:
                        totals[key] = totals.get(key, 0) + result[key]
            except (ProfileMissingError, ProfileEmptyError, ProfileMisconfiguredError) as e:
                # 部署级错误（profile 路径错 / volume 挂载失败）→ 全部跳过，
                # 不要 fallback 到"假装 profile 是空"的破坏行为
                logger.error("profile sync ABORTED for ALL projects: %s", e)
                totals["aborted"] = True
                break
            except ValueError as e:
                # 单个项目 content_mode 非法 → 跳过，不影响其它项目
                logger.warning("Skip sync for %s: %s", project_dir.name, e)
                totals["failed_projects"] += 1
            except Exception:
                logger.exception("profile sync failed for %s", project_dir.name)
                totals["failed_projects"] += 1
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
            "scenes": [],
            "props": [],
            "products": [],
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
                elif subdir == "scenes":
                    status["scenes"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
                elif subdir == "props":
                    status["props"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
                elif subdir == "products":
                    status["products"] = [f.name for f in files if f.suffix in [".png", ".jpg", ".jpeg"]]
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

    def create_script(self, project_name: str, title: str, chapter: str) -> dict:
        """
        创建新的分镜剧本模板

        Args:
            project_name: 项目名称
            title: 小说标题
            chapter: 章节名称

        Returns:
            剧本字典
        """
        script = {
            "novel": {"title": title, "chapter": chapter},
            "scenes": [],
            "metadata": {
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "total_scenes": 0,
                "estimated_duration_seconds": 0,
                "status": "draft",
            },
        }

        return script

    def save_script(
        self, project_name: str, script: dict, filename: str | None = None, *, validate: bool = True
    ) -> Path:
        """
        保存分镜剧本

        Args:
            project_name: 项目名称
            script: 剧本字典
            filename: 可选的文件名，默认使用章节名
            validate: 是否做「不更坏」结构校验（默认 True，fail-safe）。直连保存不持有
                改前剧本，由写盘统一入口按需读盘取改前（已存在则不更坏，全新保存则严格校验）。

        Returns:
            保存的文件路径
        """
        if filename is not None and filename.startswith("scripts/"):
            filename = filename[len("scripts/") :]

        if filename is None:
            chapter = script["novel"].get("chapter", "chapter_01")
            filename = f"{chapter.replace(' ', '_')}_script.json"

        with self._script_lock(project_name, filename):
            return self._write_script_unlocked(project_name, script, filename, validate=validate)

    def _write_script_unlocked(
        self,
        project_name: str,
        script: dict,
        filename: str,
        sync_project: bool = True,
        *,
        validate: bool = True,
        before: dict | None | _Unset = _UNSET,
    ) -> Path:
        """剧本写盘主体：校验 + 更新元数据 + 原子写 + 同步 project.json。

        **不获取 `_script_lock`**——调用方必须已持有该锁（见 `save_script` / `locked_script`），
        否则会丧失并发保护。独立抽出是为了避免 `locked_script` 复用 `save_script` 时二次获取
        同一把 flock 造成同进程自死锁（与 `update_project` 内联 `atomic_write_json` 而不复用
        `save_project` 同理）。filename 须已去除 `scripts/` 前缀且非 None。

        `sync_project=False` 时跳过 `sync_episode_from_script`：该同步会经 `update_project`
        再次获取 `_project_lock`，故已持有项目锁的调用方（见 `locked_episode_script`）须传 False
        以免同进程自死锁。仅写脚本内容、不改 episode 元数据的场景跳过同步无副作用。

        `validate=True`（默认，fail-safe）时按「不更坏」语义做结构校验：仅当本次写入把一个
        原本合法的剧本改成非法时才 `raise ScriptStructureValidationError`，改前就已非法的旧
        剧本照常放行。读-改-写流程（`locked_script` 一族）已持有改前剧本，应作 `before` 传入
        以零额外读盘；直连保存不传 `before`，由本函数按需读盘取改前（无改前则按严格校验）。
        资产回写等只动 `generated_assets` 的热路径传 `validate=False` 整体豁免。
        """
        scripts_dir = self.get_project_path(project_name) / "scripts"
        real = self._safe_subpath(scripts_dir, filename)
        output_path = Path(real)

        # 结构校验守卫（「不更坏」语义），置于落盘前，避免脏数据潜伏到 worker 执行层才暴露。
        if validate:
            before_script = self._load_script_or_none(output_path) if isinstance(before, _Unset) else before
            self._guard_no_worse(before_script, script)

        # 再做 filename/内部 episode 一致性校验，避免写盘后才在 sync 阶段抛错，
        # 造成"脚本文件已落盘、project.json 未同步"的部分提交。
        self._require_filename_episode_consistency(script, filename)

        # 更新元数据（兼容旧脚本：可能缺少 metadata，或 narration 使用 segments）
        now = datetime.now(UTC).isoformat()
        metadata = script.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            script["metadata"] = metadata
        metadata.setdefault("created_at", now)
        metadata.setdefault("status", "draft")
        metadata["updated_at"] = now

        # 选当前剧本的分镜数组：与结构校验 `_select_model` / 编辑核心 `resolve_items` 共用同一
        # 判别（含 reference 模式的 video_units——否则它会落入 segments 兜底分支、total_scenes 错算为 0）。
        # `resolve_items` 对 segments/scenes/video_units 存在但非 list（含 null 这类历史脏数据）会
        # fail-loud：`validate=True` 路径已被 `_guard_no_worse` 提前拦下（不会到这里）；只有
        # `validate=False` 资产回写热路径才会撞上脏数据键，此时**保留旧 metadata 不重算**——
        # 旧的 total_scenes / estimated_duration_seconds 即便陈旧，也好过把 reference 模式
        # 改写成 0-scene narration shell（fallback hard-pin kind='segments' 那种）。资产回写本就
        # 「整体豁免结构校验」，连带豁免 metadata 重算与「不更坏」语义一致：脏数据不更坏。
        try:
            items, _id_field, kind = resolve_items(script)
        except ScriptEditError as e:
            logger.warning(
                "剧本 %s 数据损坏（%s），跳过 metadata 重算以保留旧值",
                output_path.name,
                e,
            )
        else:
            # 损坏脚本可能混入非 dict 元素（如 ["foo", {...}]）——它们在读取路径
            # （get_pending_scenes 等）与写入路径（batch_update 索引 / update_scene_asset 循环）
            # 都被当作不存在过滤掉，metadata 重算一并排除：否则 total_scenes 会多计、
            # estimated_duration_seconds 会被垃圾元素按 default 时长撑大，与各路径不一致。
            scene_items = [item for item in items if isinstance(item, dict)]
            metadata["total_scenes"] = len(scene_items)
            # 计算总时长：按当前选中的数据结构决定回退值，避免 content_mode 缺失时误判。
            # ``.get(k, default)`` 仅在键缺失时返回 default，键存在但值为 None（脏数据）会
            # 返回 None 让 sum() 抛 TypeError——显式判 None 视为缺失，与同函数前面对 metadata
            # 缺字段时按 setdefault 兜底的语义一致：脏值不阻塞 metadata 重算，但若 default 也
            # 失真（如 reference 模式未填 duration_seconds），下游 estimated 字段仍是近似值。
            # shots（ad）无单镜头默认时长偏好，缺失按 0 计（与 StatusCalculator 口径一致）；
            # 未知 kind 沿用历史兜底 8。
            default_duration = {"segments": 4, "scenes": 8, "shots": 0}.get(kind, 8)

            def _duration(item: dict) -> int:
                value = item.get("duration_seconds")
                # bool 是 int 子类,排除避免 duration_seconds=True 被算成 1 秒、=False 算成 0 秒
                if isinstance(value, bool):
                    return default_duration
                if isinstance(value, (int, float)):
                    return int(value)
                return default_duration

            metadata["estimated_duration_seconds"] = sum(_duration(item) for item in scene_items)

        # 原子写（含路径遍历防护，output_path 已在守卫前解析），避免并发 PATCH 导致 JSON 损坏
        atomic_write_json(output_path, script)

        # 同步到 project.json，保证 script 写入与元数据同步是单一事务
        # （sync 走的是 `_project_lock`，与外层 `_script_lock` 不同锁，不会冲突）。
        if sync_project and self.project_exists(project_name) and isinstance(script.get("episode"), int):
            self.sync_episode_from_script(project_name, filename)

        emit_project_change_hint(
            project_name,
            changed_paths=[f"scripts/{output_path.name}"],
        )

        return output_path

    @contextmanager
    def locked_script(self, project_name: str, script_filename: str, *, validate: bool = True):
        """在单一 `_script_lock` 内完成剧本的 load → mutate → save 读-改-写。

        yield 出剧本字典供调用方就地修改；正常退出时写回，with 体内抛异常（如目标 scene/unit
        未找到）则跳过写回、照常释放锁。与 `update_project` 对称，消除"读改写之间被并发写覆盖"
        的 lost-update 竞态。

        `validate=True`（默认）时在 yield 前快照「改前」剧本，写回走「不更坏」结构校验（零额外
        读盘）。只动 `generated_assets` 的资产回写热路径传 `validate=False` 整体豁免。
        """
        norm = script_filename[len("scripts/") :] if script_filename.startswith("scripts/") else script_filename
        with self._script_lock(project_name, norm):
            script = self.load_script(project_name, norm)
            before = copy.deepcopy(script) if validate else None
            yield script
            self._write_script_unlocked(project_name, script, norm, validate=validate, before=before)

    def _read_project_raw_unlocked(self, project_name: str) -> dict:
        """裸读 project.json（不取锁、不迁移）。仅供已持 `_project_lock` 的复核调用。"""
        project_file = self._get_project_file_path(project_name)
        with open(project_file, encoding="utf-8") as f:  # noqa: PTH123
            return json.load(f)

    @contextmanager
    def locked_episode_script(
        self, project_name: str, resolve_script_file: Callable[[dict], str], *, validate: bool = True
    ):
        """统一「脚本锁 → 项目锁」顺序下，解析 episode→script_file 并对剧本做读-改-写。

        `resolve_script_file(project) -> script_file`：调用方提供的解析器，从 project.json
        找到目标 episode、做校验、返回其绑定的脚本文件名（可自行抛异常，如 404/409）。

        解析候选 → 加锁 → 复核绑定 → 写入全程在持 `_project_lock` 的临界区内完成，消除
        「锁外读 script_file 后被并发 PATCH 改绑、写入落到旧脚本」的 TOCTOU。锁获取顺序与
        worker 回写（`locked_script` → sync）保持一致的 脚本锁 → 项目锁，避免 ABBA 死锁。

        写脚本经 `sync_project=False` 跳过 `_write_script_unlocked` 内会二次取项目锁的 sync
        （避免同进程自死锁）；改在已持有的项目锁内联完成集元数据同步与 project.json 写回，
        与旧 `locked_script` → sync 路径行为一致（刷新 episodes 元数据与 `updated_at`）。

        若加锁前后绑定指向了不同脚本（并发改绑），抛 `EpisodeScriptReboundError` 让调用方重试。
        """
        candidate = resolve_script_file(self.load_project(project_name))
        norm = candidate[len("scripts/") :] if candidate.startswith("scripts/") else candidate
        with self._script_lock(project_name, norm):
            with self._project_lock(project_name):
                project = self._read_project_raw_unlocked(project_name)
                current = resolve_script_file(project)
                cur_norm = current[len("scripts/") :] if current.startswith("scripts/") else current
                if cur_norm != norm:
                    raise EpisodeScriptReboundError(f"episode script binding changed: {norm} -> {cur_norm}")
                script = self.load_script(project_name, norm)
                before = copy.deepcopy(script) if validate else None
                yield script
                self._write_script_unlocked(
                    project_name, script, norm, sync_project=False, validate=validate, before=before
                )
                # 在已持项目锁内联同步 project.json（等价 update_project 写路径，但不二次取锁）
                if isinstance(script.get("episode"), int):
                    self._apply_episode_sync(project, script, norm)
                self._migrate_legacy_resolution_on_save(project)
                self._migrate_legacy_style(project)
                self._touch_metadata(project)
                atomic_write_json(self._get_project_file_path(project_name), project)
                emit_project_change_hint(project_name, changed_paths=[self.PROJECT_FILE])

    @staticmethod
    def _require_filename_episode_consistency(script: dict, script_filename: str) -> None:
        """校验脚本内 `episode` 字段与文件名隐含的集号一致；不一致则 raise ValueError。

        filename 缺集号模式或脚本内无 `episode` int 时静默放行（兼容旧数据）。
        """
        base_name = script_filename[len("scripts/") :] if script_filename.startswith("scripts/") else script_filename
        filename_match = re.search(r"episode[-_\s]*(\d+)", base_name, re.IGNORECASE)
        if filename_match is None:
            return
        script_episode = script.get("episode")
        if not isinstance(script_episode, int):
            return
        filename_episode = int(filename_match.group(1))
        if script_episode != filename_episode:
            raise ValueError(
                f"脚本 {base_name} 内部 episode={script_episode} 与文件名隐含的 "
                f"episode={filename_episode} 不一致，拒绝操作以避免污染 project.json"
            )

    @staticmethod
    def _load_script_or_none(path: Path) -> dict | None:
        """裸读剧本 JSON 取「改前」快照；文件不存在或损坏时返回 None（→ 按严格校验处理）。"""
        loaded = load_json_or_none(path)
        return loaded if isinstance(loaded, dict) else None

    @staticmethod
    def _guard_no_worse(before: dict | None, after: dict) -> None:
        """「不更坏」守卫：仅当本次写入引入新结构错误时拒绝。

        改后合法 → 放行；改后非法时：改前合法或无改前 → 拒绝（`raise`）；改前已非法 → 放行
        （不为历史遗留背锅）。校验器经函数内延迟 import，打破 project_manager → 校验器 →
        data_validator → project_manager 的导入环。
        """
        from lib.script_structure_validator import (
            ScriptStructureValidationError,
            validate_script_structure,
        )

        after_result = validate_script_structure(after)
        if after_result.valid:
            return
        if before is not None and not validate_script_structure(before).valid:
            return
        raise ScriptStructureValidationError(after_result)

    @staticmethod
    def resolve_episode_from_script(script: dict, script_filename: str) -> int:
        """从剧本解析集号。

        优先使用 script 顶层 `episode` 字段（真相源），fallback 到文件名正则
        `episode[-_\\s]*(\\d+)`（支持下划线/空格/连字符分隔）；两者都无则抛 ValueError。

        用于替代调用方重复传入 `--episode` CLI 参数造成的错配风险。
        """
        ep = script.get("episode")
        if isinstance(ep, int):
            return ep
        match = re.search(r"episode[-_\s]*(\d+)", script_filename, re.IGNORECASE)
        if match:
            return int(match.group(1))
        raise ValueError(f"无法确定集号：剧本缺少 episode 字段且文件名 {script_filename} 不含 episodeN 模式")

    def sync_episode_from_script(self, project_name: str, script_filename: str) -> dict:
        """
        从剧本文件同步集数信息到 project.json

        Agent 写入剧本后必须调用此方法以确保 WebUI 能正确显示剧集列表。

        Args:
            project_name: 项目名称
            script_filename: 剧本文件名（如 episode_1.json）

        Returns:
            更新后的 project 字典

        Raises:
            ValueError: 当文件名隐含的集号与脚本内 `episode` 字段不一致时抛出，
                避免错误的脚本数据覆盖真实集号条目（例如 episode_10.json 内部
                错写为 episode=1，会覆盖第 1 集）。
        """
        script = self.load_script(project_name, script_filename)
        return self.update_project(
            project_name, lambda project: self._apply_episode_sync(project, script, script_filename)
        )

    def _apply_episode_sync(self, project: dict, script: dict, script_filename: str) -> None:
        """把剧本的集号/标题/script_file 同步进 `project`（就地修改，不取锁、不写盘）。

        供 `sync_episode_from_script`（在 `update_project` 锁内）与 `locked_episode_script`
        （在已持 `_project_lock` 的临界区内）共用，避免重复实现集元数据同步逻辑。
        """
        base_name = script_filename[len("scripts/") :] if script_filename.startswith("scripts/") else script_filename
        # 防御纵深：SSE 扫描路径直接调用此函数（不经 save_script），同样需要校验
        self._require_filename_episode_consistency(script, base_name)

        script_episode = script.get("episode")
        if isinstance(script_episode, int):
            episode_num = script_episode
        else:
            filename_match = re.search(r"episode[-_\s]*(\d+)", base_name, re.IGNORECASE)
            episode_num = int(filename_match.group(1)) if filename_match else 1
        episode_title = script.get("title", "")
        script_file = f"scripts/{base_name}"

        # 查找或创建 episode 条目（整段 RMW 在单一 _project_lock 内完成，避免并发同步丢失）
        episodes = project.setdefault("episodes", [])
        episode_entry: dict[str, Any] | None = next((ep for ep in episodes if ep["episode"] == episode_num), None)
        if episode_entry is None:
            episode_entry = {"episode": episode_num}
            episodes.append(episode_entry)
        # 同步核心元数据（不包含统计字段，统计字段由 StatusCalculator 读时计算）
        episode_entry["title"] = episode_title
        episode_entry["script_file"] = script_file
        episodes.sort(key=lambda x: x["episode"])

        logger.info("已同步剧集信息: Episode %d - %s", episode_num, episode_title)

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
        real = Path(self._safe_subpath(project_dir / "scripts", filename))

        if not real.exists():
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
        # 资产回写热路径：只动运行时字段，结构不可能因此变坏，豁免结构校验。
        with self.locked_script(project_name, script_filename, validate=False) as script:
            if name not in script["characters"]:
                # 在锁内抛出，locked_script 跳过写回
                raise KeyError(f"角色 '{name}' 不存在")
            script["characters"][name]["character_sheet"] = sheet_path
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
            "storyboard_last_image": None,
            "video_clip": None,
            "video_thumbnail": None,
            "video_uri": None,
            "narration_audio": None,
            "grid_id": None,
            "grid_cell_index": None,
            "status": "pending",
        }

    @staticmethod
    def create_scene_template(scene_id: str, duration_seconds: int = 8) -> dict:
        """
        创建标准场景对象模板

        Args:
            scene_id: 场景 ID（如 "E1S01"），集号已编码在 ID 中
            duration_seconds: 场景时长（秒）

        Returns:
            标准的场景字典
        """
        return {
            "scene_id": scene_id,
            "duration_seconds": duration_seconds,
            "segment_break": False,
            "characters_in_scene": [],
            "scenes": [],
            "props": [],
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
            "generated_assets": ProjectManager.create_generated_assets(),
        }

    def normalize_scene(self, scene: dict) -> dict:
        """
        补全单个场景中缺失的字段

        Args:
            scene: 场景字典

        Returns:
            补全后的场景字典
        """
        template = self.create_scene_template(
            scene_id=scene.get("scene_id", "000"),
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
            "segment_break": False,
            "characters_in_scene": [],
            "scenes": [],
            "props": [],
            "action": "",
            "dialogue": template["dialogue"],
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
            match = re.search(r"episode[-_\s]*(\d+)", script_filename, re.IGNORECASE)
            if match:
                episode = int(match.group(1))
            else:
                episode = 1

        # 补全顶层字段
        script_defaults = {
            "episode": episode,
            "title": script.get("novel", {}).get("chapter", ""),
            "duration_seconds": 0,
        }

        for key, default_value in script_defaults.items():
            if key not in script:
                script[key] = default_value

        # 确保必要的顶层结构存在
        if "novel" not in script:
            script["novel"] = {"title": "", "chapter": ""}
        # 剥离已废弃的 source_file 字段
        if isinstance(script.get("novel"), dict):
            script["novel"].pop("source_file", None)

        # 旧格式 script 仍可能携带 characters dict；project.json 已是单一真相源，
        # 此处仅记日志提醒，剧本 dict 保留不再做迁移（迁移实现历史上从未存在过）。
        if "characters" in script and isinstance(script["characters"], dict) and script["characters"]:
            logger.warning("检测到旧格式 characters 对象（仅记录提醒，不做迁移）")

        # 注意：characters_in_episode 已改为读时计算
        # 不再在 normalize_script 中创建这些字段

        if "scenes" not in script:
            script["scenes"] = []

        if "metadata" not in script:
            script["metadata"] = {
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "total_scenes": 0,
                "estimated_duration_seconds": 0,
                "status": "draft",
            }

        # 规范化每个场景
        for scene in script["scenes"]:
            self.normalize_scene(scene)

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
        # legacy helper：产出数字 scene_id 的旧结构 scene，与现行 Pydantic 模型不兼容，豁免结构校验。
        with self.locked_script(project_name, script_filename, validate=False) as script:
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
        # 资产回写热路径：只动 generated_assets，结构不可能因此变坏，豁免结构校验。
        # 但「分镜数组键损坏（如 segments: null）」是更严重的损坏，写入侧必须 fail-loud——
        # 静默 no-op 等于把数据丢失藏起来：worker 写完 N 个 video_clip 还以为成功了，UI 却
        # 看不到任何回写。让 ScriptEditError 上冒，worker 层负责降级（记 task 失败、人工修复）。
        # `resolve_items` 三模式判别（narration/drama/reference_video）与 `_write_script_unlocked`
        # / 读取 helper 共用同一源——避免 `_script_items_shape` 那种 reference 模式落到 drama 兜底
        # 取 "scenes" 键、静默返回 [] 然后 KeyError 报"场景不存在"的根因被掩盖路径。
        with self.locked_script(project_name, script_filename, validate=False) as script:
            content_mode = script.get("content_mode", "narration")
            items, id_field, _kind = resolve_items(script)

            for item in items:
                # 损坏脚本的非 dict 元素跳过（镜像 script_editor._find_index 的 isinstance 守卫），
                # 避免 item.get(id_field) 抛 AttributeError；未命中仍走下方 else 的 KeyError fail-loud。
                if not isinstance(item, dict):
                    continue
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
                    break
            else:
                # 未命中：在锁内抛出，locked_script 跳过写回
                raise KeyError(f"场景 '{scene_id}' 不存在")
        return script

    def batch_update_scene_assets(
        self,
        project_name: str,
        script_filename: str,
        updates: list[tuple[str, str, Any]],
    ) -> dict:
        """批量更新多个场景的生成资源路径（单次读写）。

        Args:
            project_name: 项目名称
            script_filename: 剧本文件名
            updates: 列表，每项为 (scene_id, asset_type, asset_path)

        Returns:
            更新后的剧本
        """
        if not updates:
            return {}

        # 资产回写热路径：只动 generated_assets，结构不可能因此变坏，豁免结构校验。
        # 分镜数组键损坏（resolve_items 抛 ScriptEditError）与 id 未命中两类错误都 fail-loud：
        # 静默 no-op 等于把 worker 写完的 N 个 clip 路径丢弃但 SSE 仍广播「all updated」、UI
        # 永远 pending。id 未命中收集一轮再统一抛，让 worker 看到完整失败集合而不是只看到首个；
        # locked_script 在 with 体内抛异常时整体不写回（与 update_scene_asset 单个版本对齐）。
        # resolve_items 让 reference 模式 worker 也能正确按 unit_id 索引 video_units。
        with self.locked_script(project_name, script_filename, validate=False) as script:
            content_mode = script.get("content_mode", "narration")
            items, id_field, _kind = resolve_items(script)

            # 建立 scene_id → item 索引，避免 O(N*M) 查找。损坏脚本的非 dict 元素过滤掉
            # （镜像 script_editor._existing_ids），命中这类 id 的 update 会落 missing → KeyError fail-loud。
            item_by_id: dict[str, dict] = {str(item.get(id_field)): item for item in items if isinstance(item, dict)}
            missing: list[str] = []

            for scene_id, asset_type, asset_path in updates:
                item = item_by_id.get(str(scene_id))
                if item is None:
                    missing.append(str(scene_id))
                    continue

                assets = item.get("generated_assets")
                if not isinstance(assets, dict):
                    assets = {}
                    item["generated_assets"] = assets

                assets_template = self.create_generated_assets(content_mode)
                for key, default_value in assets_template.items():
                    if key not in assets:
                        assets[key] = default_value

                assets[asset_type] = asset_path
                self.update_scene_status(item)

            if missing:
                raise KeyError(f"批量回写命中失败：以下分镜不存在 {sorted(set(missing))}")
        return script

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

        # `_resolve_items_or_warn` 三模式判别 + 脏数据 warn-and-skip 降级——读取侧 silent
        # 比写入侧 silent 安全（UI 渲染空列表好过 5xx 阻塞页面），但 warning 给可观测信号；
        # 写入侧（update_scene_asset / batch_update_scene_assets）则用 `resolve_items` 直接
        # 抛 ScriptEditError 保证数据损坏永远有显式信号。reference 模式下也能正确返回
        # video_units，不会静默落到 drama 兜底丢失 reference 数据。
        items = _resolve_items_or_warn(script, script_filename=script_filename)

        # item.generated_assets 缺失 / null / 非 dict 一律视为"未生成"——读取侧脏数据容错：
        # `.get("generated_assets", {}).get(...)` 只挡 key 缺失，None 与非 dict 仍会抛 AttributeError。
        # 与写入侧 update_scene_asset 的 isinstance check mirror。
        def _missing(item: dict) -> bool:
            assets = item.get("generated_assets")
            return not isinstance(assets, dict) or not assets.get(asset_type)

        # 损坏脚本的非 dict 元素直接剔除（镜像 script_editor._existing_ids 的过滤），UI 不渲染垃圾项。
        return [item for item in items if isinstance(item, dict) and _missing(item)]

    # ==================== 文件路径工具 ====================

    def get_source_path(self, project_name: str, filename: str) -> Path:
        """获取源文件路径"""
        return self.get_project_path(project_name) / "source" / filename

    def get_character_path(self, project_name: str, filename: str) -> Path:
        """获取角色设计图路径"""
        return self._get_asset_path("character", project_name, filename)

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

        # 同 get_pending_scenes：resolve_items 三模式判别 + warn-and-skip 降级 +
        # generated_assets 容错 isinstance check。
        items = _resolve_items_or_warn(script, script_filename=script_filename)

        def _missing_storyboard(item: dict) -> bool:
            assets = item.get("generated_assets")
            return not isinstance(assets, dict) or not assets.get("storyboard_image")

        # 同 get_pending_scenes：非 dict 元素剔除，镜像 script_editor._existing_ids。
        return [item for item in items if isinstance(item, dict) and _missing_storyboard(item)]

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

    @staticmethod
    def _migrate_legacy_style(project: dict) -> bool:
        """检测旧 style 值并就地迁移。返回是否发生了变更。"""
        if "style_template_id" in project:
            return False  # 已迁移
        legacy_value = project.get("style", "")
        if legacy_value not in LEGACY_STYLE_MAP:
            return False
        if project.get("style_image"):
            # 参考图优先：清空旧 style、template_id 置 None
            project["style_template_id"] = None
            project["style"] = ""
        else:
            new_id = LEGACY_STYLE_MAP[legacy_value]
            project["style_template_id"] = new_id
            project["style"] = resolve_template_prompt(new_id)
        return True

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

        migrated = False
        with self._project_lock(project_name):
            # 读-改-写放在同一把锁内，避免并发 save_project 在读与写之间完成
            # 更新后，迁移写回又把更新覆盖掉（Codex #304 P2）。
            with open(project_file, encoding="utf-8") as f:
                project = json.load(f)
            if self._migrate_legacy_style(project):
                # 不走 save_project 以避免触发 _touch_metadata 污染 updated_at。
                atomic_write_json(project_file, project)
                migrated = True
        if migrated:
            emit_project_change_hint(
                project_name,
                changed_paths=[self.PROJECT_FILE],
            )
        return project

    @contextmanager
    def _project_lock(self, project_name: str):
        """通过隐藏 lock file 获取项目文件的排他锁。

        使用独立的 .project.json.lock 而非数据文件本身，避免 os.replace
        更换 inode 后锁失效的问题。
        """
        project_file = self._get_project_file_path(project_name)
        lock_path = project_file.parent / f".{project_file.name}.lock"
        lock_path.touch(exist_ok=True)
        with portalocker.Lock(lock_path, flags=portalocker.LOCK_EX):
            yield

    @contextmanager
    def _script_lock(self, project_name: str, script_filename: str):
        """通过隐藏 lock file 获取剧本文件的排他锁。

        lock 文件命名为 `.{basename}.lock`（以 `.` 开头），位于规范化后剧本的
        parent 目录下，自动被 `list_scripts()` 的 `*.json` glob 与
        `project_archive` 的 `name.startswith(".")` 过滤排除。

        **关键**：用 `_safe_subpath` 规范化 filename 再派生 lock key，避免
        `./episode_1.json` 与 `episode_1.json` 解析到同一个 real path 却拿到
        不同锁、从而绕过互斥的别名问题。
        """
        scripts_dir = self.get_project_path(project_name) / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        if script_filename.startswith("scripts/"):
            script_filename = script_filename[len("scripts/") :]
        real = Path(self._safe_subpath(scripts_dir, script_filename))
        lock_path = real.parent / f".{real.name}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.touch(exist_ok=True)
        with portalocker.Lock(lock_path, flags=portalocker.LOCK_EX):
            yield

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

        self._migrate_legacy_resolution_on_save(project)
        self._touch_metadata(project)

        with self._project_lock(project_name):
            atomic_write_json(project_file, project)

        emit_project_change_hint(
            project_name,
            changed_paths=[self.PROJECT_FILE],
        )

        return project_file

    def update_project(
        self,
        project_name: str,
        mutate_fn: Callable[[dict], None],
    ) -> dict:
        """原子性地更新 project.json：加文件锁 → 读 → 修改 → 原子写回。

        避免并发任务（如同时生成多张角色图片）之间的 lost-update 竞态。
        在同一持锁窗口内统一应用读时迁移（_migrate_legacy_style），返回迁移后的项目元数据 dict，
        调用方无需再 load_project 一次。

        Args:
            project_name: 项目名称
            mutate_fn: 接收 project dict 并就地修改的回调函数

        Returns:
            迁移后的项目元数据字典（与 load_project 返回结构一致）
        """
        project_file = self._get_project_file_path(project_name)

        with self._project_lock(project_name):
            with open(project_file, encoding="utf-8") as f:
                project = json.load(f)
            mutate_fn(project)
            self._migrate_legacy_resolution_on_save(project)
            self._migrate_legacy_style(project)
            self._touch_metadata(project)
            atomic_write_json(project_file, project)

        emit_project_change_hint(
            project_name,
            changed_paths=[self.PROJECT_FILE],
        )

        return project

    @staticmethod
    def _touch_metadata(project: dict) -> None:
        now = datetime.now(UTC).isoformat()
        if "metadata" not in project:
            project["metadata"] = {"created_at": now, "updated_at": now}
        else:
            project["metadata"]["updated_at"] = now

    @staticmethod
    def _migrate_legacy_resolution_on_save(project: dict) -> None:
        """若 project.model_settings 含 resolution，清除 video_model_settings 中命中的 legacy 条目。

        规则：对每个 new model_settings key（形如 "<provider>/<model>"），若其 resolution 已设置，
        则从 video_model_settings[<model>] 中移除 resolution 字段；如该条目变空则删除该 key；
        legacy dict 变空时整体删除 video_model_settings。
        """
        model_settings = project.get("model_settings") or {}
        legacy = project.get("video_model_settings") or {}
        if not model_settings or not legacy:
            return
        for composite_key, entry in model_settings.items():
            if "/" not in composite_key:
                continue
            _, model_id = composite_key.split("/", 1)
            if not entry.get("resolution"):
                continue
            legacy_entry = legacy.get(model_id)
            if not legacy_entry:
                continue
            legacy_entry.pop("resolution", None)
            if not legacy_entry:
                legacy.pop(model_id, None)
        if not legacy:
            project.pop("video_model_settings", None)

    # 广告/短片项目恒单集：episodes 恒为第 1 集单条，剧本即第 1 集脚本文件
    AD_SINGLE_EPISODE = {"episode": 1, "title": "", "script_file": episode_script_relpath(1)}
    # 创建入口未传 target_duration 时的数据层兜底（与创建向导默认档位同值）
    AD_DEFAULT_TARGET_DURATION = 60

    def create_project_metadata(
        self,
        project_name: str,
        title: str | None = None,
        style: str | None = None,
        content_mode: str | None = "narration",
        aspect_ratio: str | None = "9:16",
        default_duration: int | None = None,
        style_template_id: str | None = None,
        extras: dict | None = None,
        target_duration: int | None = None,
        brief: str | None = None,
        source_kind: str | None = None,
    ) -> dict:
        """
        创建新的项目元数据文件

        `extras` 用于写入可选的模型/后端等字段（如 video_backend / image_provider_t2i /
        image_provider_i2i / text_backend_{script,overview,style}）。调用方负责剔除空值，
        本方法只按字面写入 extras 中已有的键——退役的单字段 image_backend 不在写入范围
        （解析链不再读取、写边界已拒绝），调用方不应再传入。

        `target_duration` / `brief` 仅 content_mode=ad 可用；ad 项目不持有
        `default_duration`，且 episodes 恒为第 1 集单条。

        `source_kind` 为源文件性质（novel / screenplay），缺省 novel，创建即定、之后不可变
        （可变性守卫在路由 PATCH 层，与 content_mode 同性质）。
        """
        project_name = self.normalize_project_name(project_name)
        project_title = str(title).strip() if title is not None else ""
        resolved_mode = content_mode or "narration"
        resolved_source_kind = DEFAULT_SOURCE_KIND if source_kind is None else source_kind
        if resolved_source_kind not in VALID_SOURCE_KINDS:
            raise ValueError(f"source_kind 值无效: {source_kind!r}，必须是 {sorted(VALID_SOURCE_KINDS)}")

        # 数据层守卫：模式专属字段互斥。路由层已返回 400，这里再兜一道防非路由调用方。
        if resolved_mode == "ad":
            if default_duration is not None:
                raise ValueError("广告/短片项目不持有 default_duration（镜头时长按 target_duration 预算逐镜头规划）")
            if target_duration is not None and (
                not isinstance(target_duration, int) or isinstance(target_duration, bool) or target_duration <= 0
            ):
                raise ValueError(f"target_duration 必须为正整数秒，当前为 {target_duration!r}")
            if brief is not None and not isinstance(brief, str):
                raise ValueError(f"brief 必须是字符串，当前为 {brief!r}")
        else:
            if target_duration is not None:
                raise ValueError("target_duration 仅广告/短片项目（content_mode=ad）可用")
            if brief is not None:
                raise ValueError("brief 仅广告/短片项目（content_mode=ad）可用")

        # schema_version 与 CURRENT_SCHEMA_VERSION 对齐：新项目即最新形态，
        # 避免被启动迁移误处理（如 v0→v1 在"未含 clues 字段"时误清空 scenes/props）。
        from lib.project_migrations import CURRENT_SCHEMA_VERSION

        project = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            # 允许空字符串:前端会以 i18n「未命名项目」兜底显示,避免把 slug
            # 风格的 project_name 固化为用户可见的标题。
            "title": project_title,
            "content_mode": resolved_mode,
            "source_kind": resolved_source_kind,
            "aspect_ratio": aspect_ratio or "9:16",
            "style": style or "",
            "episodes": [],
            "planning_cursor": None,
            "characters": {},
            "scenes": {},
            "props": {},
            "metadata": {
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        }
        if resolved_mode == "ad":
            project["target_duration"] = (
                target_duration if target_duration is not None else self.AD_DEFAULT_TARGET_DURATION
            )
            project["brief"] = brief if brief is not None else ""
            project["episodes"] = [dict(self.AD_SINGLE_EPISODE)]
        if default_duration is not None:
            project["default_duration"] = default_duration
        if style_template_id is not None:
            project["style_template_id"] = style_template_id
        if extras:
            # 数据层守卫：退役的单字段 image_backend 不得写入（解析链不再读取，写回只会
            # 重新制造被静默忽略的 legacy 形态）。路由层已返回 400，这里再兜一道防非路由调用方。
            if "image_backend" in extras:
                raise ValueError("image_backend 已废弃，请改用 image_provider_t2i / image_provider_i2i")
            # extras 只许追加可选字段，不得覆盖上方已校验/已构造的核心字段——
            # 否则非路由调用方可借 extras 绕过模式互斥守卫（如 ad 项目写回 default_duration）。
            reserved = set(project) | {"default_duration", "style_template_id", "target_duration", "brief"}
            forbidden = reserved & set(extras)
            if forbidden:
                raise ValueError(f"extras 不允许覆盖核心字段: {sorted(forbidden)}")
            project.update(extras)

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

        def _mutate(project: dict) -> None:
            # 已存在则更新，否则追加（整段 RMW 在单一 _project_lock 内完成）
            for ep in project["episodes"]:
                if ep["episode"] == episode:
                    ep["title"] = title
                    ep["script_file"] = script_file
                    return
            # 添加新剧集（不包含统计字段，由 StatusCalculator 读时计算）
            project["episodes"].append({"episode": episode, "title": title, "script_file": script_file})
            project["episodes"].sort(key=lambda x: x["episode"])

        return self.update_project(project_name, _mutate)

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

    # ==================== 项目级资产统一 API（character / scene / prop） ====================
    #
    # 这一节的 6 个私有方法按 lib.asset_types.ASSET_SPECS 驱动，统一处理 character /
    # scene / prop 三类项目级资产的桶级读写。下方的 public 方法（add_project_scene /
    # add_prop / get_scene / update_*_sheet 等）全部委托给这些私有方法，签名与异常
    # 100% 兼容旧调用方。

    def _add_asset(self, asset_type: str, project_name: str, name: str, entry: dict) -> bool:
        """新增 entry 到 project[bucket][name]。冲突时返回 False。

        通过 update_project 在单一文件锁内完成 read-modify-write，避免并发新增时的
        lost-update 竞态。
        """
        name = validate_asset_name(name)
        spec = ASSET_SPECS[asset_type]
        added = False

        def _mutate(project):
            nonlocal added
            bucket = project.setdefault(spec.bucket_key, {})
            if name in bucket:
                logger.debug("%s '%s' 已存在于 project.json，跳过", spec.label_zh, name)
                return
            bucket[name] = entry
            added = True

        self.update_project(project_name, _mutate)
        if added:
            logger.info("添加%s: %s", spec.label_zh, name)
        return added

    def _add_assets_batch(self, asset_type: str, project_name: str, entries: dict[str, dict]) -> int:
        """批量新增 entries。已存在的 name 跳过，返回新增数量。

        通过 update_project 在单一文件锁内完成 read-modify-write，避免并发批量新增时
        的 lost-update 竞态。
        """
        spec = ASSET_SPECS[asset_type]
        # 与 upsert_assets 同口径：strip 后等价的 key（{"李白", "  李白  "}）不允许静默
        # 互相覆盖，整批 fail-loud 不落盘，让调用方感知 collision 并去重。
        normalized_entries: dict[str, dict] = {}
        raw_keys_by_normalized: dict[str, str] = {}
        for raw_name, entry in entries.items():
            name = validate_asset_name(raw_name)
            if name in normalized_entries:
                raise ValueError(
                    f"{spec.bucket_key} 的 entries 含规范化后冲突的 name {name!r}："
                    f"原始键 {raw_keys_by_normalized[name]!r} 与 {raw_name!r} 在 strip 后等价"
                )
            normalized_entries[name] = entry
            raw_keys_by_normalized[name] = raw_name
        entries = normalized_entries
        added = 0

        def _mutate(project):
            nonlocal added
            bucket = project.setdefault(spec.bucket_key, {})
            for name, entry in entries.items():
                if name in bucket:
                    logger.debug("%s '%s' 已存在，跳过", spec.label_zh, name)
                    continue
                bucket[name] = entry
                added += 1
                logger.info("添加%s: %s", spec.label_zh, name)

        if entries:
            self.update_project(project_name, _mutate)
        return added

    def upsert_assets(self, project_name: str, table: str, entries: dict[str, dict]) -> dict[str, Any]:
        """按 table（characters/scenes/props）+ name upsert 资产：不存在则新增、存在则改字段。

        在 `update_project` 的单一文件锁内完成 read-modify-write；apply 后、落盘前对结果
        project dict 做 payload 级结构校验，按**「不更坏」语义**裁决：仅当本次 upsert 把原本
        合法的 project 改成非法时才 raise 且**不落盘**（mutation 抛错时 `update_project` 不执行
        atomic_write）；改前已非法（历史遗留脏数据，如空 `style`）则照常放行——否则带历史问题的
        项目会整条 patch_project 路径不可用（旧 `add_assets.py` 报告校验错误也不阻断写入）。
        与剧本写盘统一入口的 `_guard_no_worse` 同源。把「只能加」扩为「可改」。

        返回**诊断 dict**（不是 project 元数据）：``added``（新建条目名列表）、``merged``
        （合并已有条目名列表）、``dropped_fields``（被白名单丢弃的非允许字段，{name: [字段名]}）、
        ``dropped_legacy``（被剔除的历史字段如 type/importance，{name: [字段名]}）。caller
        （MCP tool 层）据此构造对 agent 的明确反馈——silent drop 是设计意图（least privilege），
        但纯 silent 让 agent 误以为 reference_image / sheet_field 写入成功；返回诊断让工具层
        把忽略原因明示给 agent，避免 agent 重复尝试同样会被丢的字段。
        """
        # data_validator 在模块级 import 本模块（effective_mode），故惰性 import 破环。
        from lib.data_validator import DataValidator

        asset_type = self._BUCKET_TO_ASSET_TYPE.get(table)
        if asset_type is None:
            raise ValueError(f"未知资产表: {table!r}，须是 {sorted(self._BUCKET_TO_ASSET_TYPE)} 之一")
        # 拆开两种失败 case 让 agent 错误更精确（之前合并的 "entries 不能为空" 无法区分两者）
        if not isinstance(entries, dict):
            raise ValueError(f"entries 必须是对象（dict），当前为 {type(entries).__name__}")
        if not entries:
            raise ValueError("entries 不能为空（至少需要一个 name → attrs 条目）")
        # 规范化 name：strip 空白后非空，且须是路径安全的单段组件（validate_asset_name，
        # 名称会被拼进文件路径与单段路由参数）。agent 误传 "  李白  " 这种带空格的 name 会让
        # 后续按 name 索引查找（角色生成等）因空格差异 mismatch。非法 name fail-loud。
        # 同时检测 strip 后冲突：{"李白": {...}, "  李白  ": {...}} 规范化后 key 相同 →
        # 后者会 silent overwrite 前者；fail-loud 让 agent 明确感知 collision 并去重。
        normalized_entries: dict[str, dict] = {}
        raw_keys_by_normalized: dict[str, str] = {}
        for raw_name, attrs in entries.items():
            try:
                name = validate_asset_name(raw_name)
            except ValueError as exc:
                raise ValueError(f"{table}: {exc}") from None
            if not isinstance(attrs, dict):
                raise ValueError(f"{table} '{name}' 的内容必须是对象")
            if name in normalized_entries:
                raise ValueError(
                    f"{table} 的 entries 含规范化后冲突的 name {name!r}："
                    f"原始键 {raw_keys_by_normalized[name]!r} 与 {raw_name!r} 在 strip 后等价"
                )
            normalized_entries[name] = attrs
            raw_keys_by_normalized[name] = raw_name

        spec = ASSET_SPECS[asset_type]
        # 字段白名单走 spec 的「agent 权限维度」`agent_editable_extra_fields`，**不复用** schema 维度
        # `extra_string_fields`——后者包括 `reference_image` 这类系统/用户路径字段（与 sheet_field
        # 同性质，更新走 `update_character_reference_image` 专用 API），不该被 agent patch_project 直改。
        # 不允许的字段同样含 `sheet_field`（character_sheet / scene_sheet / prop_sheet，资产生成流水线
        # 在图像就绪后通过 `_update_asset_sheet` 专用 API 回写）以及 spec 之外的任意 key。
        # `_strip_legacy_asset_fields` 处理 type/importance 等历史字段，这层再加白名单形成「最小特权」。
        allowed_fields = {"description", *spec.agent_editable_extra_fields}
        # 收集白名单丢字段 / 历史字段丢弃 给 caller 用于明示 agent。silent drop 仍是设计意图,
        # 但通过返回 dict 把"被丢了什么"显式告诉工具层,工具层据此告知 agent,避免 LLM 重复尝试。
        cleaned: dict[str, dict[str, Any]] = {}
        dropped_fields: dict[str, list[str]] = {}  # name → [被白名单丢的字段]
        dropped_legacy: dict[str, list[str]] = {}  # name → [被 _LEGACY_ASSET_FIELDS 剔除的字段]
        for name, attrs in normalized_entries.items():
            legacy_keys = sorted(set(attrs) & self._LEGACY_ASSET_FIELDS)
            if legacy_keys:
                dropped_legacy[name] = legacy_keys

            entry_clean: dict[str, Any] = {}
            non_allowed: list[str] = []
            for k, v in self._strip_legacy_asset_fields(attrs).items():
                if k in allowed_fields:
                    entry_clean[k] = v
                else:
                    non_allowed.append(k)
                    logger.debug(
                        "upsert_assets: %s '%s' 的字段 %r 不在 agent 可编辑白名单 %s,已忽略",
                        table,
                        name,
                        k,
                        sorted(allowed_fields),
                    )
            if non_allowed:
                dropped_fields[name] = sorted(non_allowed)
            cleaned[name] = entry_clean

        added: list[str] = []
        merged: list[str] = []
        noop: list[str] = []

        def _mutate(project: dict) -> None:
            validator = DataValidator(str(self.projects_root))
            before_errors = set(validator.validate_project_payload(project).errors)  # 改前快照
            bucket = project.setdefault(spec.bucket_key, {})
            if not isinstance(bucket, dict):
                # 历史脏数据：bucket_key 已存在却非 dict（如 list/str）。继续会让下方
                # bucket.get/bucket[name].update 抛含糊的 AttributeError，故先 fail-loud
                # 给出意外类型与 offending key（mutation 物理上无法对非 dict 施加，与「不更坏」无关）。
                raise ValueError(f"project[{spec.bucket_key!r}] 必须是对象，当前为 {type(bucket).__name__}")
            for name, attrs in cleaned.items():
                existing = isinstance(bucket.get(name), dict)
                # 仅对已存在 entry 检测 no-op:全字段被白名单/legacy strip 丢空时 update({})
                # 实际不变,归到 noop 而非 merged 避免「合并 1 个」误报。新 entry 即使
                # cleaned 空也仍走 _build_asset_entry,让 description 缺失的 validator 拒写
                # fail-loud(不能让"无可写字段"变成绕过 entry 创建必填校验的旁路)。
                if existing and not attrs:
                    noop.append(name)
                    continue
                if existing:
                    bucket[name].update(attrs)  # 改：合并字段，保留 sheet 路径等既有字段
                    merged.append(name)
                else:
                    bucket[name] = self._build_asset_entry(asset_type, attrs.get("description", ""), attrs)
                    added.append(name)
            after_errors = set(validator.validate_project_payload(project).errors)
            # 「不更坏」按 error set diff 判定：after 不应比 before 多任何 errors。
            #   - 改前合法、改后非法 → new_errors=全部 after errors → 拒
            #   - 改前已脏、改后相同脏 → new_errors=∅ → 放行（允许带历史脏数据的项目继续 patch）
            #   - 改前已脏、改后引入新错误（如本次 entries 缺 description）→ new_errors≠∅ → 拒
            #   - 改前已脏、改后修复了部分 → new_errors=∅ → 放行（允许 patch 改进历史脏数据）
            # 比单纯比 valid 标志更严：堵住「带历史脏数据的项目里新 entry 的结构错误 piggyback 落盘」。
            new_errors = after_errors - before_errors
            if new_errors:
                raise ValueError("project.json 结构校验失败: " + "; ".join(sorted(new_errors)))

        self.update_project(project_name, _mutate)
        return {
            "added": added,
            "merged": merged,
            "noop": noop,
            "dropped_fields": dropped_fields,
            "dropped_legacy": dropped_legacy,
        }

    # bucket_key（characters/scenes/props）→ 资产类型，从静态 ASSET_SPECS 派生一次，避免每次 upsert 重建。
    _BUCKET_TO_ASSET_TYPE = {spec.bucket_key: t for t, spec in ASSET_SPECS.items()}

    _LEGACY_ASSET_FIELDS = frozenset({"type", "importance"})

    @classmethod
    def _strip_legacy_asset_fields(cls, attrs: dict) -> dict:
        """剔除旧式 type/importance 字段（schema 演进遗留），返回新 dict。"""
        return {k: v for k, v in attrs.items() if k not in cls._LEGACY_ASSET_FIELDS}

    def _update_asset_sheet(self, asset_type: str, project_name: str, name: str, sheet_path: str) -> dict:
        """更新资产 sheet 字段路径。资产不存在抛 KeyError。

        通过 update_project 在单一文件锁内完成 read-modify-write，避免与并发 add /
        update 任务的 lost-update 竞态。
        """
        spec = ASSET_SPECS[asset_type]

        def _mutate(project):
            bucket = project.get(spec.bucket_key)
            if bucket is None or name not in bucket:
                raise KeyError(f"{spec.label_zh} '{name}' 不存在")
            bucket[name][spec.sheet_field] = sheet_path

        return self.update_project(project_name, _mutate)

    def _get_asset(self, asset_type: str, project_name: str, name: str) -> dict:
        """获取资产定义。不存在抛 KeyError。"""
        spec = ASSET_SPECS[asset_type]
        project = self.load_project(project_name)
        bucket = project.get(spec.bucket_key)
        if bucket is None or name not in bucket:
            raise KeyError(f"{spec.label_zh} '{name}' 不存在")
        return bucket[name]

    def _get_pending_assets(self, asset_type: str, project_name: str) -> list[dict]:
        """无 sheet 字段或 sheet 文件不存在的资产列表。"""
        spec = ASSET_SPECS[asset_type]
        project = self.load_project(project_name)
        project_dir = self.get_project_path(project_name)
        pending = []
        for name, entry in (project.get(spec.bucket_key) or {}).items():
            sheet = entry.get(spec.sheet_field)
            if not sheet or not (project_dir / sheet).exists():
                pending.append({"name": name, **entry})
        return pending

    def _get_asset_path(self, asset_type: str, project_name: str, filename: str) -> Path:
        """获取资产文件在项目目录下的绝对路径。"""
        spec = ASSET_SPECS[asset_type]
        return self.get_project_path(project_name) / spec.subdir / filename

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
        name = validate_asset_name(name)

        def _mutate(project: dict) -> None:
            project["characters"][name] = {
                "description": description,
                "voice_style": voice_style or "",
                "character_sheet": character_sheet or "",
            }

        return self.update_project(project_name, _mutate)

    def update_project_character_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        """更新项目级角色设计图路径"""
        return self._update_asset_sheet("character", project_name, name, sheet_path)

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

        def _mutate(project: dict) -> None:
            if "characters" not in project or char_name not in project["characters"]:
                raise KeyError(f"角色 '{char_name}' 不存在")
            project["characters"][char_name]["reference_image"] = ref_path

        return self.update_project(project_name, _mutate)

    def get_project_character(self, project_name: str, name: str) -> dict:
        """获取项目级角色定义"""
        return self._get_asset("character", project_name, name)

    # ==================== 场景管理（scene） ====================

    def update_scene_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        """更新场景设计图路径"""
        return self._update_asset_sheet("scene", project_name, name, sheet_path)

    def get_scene(self, project_name: str, name: str) -> dict:
        """获取场景定义"""
        return self._get_asset("scene", project_name, name)

    def get_pending_project_scenes(self, project_name: str) -> list[dict]:
        """无 scene_sheet 或文件不存在的场景。"""
        return self._get_pending_assets("scene", project_name)

    def get_scene_path(self, project_name: str, filename: str) -> Path:
        """获取场景设计图路径"""
        return self._get_asset_path("scene", project_name, filename)

    # ==================== 道具管理（prop） ====================

    def update_prop_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        """更新道具设计图路径"""
        return self._update_asset_sheet("prop", project_name, name, sheet_path)

    def get_prop(self, project_name: str, name: str) -> dict:
        """获取道具定义"""
        return self._get_asset("prop", project_name, name)

    def get_pending_project_props(self, project_name: str) -> list[dict]:
        """无 prop_sheet 或文件不存在的道具。"""
        return self._get_pending_assets("prop", project_name)

    def get_prop_path(self, project_name: str, filename: str) -> Path:
        """获取道具设计图路径"""
        return self._get_asset_path("prop", project_name, filename)

    def get_pending_characters(self, project_name: str) -> list[dict]:
        """获取待生成设计图的角色列表（无 character_sheet 或文件不存在）"""
        return self._get_pending_assets("character", project_name)

    # ==================== 产品管理（product） ====================

    def update_product_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        """更新产品标准参考图（product sheet）路径"""
        return self._update_asset_sheet("product", project_name, name, sheet_path)

    def get_product(self, project_name: str, name: str) -> dict:
        """获取产品定义"""
        return self._get_asset("product", project_name, name)

    def get_pending_project_products(self, project_name: str) -> list[dict]:
        """无 product_sheet 或文件不存在的产品。"""
        return self._get_pending_assets("product", project_name)

    def get_product_path(self, project_name: str, filename: str) -> Path:
        """获取产品图片路径"""
        return self._get_asset_path("product", project_name, filename)

    def add_product_reference_image(self, project_name: str, product_name: str, ref_path: str) -> dict:
        """向产品的 reference_images 列表追加一张原图路径（已存在则不重复追加）。

        原图是产品保真的验收锚点，只增不改；删除/重排走资产 PATCH 通道。
        """

        def _mutate(project: dict) -> None:
            bucket = project.get("products")
            if bucket is None or product_name not in bucket:
                raise KeyError(f"产品 '{product_name}' 不存在")
            refs = bucket[product_name].setdefault("reference_images", [])
            if not isinstance(refs, list):
                raise ValueError(
                    f"products['{product_name}'].reference_images 必须是列表，当前为 {type(refs).__name__}"
                )
            if ref_path not in refs:
                refs.append(ref_path)

        return self.update_project(project_name, _mutate)

    # ==================== 角色/场景/道具直接写入工具 ====================

    @staticmethod
    def _build_asset_entry(asset_type: str, description: str, source: dict | None = None) -> dict:
        """按 ASSET_SPECS 构造 entry：description + sheet 字段为空 + extra 字段从 source 取或默认。

        source 为 None 时（add_character 等单条新增），仅写入 spec 中声明的 extra 字段
        默认值（字符串字段空串、列表字段空列表）；source 提供时（batch 新增），同时允许
        覆盖 sheet 字段。source 中的非法类型不在此处修正，由落盘前的结构校验 fail-loud。
        """
        spec = ASSET_SPECS[asset_type]
        data = source or {}
        entry: dict = {"description": description, spec.sheet_field: data.get(spec.sheet_field, "")}
        for field in spec.extra_string_fields:
            entry[field] = data.get(field, "")
        for field in spec.extra_list_fields:
            value = data.get(field)
            if isinstance(value, list):
                entry[field] = list(value)  # 复制，避免 entry 与调用方共享同一列表对象
            elif value is None:
                entry[field] = []
            else:
                entry[field] = value  # 非法类型透传，由落盘前结构校验 fail-loud
        return entry

    def add_character(self, project_name: str, name: str, description: str, voice_style: str = "") -> bool:
        """直接添加角色到 project.json。已存在返回 False。"""
        entry = self._build_asset_entry("character", description, {"voice_style": voice_style})
        return self._add_asset("character", project_name, name, entry)

    def add_project_scene(self, project_name: str, name: str, description: str) -> bool:
        """直接添加场景到 project.json。已存在返回 False。"""
        entry = self._build_asset_entry("scene", description)
        return self._add_asset("scene", project_name, name, entry)

    def add_prop(self, project_name: str, name: str, description: str) -> bool:
        """直接添加道具到 project.json。已存在返回 False。"""
        entry = self._build_asset_entry("prop", description)
        return self._add_asset("prop", project_name, name, entry)

    def add_product(self, project_name: str, name: str, description: str, brand: str = "") -> bool:
        """直接添加产品到 project.json。已存在返回 False。"""
        entry = self._build_asset_entry("product", description, {"brand": brand})
        return self._add_asset("product", project_name, name, entry)

    def add_characters_batch(self, project_name: str, characters: dict[str, dict]) -> int:
        """批量添加角色到 project.json。已存在的跳过，返回新增数量。"""
        entries = {
            name: self._build_asset_entry("character", data.get("description", ""), data)
            for name, data in characters.items()
        }
        return self._add_assets_batch("character", project_name, entries)

    def add_scenes_batch(self, project_name: str, scenes: dict[str, dict]) -> int:
        """批量添加场景到 project.json。已存在的跳过，返回新增数量。"""
        entries = {
            name: self._build_asset_entry("scene", data.get("description", ""), data) for name, data in scenes.items()
        }
        return self._add_assets_batch("scene", project_name, entries)

    def add_props_batch(self, project_name: str, props: dict[str, dict]) -> int:
        """批量添加道具到 project.json。已存在的跳过，返回新增数量。"""
        entries = {
            name: self._build_asset_entry("prop", data.get("description", ""), data) for name, data in props.items()
        }
        return self._add_assets_batch("prop", project_name, entries)

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

        # 道具参考图
        for prop in scene.get("props_in_scene", []):
            prop_data = project.get("props", {}).get(prop, {})
            sheet = prop_data.get("prop_sheet")
            if sheet:
                sheet_path = project_dir / sheet
                if sheet_path.exists():
                    refs.append(sheet_path)

        return refs

    # ==================== 项目概述生成 ====================

    def _read_source_files(self, project_name: str, max_chars: int = 50000) -> str:
        """
        读取项目 source 目录下的所有 UTF-8 文本文件内容。

        非 UTF-8 文件会抛 SourceDecodeError —— 上传路径已统一规范化为 UTF-8，
        启动迁移已修历史项目；这里若仍遇到非 UTF-8，说明用户绕过 API 直接拷贝
        文件，需显式报错而非"源目录为空"误导。
        """
        from .source_loader.errors import SourceDecodeError

        project_dir = self.get_project_path(project_name)
        source_dir = project_dir / "source"

        if not source_dir.exists():
            return ""

        contents = []
        total_chars = 0
        for file_path in sorted(source_dir.glob("*")):
            if not (file_path.is_file() and file_path.suffix.lower() in SOURCE_TEXT_SUFFIXES):
                continue

            raw = file_path.read_bytes()
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SourceDecodeError(
                    filename=file_path.name,
                    tried_encodings=["utf-8"],
                ) from exc

            remaining = max_chars - total_chars
            if remaining <= 0:
                break
            if len(content) > remaining:
                content = content[:remaining]
            contents.append(f"--- {file_path.name} ---\n{content}")
            total_chars += len(content)

        return "\n\n".join(contents)

    async def generate_overview(self, project_name: str) -> dict:
        """
        使用 Gemini API 异步生成项目概述

        Args:
            project_name: 项目名称

        Returns:
            生成的 overview 字典，包含 synopsis, genre, theme, world_setting, generated_at
        """
        from .prompt_builders_script import build_overview_prompt
        from .text_backends.base import TextGenerationRequest, TextTaskType
        from .text_generator import TextGenerator

        # 读取源文件内容
        source_content = self._read_source_files(project_name)
        if not source_content:
            raise ValueError("source 目录为空，无法生成概述")

        # 创建 TextGenerator（自动追踪用量）
        generator = await TextGenerator.create(TextTaskType.OVERVIEW, project_name)

        # 调用 TextGenerator（Structured Outputs）。source_kind=screenplay 时翻为「提取优先」：
        # 作者写下的创作方案前言优先照用，缺失才退回从正文归纳（novel 行为不变）。
        source_kind = resolve_source_kind(self.load_project(project_name))
        prompt = build_overview_prompt(source_content, source_kind=source_kind)

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
        overview_dict["generated_at"] = datetime.now(UTC).isoformat()

        # 保存到 project.json（RMW 在单一 _project_lock 内完成，避免并发覆盖其它字段）
        def _mutate(project: dict) -> None:
            project["overview"] = overview_dict
            project["source_language"] = overview_dict["language"]

        self.update_project(project_name, _mutate)

        logger.info("项目概述已生成并保存")
        return overview_dict
