"""
状态和统计字段的实时计算器

提供读时计算的统计字段，避免存储冗余数据。
配合 ProjectManager 使用，在 API 响应时注入计算字段。
"""

import logging

from lib.episode_paths import (
    REFERENCE_VIDEO_STEP1_FILENAME,
    REFERENCE_VIDEO_STEP1_QUARANTINE_FILENAME,
    STEP1_FILENAMES,
    STEP1_LEGACY_FILENAMES,
    episode_drafts_dir,
)
from lib.path_safety import safe_exists
from lib.script_models import NON_PERSISTED_COUNT_KEYS, get_generated_assets, script_duration_total
from lib.script_skeleton import SKELETONS, resolve_declared_kind

logger = logging.getLogger(__name__)

# 缺 creation_type 声明的老脚本：按主结构鸭子类型兜底探测的骨架种类，顺序固定
# segments > scenes > shots（video_units 不参与——按声明分派，不嗅探残留派生索引）。
_LEGACY_DUCK_TYPE_KINDS: tuple[str, ...] = ("segments", "scenes", "shots")

# 「是否分过段」判定中兼认旧版 .md 别名的 creation_type：narration 的旧 step1_segments.md
# 代表真实的分段工作、兼认；drama 的旧 .md 早于内容抽取前移（见 ADR 0041），不再视为有效
# step1——仅 .md 无剧本 JSON 的在制品会被路由回重跑 step1，故不在此集合。这与 gate 只认结构化
# .json、web 读取层兼认双模式旧 .md 的语义有意不同。
_SEGMENTED_LEGACY_MODES: frozenset[str] = frozenset({"narration"})


def _draft_candidates(creation_type: str, generation_mode: str | None = None) -> tuple[str, ...]:
    """剧本缺失时按 (creation_type, generation_mode) 探测的 step1 草稿候选文件名（任一存在即视为已分段）。

    结构化文件名取自单一真相源 ``lib.episode_paths.STEP1_FILENAMES``，新增 creation_type 自动覆盖。
    ad 不走拆分中间稿（brief 不经 source_loader），返回空元组表示无草稿可探测；未知值沿用历史
    兜底探 drama 结构化草稿名。旧版 .md 仅对 ``_SEGMENTED_LEGACY_MODES`` 内的模式附加。

    reference_video 是跨 creation_type 的 generation_mode 维度（与 ``lib.script_review.step1_kind``
    同口径，项目路线优先于 creation_type），命中时探测其专属结构化草稿名
    ``REFERENCE_VIDEO_STEP1_FILENAME`` 而非 creation_type 对应名——否则 rv 项目的
    ``step1_reference_units.json`` 永远探测不到，script_status 停留 none，web 路由卡在源文审阅页
    进不了 ``ScriptReviewGate``。旧版自由文本别名仅供读取 / 浏览层兼认（见 episode_paths 注释），
    生成侧遇到会拒绝并提示重跑拆分，此处不纳入——避免误报「已分段」掩盖需要重跑的存量草稿。

    ``REFERENCE_VIDEO_STEP1_QUARANTINE_FILENAME`` 同样纳入探测：首次拆分若未过校验，只会
    产出隔离草稿、正式文件从未写过，只探正式文件名会让 script_status 停在 none、web 路由
    落到 ``EpisodeSourceReview`` 而不是挂着隔离态预览面板的 ``ScriptReviewGate``——用户见不到
    违约详情与修复入口，恰是隔离草稿最常见的产出路径（首轮拆分失败）。
    """
    if creation_type == "ad":
        return ()
    if generation_mode == "reference_video":
        return (REFERENCE_VIDEO_STEP1_FILENAME, REFERENCE_VIDEO_STEP1_QUARANTINE_FILENAME)
    primary = STEP1_FILENAMES.get(creation_type) or STEP1_FILENAMES["drama"]
    legacy = STEP1_LEGACY_FILENAMES.get(creation_type, ()) if creation_type in _SEGMENTED_LEGACY_MODES else ()
    return (primary, *legacy)


def _unit_items(script: dict) -> list[dict]:
    """取 ``video_units`` 数组，容器非数组、成员非对象（外部编辑 / 归档导入的脏数据）一律剔除。

    读时计算不抛错，数据契约校验归 ``DataValidator``：容器与成员都要归一，
    ``{"video_units": {...}}`` 会让下游按 dict 的键迭代、``["bad"]`` 会让下游对 str 调
    ``get``，两者都把项目详情读取变成 500、整个项目不可查看。
    """
    items = script.get("video_units")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _shot_count(units: list[dict]) -> int:
    """参考路线的镜头数：各 unit 内嵌 ``shots`` 之和。非数组容器（脏数据）计 0。"""
    return sum(len(unit["shots"]) for unit in units if isinstance(unit.get("shots"), list))


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
    def _select_kind_and_items(cls, script: dict, generation_mode: str | None) -> tuple[str, list[dict]]:
        """返回 ``(骨架种类, items)``，骨架种类 ∈ {segments, scenes, shots, video_units}。

        **主路径**按剧本级 ``creation_type`` 与调用方传入的项目级 ``generation_mode`` 走规范解析
        （``resolve_declared_kind``）——计分必须按声明分派、不嗅探数据形状（残留派生索引
        不得污染 storyboard 计分）：``video_units`` 恒按声明取 ``video_units`` 数组、不回退。
        **legacy 容忍**（本模块本地，不进解析器本体）：缺失/未知 creation_type 的存量剧本，
        保留按项目路线的 reference 短路 + 主结构鸭子类型兜底阶梯（现状行为）。
        """
        creation_type = script.get("creation_type")
        try:
            kind = resolve_declared_kind(creation_type, generation_mode)
        except ValueError:
            kind = None

        if kind == "video_units":
            # 按声明分派：不回退鸭子类型，残留 segments/scenes 索引不得抢走参考集计分。
            return "video_units", _unit_items(script)
        if kind is not None:
            items = script.get(kind)
            if isinstance(items, list):
                return kind, items
        elif generation_mode == "reference_video" and "video_units" in script:
            # 缺失/未知 creation_type 但项目走参考路线：沿用历史 legacy 容忍，按路线取 video_units。
            # 以 video_units 键在场为前提（与 ``ensure_route_skeleton`` 同判据），避免无条件
            # 短路把遗留主骨架的计分抢成空 video_units。
            return "video_units", _unit_items(script)

        for legacy_kind in _LEGACY_DUCK_TYPE_KINDS:
            if isinstance(script.get(legacy_kind), list):
                return legacy_kind, script.get(legacy_kind, [])

        return (kind or "segments"), []

    def calculate_episode_stats(self, project_name: str, script: dict, *, generation_mode: str | None = None) -> dict:
        """计算单集的统计信息 — 按骨架种类分派。

        ``generation_mode`` 由调用方从 project.json 的项目路线字段传入——剧本不携带路线信息，
        路线是项目级唯一事实。reference_video 路线统一按 ``video_units`` 计分。
        """
        kind, items = self._select_kind_and_items(script, generation_mode)

        if kind == "video_units":
            return self._calculate_reference_video_stats(items)

        storyboard_done = sum(1 for i in items if get_generated_assets(i).get("storyboard_image"))
        video_done = sum(1 for i in items if get_generated_assets(i).get("video_clip"))
        total = len(items)

        if video_done == total and total > 0:
            status = "completed"
        elif storyboard_done > 0 or video_done > 0:
            status = "in_production"
        else:
            status = "draft"

        return {
            "storyboard_count": total,
            "status": status,
            "duration_seconds": script_duration_total(kind, items),
            "storyboards": {"total": total, "completed": storyboard_done},
            "videos": {"total": total, "completed": video_done},
        }

    @staticmethod
    def _calculate_reference_video_stats(units: list[dict]) -> dict:
        """Reference-video scripts are scored by video_units[].generated_assets.video_clip."""
        total = len(units)
        video_done = sum(1 for u in units if get_generated_assets(u).get("video_clip"))

        if total == 0:
            status = "draft"
        elif video_done == total:
            status = "completed"
        elif video_done > 0:
            status = "in_production"
        else:
            status = "draft"

        return {
            "video_unit_count": total,
            "shot_count": _shot_count(units),
            "status": status,
            "duration_seconds": script_duration_total("video_units", units),
            "storyboards": {"total": total, "completed": 0},
            "videos": {"total": total, "completed": video_done},
        }

    def _load_episode_script(
        self,
        project_name: str,
        episode_num: int,
        script_file: str,
        *,
        creation_type: str = "narration",
        generation_mode: str | None = None,
        preloaded_scripts: dict[str, dict] | None = None,
    ) -> tuple:
        """加载单集剧本，返回 (script_status, script|None)，避免重复读取文件。
        script_status: 'generated' | 'segmented' | 'none'

        若 ``preloaded_scripts`` 提供且 ``script_file`` 命中其 key，则直接复用预加载
        结果，跳过一次 JSON 解析。缺失时回退到 ``pm.load_script``，保持原兜底语义。
        ``generation_mode`` 传项目路线字段，驱动 rv 项目的草稿探测（见 ``_draft_candidates``）。
        """
        if preloaded_scripts is not None and script_file in preloaded_scripts:
            return "generated", preloaded_scripts[script_file]
        try:
            script = self.pm.load_script(project_name, script_file)
            return "generated", script
        except FileNotFoundError:
            project_dir = self.pm.get_project_path(project_name)
            try:
                safe_num = int(episode_num)
            except (ValueError, TypeError):
                return "none", None
            draft_filenames = _draft_candidates(creation_type, generation_mode)
            if not draft_filenames:
                return "none", None
            drafts_dir = episode_drafts_dir(project_dir, safe_num)
            segmented = any((drafts_dir / name).exists() for name in draft_filenames)
            return ("segmented" if segmented else "none"), None
        except ValueError as e:
            logger.warning(
                "剧本 JSON 损坏或路径无效，跳过状态计算 project=%s file=%s: %s",
                project_name,
                script_file,
                e,
            )
            return "generated", None

    def calculate_current_phase(
        self,
        project: dict,
        episodes_stats: list[dict],
        *,
        assets_completed: int = 0,
    ) -> str:
        """根据项目和集状态推断当前阶段（按实际产物倒序判定）。

        判定顺序（高优先级在前）：
        1. 已有任意一集脚本生成 → ``scripting`` / ``production`` / ``completed``
        2. 已有任意分段草稿、资产设计图（character/scene/prop sheet）或 overview
           → ``worldbuilding``
        3. 其它（全新项目）→ ``setup``

        这避免了「用户跳过 overview 直接做剧本/分镜/视频，阶段却卡在 setup」
        的体验问题——overview 只是 worldbuilding 的一种入口信号，而不是
        离开 setup 的必经门票。
        """
        any_generated = False
        all_generated = bool(episodes_stats)
        any_segmented = False
        all_completed = bool(episodes_stats)
        for s in episodes_stats:
            script_status = s["script_status"]
            if script_status == "generated":
                any_generated = True
            else:
                all_generated = False
                if script_status == "segmented":
                    any_segmented = True
            if s.get("status") != "completed":
                all_completed = False

        if all_generated:
            return "completed" if all_completed else "production"
        if any_generated:
            return "scripting"
        if any_segmented or assets_completed > 0 or project.get("overview"):
            return "worldbuilding"
        return "setup"

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
            "storyboard_count": 0,
            "video_unit_count": 0,
            "shot_count": 0,
            "duration_seconds": 0,
        }

    def _build_episodes_stats(
        self,
        project_name: str,
        project: dict,
        *,
        preloaded_scripts: dict[str, dict] | None = None,
    ) -> list[dict]:
        """遍历所有集数，加载剧本并计算每集统计。

        ``preloaded_scripts`` 按 ``episode['script_file']`` 原样作为 key，命中则
        跳过 pm.load_script；未命中仍走磁盘加载 + 草稿探测的既有兜底路径。
        """
        creation_type = project.get("creation_type", "narration")
        generation_mode = project.get("generation_mode")
        episodes_stats = []
        for ep in project.get("episodes", []):
            # 账本标 stale 的集（重新规划后原文范围已失效）：读时状态回退为待预处理，
            # 驱动重做流程；剧本/媒体产物不删除，重做沿现有覆盖/版本机制替换。
            if ep.get("ledger_status") == "stale":
                episodes_stats.append(self._make_fallback_ep_stats("none"))
                continue

            script_file = ep.get("script_file", "")
            episode_num = ep.get("episode", 0)

            if script_file:
                script_status, script = self._load_episode_script(
                    project_name,
                    episode_num,
                    script_file,
                    creation_type=creation_type,
                    generation_mode=generation_mode,
                    preloaded_scripts=preloaded_scripts,
                )
            else:
                script_status, script = "none", None

            if script_status == "generated" and script is not None:
                ep_stats = self.calculate_episode_stats(project_name, script, generation_mode=generation_mode)
                if ep_stats["status"] == "draft":
                    ep_stats["status"] = "scripted"
                ep_stats["script_status"] = "generated"
            else:
                ep_stats = self._make_fallback_ep_stats(script_status)
            episodes_stats.append(ep_stats)
        return episodes_stats

    def calculate_project_status(
        self,
        project_name: str,
        project: dict,
        *,
        _preloaded_episodes_stats: list[dict] | None = None,
        preloaded_scripts: dict[str, dict] | None = None,
    ) -> dict:
        """
        计算项目整体状态（用于列表 API）。

        Args:
            _preloaded_episodes_stats: 若已由 enrich_project 预先计算，直接传入以避免重复 I/O。
            preloaded_scripts: 调用方（如 list_projects）已加载的剧本字典，key 为
                ``episode['script_file']`` 原值，value 为剧本 JSON。
                命中即跳过 pm.load_script，避免与 resolve_project_cover 重复 I/O。

        Returns:
            ProjectStatus 字典：current_phase, phase_progress, characters, scenes, props, episodes_summary
        """
        project_dir = self.pm.get_project_path(project_name)

        # 角色统计
        chars = project.get("characters", {})
        chars_total = len(chars)
        chars_done = sum(1 for c in chars.values() if safe_exists(project_dir, c.get("character_sheet", "")))

        # 场景统计
        scenes = project.get("scenes", {})
        scenes_total = len(scenes)
        scenes_done = sum(1 for s in scenes.values() if safe_exists(project_dir, s.get("scene_sheet", "")))

        # 道具统计
        props = project.get("props", {})
        props_total = len(props)
        props_done = sum(1 for p in props.values() if safe_exists(project_dir, p.get("prop_sheet", "")))

        # 每集状态：优先使用预加载数据，否则自行加载
        if _preloaded_episodes_stats is not None:
            episodes_stats = _preloaded_episodes_stats
        else:
            episodes_stats = self._build_episodes_stats(project_name, project, preloaded_scripts=preloaded_scripts)

        phase = self.calculate_current_phase(
            project,
            episodes_stats,
            assets_completed=chars_done + scenes_done + props_done,
        )
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
            # 先清后注：注入只覆盖当前骨架那一个计数键，归档带进来的另一族计数否则会以陈旧值留在响应里
            for key in NON_PERSISTED_COUNT_KEYS:
                ep.pop(key, None)
            ep.update(ep_stats)

        # 传入预加载的 episodes_stats，避免 calculate_project_status 重复加载剧本
        project["status"] = self.calculate_project_status(
            project_name, project, _preloaded_episodes_stats=episodes_stats
        )
        return project

    def enrich_script(self, script: dict, *, generation_mode: str | None = None) -> dict:
        """
        为剧本数据注入计算字段

        不会修改原始 JSON 文件，仅用于 API 响应。

        Args:
            script: 原始剧本数据
            generation_mode: 项目路线（project.json 字段），与 ``calculate_episode_stats``
                同口径定骨架；缺省时按分镜族解析。

        Returns:
            注入计算字段后的剧本数据
        """
        kind, items = self._select_kind_and_items(script, generation_mode)
        total_duration = script_duration_total(kind, items)

        # 注入 metadata 计算字段
        if "metadata" not in script:
            script["metadata"] = {}

        # 未走过迁移或从归档带进来的剧本否则会同时带着两套计数返回给前端
        for key in NON_PERSISTED_COUNT_KEYS:
            script["metadata"].pop(key, None)
        if kind == "video_units":
            script["metadata"]["video_unit_count"] = len(items)
            script["metadata"]["shot_count"] = _shot_count([item for item in items if isinstance(item, dict)])
        else:
            script["metadata"]["storyboard_count"] = len(items)
        script["metadata"]["estimated_duration_seconds"] = total_duration
        script["duration_seconds"] = total_duration  # 读时注入，与 metadata 保持同步

        # 聚合 characters_in_episode / scenes_in_episode / props_in_episode（仅用于 API 响应，不存储）
        chars_set = set()
        scenes_set = set()
        props_set = set()

        if kind == "video_units":
            for item in items:
                # 容器与条目都按脏数据处理：unit 本身已由 ``_unit_items`` 归一，但 references
                # 是 unit 内部字段，外部编辑可留下非数组容器或非对象条目，聚合时照样会把项目
                # 详情读取变成 500。与本模块读时不抛错的口径一致，跳过而不部分计入。
                refs = item.get("references")
                if not isinstance(refs, list):
                    continue
                for ref in refs:
                    if not isinstance(ref, dict):
                        continue
                    ref_type = ref.get("type")
                    name = ref.get("name")
                    # 只收非空字符串：list / dict 名会在 add 处抛 unhashable，数字名会让下游
                    # sorted() 拿混类型集合抛比较错误，两者同样让项目详情读取失败。
                    if not isinstance(name, str) or not name:
                        continue
                    if ref_type == "character":
                        chars_set.add(name)
                    elif ref_type == "scene":
                        scenes_set.add(name)
                    elif ref_type == "prop":
                        props_set.add(name)
        else:
            # 此分支 kind 必为 storyboard 骨架（segments/scenes/shots，_select 已归一），
            # chars_field 非 None；video_units 已在上分支按 references 派生角色。
            char_field = SKELETONS[kind].chars_field
            for item in items:
                if char_field is not None:
                    chars_set.update(item.get(char_field, []))
                scenes_set.update(item.get("scenes", []))
                props_set.update(item.get("props", []))

        script["characters_in_episode"] = sorted(chars_set)
        script["scenes_in_episode"] = sorted(scenes_set)
        script["props_in_episode"] = sorted(props_set)

        return script
