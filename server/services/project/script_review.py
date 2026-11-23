"""script_plan→prompt_authoring web 内容确认的服务层：确认状态读取、结构化中间态编辑、确认动作。

纯 gate 逻辑（适用性 / 指纹 / 状态派生）在 ``lib.script.script_review``；本层叠加 ProjectManager
持久化（确认指纹落 project.json ``episodes[i].script_plan_review``）与结构化内容的 Pydantic 校验、落盘。

确认即把脚本规划整份机械转为正式脚本（全部条目待编写），并与确认记录同一次写入落盘；该集已有
正式脚本时确认就是覆盖，须调用方显式认可。提示词编写（剧本视觉生成）由 Agent 的
``generate_episode_script`` 工具执行，只读正式脚本，不经内容确认门禁。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from lib.config.resolver import ConfigResolver
from lib.episode.episode_target_duration import project_episode_target_duration
from lib.generation.video_request_facts import (
    VideoRequestFactsError,
    VideoRequestFactsFailure,
    reference_migration_durations,
)
from lib.infra.async_thread import run_sync_transaction
from lib.infra.json_io import load_json_or_none
from lib.project.project_manager import ProjectManager, ScriptWriteConflict
from lib.script import script_review
from lib.script.draft_quarantine import (
    DRAFT_OWNER_AGENT,
    DRAFT_OWNER_USER,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_TO_DOC_TYPE,
    draft_owner,
    draft_revision,
    quarantine_path,
    read_quarantine,
    violation_entries,
)
from lib.script.plan_new_assets import NEW_ASSETS_FIELD, NewAssetsError, UnregisteredReferencesError, resolve_new_assets
from lib.script.script_generator import ScriptGenerator, UnitDurationOffTier
from lib.script.script_models import DramaNormalizedScript, NarrationScriptPlanDraft, ReferenceScriptPlanDraft
from lib.speech.speech_composition import SpeechAdmission, SpeechAdmissionError, admit_script_unit
from server.services.tasks.video_caps import (
    reference_request_facts_lookup,
    reference_unit_capabilities,
)
from server.text_generation import fetch_reference_split_caps, reference_soft_violations, soft_violation_entries

logger = logging.getLogger(__name__)

_SCRIPT_PLAN_CONTENT_MODEL: dict[str, type[BaseModel]] = {
    "drama": DramaNormalizedScript,
    "narration": NarrationScriptPlanDraft,
    "reference_video": ReferenceScriptPlanDraft,
}


class ScriptReviewError(Exception):
    """gate 操作的领域错误。``code`` 供 router 映射 HTTP 状态与 i18n key；``message`` 为技术细节。"""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        admission: SpeechAdmission | None = None,
        overwrite: dict[str, Any] | None = None,
        script_filename: str | None = None,
        problem: VideoRequestFactsFailure | None = None,
        params: Mapping[str, object] | None = None,
    ):
        super().__init__(message or code)
        self.code = code
        self.message = message
        self.admission = admission
        self.problem = problem
        self.overwrite = overwrite
        self.script_filename = script_filename
        self.params: dict[str, object] = dict(params or {})


def _require_changed_speech_admitted(kind: str, previous: object, candidate: object) -> None:
    """Reject only new or edited speech content, leaving legacy mixed metadata editable."""

    if kind == "drama":
        root, skeleton, id_field, speech_fields = "scenes", "scenes", "scene_id", ("utterances",)
    elif kind == "reference_video":
        root, skeleton, id_field, speech_fields = "units", "video_units", "unit_id", ("text",)
    else:
        return
    if not isinstance(candidate, dict) or not isinstance(candidate.get(root), list):
        return
    previous_units = previous.get(root) if isinstance(previous, dict) else None
    previous_by_id = {
        unit.get(id_field): unit
        for unit in previous_units or []
        if isinstance(unit, dict) and isinstance(unit.get(id_field), str)
    }
    for unit in candidate[root]:
        if not isinstance(unit, dict) or not isinstance(unit.get(id_field), str):
            continue
        old = previous_by_id.get(unit[id_field])
        speech_changed = old is None or any(old.get(field) != unit.get(field) for field in speech_fields)
        if not speech_changed:
            if isinstance(old, dict) and old.get("needs_replan") is True:
                unit["needs_replan"] = True
            continue
        admission = admit_script_unit(skeleton, unit, ignore_marker=True)
        if not admission.allowed:
            raise ScriptReviewError("speech_admission", admission=admission)
        unit.pop("needs_replan", None)


class ScriptReviewService:
    """封装 script_plan→prompt_authoring 内容确认的读写。router 与测试经此操作 gate，不直接碰文件 / project.json。"""

    def __init__(self, pm: ProjectManager, *, config_resolver: ConfigResolver | None = None):
        self.pm = pm
        self.config_resolver = config_resolver

    def _resolve_script_plan_model(self, project: dict[str, Any], episode: int) -> tuple[str, type[BaseModel]]:
        """该集 script_plan 变体 + 结构校验模型；不适用 gate（无结构化 script_plan）时抛 not_applicable。

        变体判定单一真相源在 ``script_review.script_plan_kind``（reference_video 按项目生成模式优先，
        跨 content_mode）；本层据此选 Pydantic 模型。返回变体名供调用方按变体分流。
        """
        kind = script_review.script_plan_kind(project)
        if kind is None:
            raise ScriptReviewError("not_applicable")
        return kind, _SCRIPT_PLAN_CONTENT_MODEL[kind]

    def _require_episode(self, project_name: str, project: dict[str, Any], episode: int) -> dict[str, Any]:
        """gate 适用时校验该集已在 project.json ``episodes[]`` 登记，返回 project。

        与 ``confirm`` 的写入前置一致：避免 ``get_state`` 把未登记分集误报成 no_script_plan、
        ``save_content`` 给未登记分集写出永远无法与 project.json 关联的孤儿 script_plan 文件。
        集只经显式登记进入账本，``source/`` 里有同名集文件不算登记。
        """
        if script_review.find_episode(project, episode) is None:
            raise ScriptReviewError("episode_not_found")
        return project

    async def _resolve_supported_durations(self, project: dict[str, Any]) -> list[int] | None:
        """结构收编用的时长全集：参考路线取 r2v 与 i2v 两桶视频请求事实声明全集的并集。

        任一桶解析不出时为 None——读时迁移只做结构区间 clamp、不按另一桶的档位改写用户秒数，
        gate 面板也不提供可选档位；档位偏移由随后的逐单元事实校验阻断。

        调用方须在取 ``self.pm.file_lock`` **之前** await 本方法：那把锁是阻塞式文件锁，跨
        await 持有会连带把事件循环上的其它协程挡在锁外。
        """
        if script_review.script_plan_kind(project) != "reference_video":
            return None
        request_facts = reference_request_facts_lookup(project, self.config_resolver)
        return reference_migration_durations(await request_facts("r2v"), await request_facts("i2v"))

    def _read_script_plan_migrated(
        self,
        project_name: str,
        project: dict[str, Any],
        episode: int,
        path: Path,
        project_path: Path,
        supported_durations: list[int] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """读结构化 script_plan，并对参考生视频草稿做一次性时长收编迁移；返回 ``(内容, 最新 project)``。

        草稿是 gate 的三个入口（读状态 / 保存 / 确认）唯一的内容来源，而收编后的
        ``ReferenceScriptPlanUnit`` 要求 unit 级 ``duration_seconds`` 且不接受多余字段：存量草稿若不在
        读时迁移，保存与确认都会撞结构校验，用户在 gate 里既改不了也确认不了。
        剧集脚本的迁移在 ``ProjectManager.load_script``，草稿的在这里，两处共用同一迁移器。

        ``supported_durations`` 由调用方经 ``_resolve_supported_durations`` 解析后传入（本函数
        跑在锁内，不能自行 await），与 prompt_authoring 生成侧同源：迁移幂等一次性、谁先跑谁定终局，两侧
        口径不一致时先跑的内容确认会把落在结构区间内但非档位成员的秒数固化到盘上，prompt_authoring 的枚举
        schema 随后硬拒，用户在 gate 里既看不出问题也改不动。为 None（项目未配置视频型号）时
        退回结构区间 clamp——缺配置不该阻断草稿加载，档位偏移仍由执行时取档兜底。

        调用方须已持有 ``self.pm.file_lock(path)``；reference_video 还须先持 prompt_authoring 草稿锁，
        统一锁序为「下游草稿 → 正式 script_plan」。本函数只做读改写，不自行加锁，避免与调用方
        （如 ``confirm``）已持有的同一把锁发生同线程二次获取的死锁。
        """
        content = _read_json(path)
        if content is None or script_review.script_plan_kind(project) != "reference_video":
            return content, project

        updated, _warnings = script_review.migrate_script_plan_draft_in_place(
            project_path,
            content,
            episode=episode,
            update_project=lambda mutate: self.pm.update_project(project_name, mutate),
            supported_durations=supported_durations,
        )
        return content, updated or project

    async def get_state(self, project_name: str, episode: int) -> dict[str, Any]:
        """返回该集内容确认状态 + 结构化中间态内容（供 web 渲染）。

        ``content`` 为解析后的结构化 script_plan（drama: {title, scenes[]}；narration: {segments[]}；
        reference_video: {units[]}）；不适用 gate 或 script_plan 缺失 / 损坏时为 None。

        ``supported_durations`` 是参考路线读时迁移的结构档位全集；i2v 未知时为 None，
        不借用 r2v 档位改写秒数。界面可选档位由 ``get_reference_duration_tiers`` 按桶提供。

        档位解析先于落盘读写完成，其余同步 I/O 整段卸到线程：``file_lock`` 是阻塞式文件锁，
        不能跨 await 持有，而档位解析本身要 await 视频能力查询。
        """
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        supported_durations = await self._resolve_supported_durations(project)
        state = await run_sync_transaction(self._get_state_sync, project_name, project, episode, supported_durations)
        state["soft_violations"] = []
        content = state["content"]
        if script_review.script_plan_kind(project) == "reference_video" and isinstance(content, dict):
            try:
                caps = await fetch_reference_split_caps(project, config_resolver=self.config_resolver)
            except VideoRequestFactsError:
                return state
            units = content.get("units") or []
            texts = [str(unit.get("text") or "") if isinstance(unit, dict) else "" for unit in units]
            unit_ids = [
                unit_id for unit in units if isinstance(unit, dict) and isinstance(unit_id := unit.get("unit_id"), str)
            ]
            state["soft_violations"] = soft_violation_entries(
                reference_soft_violations(
                    texts,
                    project,
                    episode=episode,
                    voice=caps.voice,
                    unit_ids=unit_ids if len(unit_ids) == len(units) else None,
                )
            )
        return state

    def _get_state_sync(
        self,
        project_name: str,
        project: dict[str, Any],
        episode: int,
        supported_durations: list[int] | None,
    ) -> dict[str, Any]:
        """``get_state`` 的同步主体：分集自愈、读时迁移、状态与指纹派生，全部在一个线程内完成。

        ``project`` 与 ``supported_durations`` 由 ``get_state`` 预先取好传入——迁移不改动视频
        型号配置，档位表在迁移前后描述的是同一件事，无需在锁内重算。
        """
        project_path = self.pm.get_project_path(project_name)
        path = script_review.script_plan_path(project_path, project, episode)
        if path is not None:
            project = self._require_episode(project_name, project, episode)
        content: dict[str, Any] | None = None
        fingerprint: str | None = None
        if path is not None:
            prompt_authoring_lock = (
                self.pm.file_lock(quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING))
                if script_review.script_plan_kind(project) == "reference_video"
                else nullcontext()
            )
            with prompt_authoring_lock, self.pm.file_lock(path):
                content, project = self._read_script_plan_migrated(
                    project_name, project, episode, path, project_path, supported_durations
                )
                fingerprint = script_review.content_fingerprint(path)
                status = script_review.review_status(project_path, project, episode)
        else:
            status = script_review.review_status(project_path, project, episode)
        return {
            "episode": episode,
            "content_mode": project.get("content_mode"),
            "status": status,
            "fingerprint": fingerprint,
            "confirmed_at": script_review.stored_review(project, episode).get("confirmed_at"),
            "content": content,
            "supported_durations": supported_durations,
            "episode_target_duration": project_episode_target_duration(project),
            "script_overwrite": _overwrite_dict(project_path, project, episode) if path is not None else None,
        }

    async def get_quarantine_info(self, project_name: str, episode: int) -> dict[str, Any] | None:
        """本集 script_plan 草稿的呈现视图（供内容确认页展示与手修），不适用 / 无草稿时 None。

        视图字段：``doc_type``、``revision``（手修保存与丢弃的并发令牌；信封损坏时为 None）、
        ``editable_by``（``user`` 为待修复草稿，``agent`` 为 Agent 的可编辑草稿）、``content``（草稿正文
        原样，供创作者就地修改；Agent 的可编辑草稿不展示内容，为 None）、``violations``、
        ``soft_violations``（逐条目定位的降级提示）与 ``formal_exists``（丢弃后是否有正式脚本规划可回）。

        违约读时按产出时那套校验器全量重算（``revalidate_script_plan_draft`` 按 kind 分派到该变体的
        重判器，晋升工具同一份代码），不信任草稿里 ``violations`` 的上一轮快照——草稿在场期间源文或
        模型配置可能已变，报告要对现值负责。meta 被改坏以致无从重算时，把「无法重算」本身作为一条
        违约返回，而不是退回草稿里那份上一轮快照。可编辑草稿归 Agent 处置，不重算。

        本方法与 ``get_state`` 各自读盘、互不依赖，由 router 在同一次请求内合并两者的返回。项目 /
        草稿的同步文件读取经 ``asyncio.to_thread`` 卸到线程，避免源文较大时占用事件循环。
        """
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        quarantine_kind = script_review.script_plan_quarantine_kind(project)
        if quarantine_kind is None:
            return None

        def _locate() -> tuple[Path, Path | None, bool]:
            project_path = self.pm.get_project_path(project_name)
            quarantine_path = script_review.script_plan_quarantine_path(project_path, project, episode)
            if quarantine_path is None or not quarantine_path.exists():
                return project_path, None, False
            formal_path = script_review.script_plan_path(project_path, project, episode)
            return project_path, quarantine_path, formal_path is not None and formal_path.exists()

        project_path, quarantine_path, formal_exists = await asyncio.to_thread(_locate)
        if quarantine_path is None:
            return None
        view: dict[str, Any] = {
            "doc_type": QUARANTINE_KIND_TO_DOC_TYPE[quarantine_kind],
            "revision": None,
            "editable_by": DRAFT_OWNER_USER,
            "content": None,
            "violations": [],
            "soft_violations": [],
            "formal_exists": formal_exists,
        }
        draft = await asyncio.to_thread(read_quarantine, project_path, episode, quarantine_kind)
        if draft is None:
            if not await asyncio.to_thread(quarantine_path.exists):
                return None
            view["violations"] = [_unreadable_violation("草稿文件已损坏或格式不符，无法解析")]
            return view
        view["revision"] = draft_revision(draft)
        view["editable_by"] = draft_owner(draft)
        if view["editable_by"] == DRAFT_OWNER_AGENT:
            return view
        view["content"] = draft.content
        from server.draft_workflow import revalidate_script_plan_draft

        try:
            revalidation = await revalidate_script_plan_draft(
                project_path,
                project,
                episode,
                draft,
                config_resolver=self.config_resolver,
            )
        except VideoRequestFactsError as exc:
            view["violations"] = [
                {
                    "code": exc.code,
                    "label": "",
                    "message": "",
                    "line": None,
                    "params": exc.params,
                }
            ]
            return view
        except ValueError as exc:
            logger.warning("草稿重算失败 project=%s episode=%s：%s", project_name, episode, exc)
            view["violations"] = [_unreadable_violation("草稿的产出上下文缺失或损坏，无法重新校验")]
            return view
        view["violations"] = violation_entries(revalidation.violations)
        view["soft_violations"] = soft_violation_entries(list(revalidation.soft_violations))
        return view

    async def get_reference_duration_tiers(
        self, project_name: str, episode: int, units: Sequence[dict[str, Any]] = ()
    ) -> dict | None:
        """返回参考路线带参考图的生效档位与逐单元定桶结论。

        ``units`` 是本集脚本规划的单元：``units`` 键按 ``unit_id`` 给出每个单元按可用参考图
        所落的桶、该桶档位与声明引用的分裂情况，面板据此取档。
        非参考路线或 r2v 桶的视频请求事实解析不出时返回 None，呈现层保持只读。项目文件读取卸到线程，避免阻塞请求循环。
        """
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        if script_review.script_plan_kind(project) != "reference_video":
            return None
        request_facts = reference_request_facts_lookup(project, self.config_resolver)
        with_ref_facts = await request_facts("r2v")
        if isinstance(with_ref_facts, VideoRequestFactsFailure):
            return None
        project_path = await asyncio.to_thread(self.pm.get_project_path, project_name)
        unit_capabilities = await reference_unit_capabilities(
            project,
            project_path,
            units,
            request_facts=request_facts,
        )
        return {
            "with_references": list(with_ref_facts.allowed_durations),
            "units": unit_capabilities,
        }

    async def save_content(
        self, project_name: str, episode: int, content: object, base_fingerprint: str | None = None
    ) -> dict[str, Any]:
        """校验并落盘编辑后的结构化中间态（手动或 Agent 编辑后回写），返回最新状态（重新等待确认）。

        内容变更使指纹漂移，``get_state`` 据此自动回到 pending_review——保存即重新需要确认。
        已确认的脚本规划只读，保存抛 ``script_plan_confirmed``、不落盘；重跑脚本规划写出新内容后恢复可保存。
        Agent 的可编辑草稿在场时，正式规划也只读，保存抛 ``draft_agent_owned``。

        ``base_fingerprint`` 是编辑方读取内容（``get_state``）时拿到的指纹：给定时在锁内与盘上
        现值比对，不一致（编辑期间另一写入方已改过 script_plan）抛 ``conflict``、不落盘——后写方拿
        409 冲突提示去刷新合并，先写方的内容不被静默覆盖。``None`` 不比对（无基线的直连调用）。

        落盘本身全同步（不做时长收编，无需档位表），整段卸到线程；随后的 ``get_state`` 自行
        解析档位表，保存的响应与 GET 首次加载因此同源。
        """
        await run_sync_transaction(self._save_content_sync, project_name, episode, content, base_fingerprint)
        return await self.get_state(project_name, episode)

    def _save_content_sync(
        self, project_name: str, episode: int, content: object, base_fingerprint: str | None
    ) -> None:
        """``save_content`` 的同步主体：结构校验、基线比对、落盘。"""
        project = self.pm.load_project(project_name)
        project_path = self.pm.get_project_path(project_name)
        path = script_review.script_plan_path(project_path, project, episode)
        if path is None:
            raise ScriptReviewError("not_applicable")
        project = self._require_episode(project_name, project, episode)
        kind, model = self._resolve_script_plan_model(project, episode)
        try:
            validated = model.model_validate(content).model_dump()
        except ValidationError as exc:
            _require_changed_speech_admitted(kind, _read_json(path), content)
            raise ScriptReviewError("invalid_content", str(exc)) from exc
        if kind == "drama":
            for scene in validated["scenes"]:
                if scene.get("needs_replan") is not True:
                    scene.pop("needs_replan", None)
        if not validated.get(NEW_ASSETS_FIELD):
            validated.pop(NEW_ASSETS_FIELD, None)
        expected = base_fingerprint if base_fingerprint is not None else script_review.UNCHECKED_FINGERPRINT
        try:
            if kind == "reference_video":
                prompt_authoring_path = quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING)
                with (
                    self.pm.file_lock(prompt_authoring_path),
                    script_review.script_plan_write_lock(project_path, episode),
                ):
                    self._reject_readonly_script_plan(project_name, project_path, episode)
                    _require_changed_speech_admitted(kind, _read_json(path), validated)
                    script_review.write_script_plan_locked(
                        project_path, episode, validated, expected_fingerprint=expected
                    )
            else:
                with script_review.formal_script_plan_lock(project_path, episode, path):
                    self._reject_readonly_script_plan(project_name, project_path, episode)
                    script_review.assert_base_fingerprint(path, expected)
                    _require_changed_speech_admitted(kind, _read_json(path), validated)
                    script_review.write_formal_script_plan_locked(project_path, episode, path, validated)
        except script_review.ScriptPlanWriteConflict as exc:
            raise ScriptReviewError("conflict", str(exc)) from exc

    def _reject_readonly_script_plan(self, project_name: str, project_path: Path, episode: int) -> None:
        """持正式规划锁按最新确认记录与 Agent 草稿归属拒绝只读规划的保存。"""
        project = self.pm.load_project(project_name)
        if script_review.formal_script_plan_agent_owned(project_path, project, episode):
            raise ScriptReviewError("draft_agent_owned")
        if script_review.formal_script_plan_confirmed(project_path, project, episode):
            raise ScriptReviewError("script_plan_confirmed")

    async def confirm(
        self, project_name: str, episode: int, *, overwrite_revision: str | None = None
    ) -> dict[str, Any]:
        """确认该集 script_plan：整份转为正式脚本，并在同一次写入里记录确认指纹，放行 prompt_authoring。

        无 script_plan / 不适用 / 集条目缺失 / script_plan 内容结构非法 / 有草稿待处置 / 转换准入
        不满足时抛 ScriptReviewError，由 router 映射 4xx。该集已有正式脚本时，``overwrite_revision``
        须等于其覆盖清单的 ``revision``（调用方认可覆盖的正是这一份）；缺失或不符时抛
        ``overwrite_required``，携带当前的覆盖清单，不写正式脚本与确认记录。

        档位表先于加锁解析（同 ``get_state``）：确认路径同样要对存量草稿做一次读时收编，收编
        用的档位表须与 gate 面板呈现的那份同源，否则确认会把面板上选不到的秒数固化到盘上。
        """
        project = await asyncio.to_thread(self.pm.load_project, project_name)
        supported_durations = await self._resolve_supported_durations(project)

        def _locate_overwrite() -> tuple[Path, script_review.FormalScriptOverwrite | None]:
            project_path = self.pm.get_project_path(project_name)
            return project_path, script_review.formal_script_overwrite(project_path, project, episode)

        project_path, overwrite = await asyncio.to_thread(_locate_overwrite)
        fingerprint = await run_sync_transaction(
            self._confirm_sync, project_name, project, episode, supported_durations
        )
        if overwrite is not None and script_review.fingerprint_of_revision(overwrite_revision) != overwrite.fingerprint:
            raise ScriptReviewError("overwrite_required", overwrite=overwrite.to_dict())
        await self._materialize(
            project_path,
            episode,
            plan_fingerprint=fingerprint,
            script_fingerprint=overwrite.fingerprint if overwrite is not None else None,
        )
        return await self.get_state(project_name, episode)

    async def _materialize(
        self, project_path: Path, episode: int, *, plan_fingerprint: str, script_fingerprint: str | None
    ) -> None:
        """整集转换与确认记录一起落盘；转换期间脚本规划或正式脚本被改过即按冲突拒绝。"""
        confirmed_at = datetime.now(UTC).isoformat()

        def _record_confirmation(project: dict[str, Any]) -> None:
            if not script_review.apply_confirmation(project, episode, plan_fingerprint, confirmed_at):
                raise ScriptReviewError("episode_not_found")

        generator = await asyncio.to_thread(ScriptGenerator, project_path, config_resolver=self.config_resolver)
        try:
            await generator.materialize_script_plan(
                episode,
                expected_plan_revision=plan_fingerprint,
                expected_script_fingerprint=script_fingerprint,
                project_update=_record_confirmation,
            )
        except SpeechAdmissionError as exc:
            raise ScriptReviewError("speech_admission", admission=exc.admission) from exc
        except (ScriptWriteConflict, script_review.ScriptPlanWriteConflict) as exc:
            raise ScriptReviewError("conversion_conflict", str(exc)) from exc
        except VideoRequestFactsError as exc:
            raise ScriptReviewError("video_request_facts", exc.code, problem=exc.failure) from exc
        except script_review.ForeignFormalScriptError as exc:
            raise ScriptReviewError("foreign_formal_script", str(exc), script_filename=exc.filename) from exc
        except NewAssetsError as exc:
            raise ScriptReviewError("invalid_new_assets", str(exc)) from exc
        except UnregisteredReferencesError as exc:
            raise ScriptReviewError("unregistered_references", str(exc)) from exc
        except UnitDurationOffTier as exc:
            params = {
                "position": exc.position,
                "duration_seconds": exc.duration_seconds,
                "tiers": " / ".join(str(tier) for tier in exc.tiers),
            }
            raise ScriptReviewError("duration_off_tier", str(exc), params=params) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise ScriptReviewError("conversion_refused", str(exc)) from exc

    def _confirm_sync(
        self,
        project_name: str,
        project: dict[str, Any],
        episode: int,
        supported_durations: list[int] | None,
    ) -> str:
        """``confirm`` 的同步前半：待处置草稿校验、读时收编、结构校验，返回待确认内容的指纹。"""
        project_path = self.pm.get_project_path(project_name)
        path = script_review.script_plan_path(project_path, project, episode)
        if path is None:
            raise ScriptReviewError("not_applicable")
        project = self._require_episode(project_name, project, episode)
        quarantine = script_review.script_plan_quarantine_path(project_path, project, episode)
        if quarantine is not None and quarantine.exists():
            raise ScriptReviewError("quarantined", f"script_plan 草稿待处置: {quarantine}")
        kind, model = self._resolve_script_plan_model(project, episode)
        prompt_authoring_lock = (
            self.pm.file_lock(quarantine_path(project_path, episode, QUARANTINE_KIND_PROMPT_AUTHORING))
            if kind == "reference_video"
            else nullcontext()
        )
        with prompt_authoring_lock, self.pm.file_lock(path):
            content, project = self._read_script_plan_migrated(
                project_name, project, episode, path, project_path, supported_durations
            )
            if content is None and not path.exists():
                raise ScriptReviewError("no_script_plan")
            try:
                validated = model.model_validate(content)
            except ValidationError as exc:
                raise ScriptReviewError("invalid_content", str(exc)) from exc
            try:
                resolve_new_assets(project, validated.model_dump().get(NEW_ASSETS_FIELD))
            except NewAssetsError as exc:
                raise ScriptReviewError("invalid_new_assets", str(exc)) from exc

            marked_shape = {
                "drama": ("scenes", "scenes"),
                "reference_video": ("units", "video_units"),
            }.get(kind)
            if marked_shape is not None:
                root, skeleton = marked_shape
                for unit in validated.model_dump()[root]:
                    if unit.get("needs_replan") is not True:
                        continue
                    admission = admit_script_unit(skeleton, unit)
                    raise ScriptReviewError("speech_admission", admission=admission)

            if kind == "reference_video":
                dumped = validated.model_dump()
                for unit in dumped["units"]:
                    admission = admit_script_unit("video_units", unit)
                    if not admission.allowed:
                        raise ScriptReviewError("speech_admission", admission=admission)
                script_review.write_script_plan_locked(project_path, episode, dumped)
                fingerprint = script_review.content_fingerprint_of_data(dumped)
            else:
                fingerprint = script_review.content_fingerprint_of_data(content)
        return fingerprint


def _overwrite_dict(project_path: Path, project: Mapping[str, Any], episode: int) -> dict[str, Any] | None:
    overwrite = script_review.formal_script_overwrite(project_path, project, episode)
    return overwrite.to_dict() if overwrite is not None else None


def _unreadable_violation(message: str) -> dict[str, Any]:
    """草稿无从重算时的那条整集层面违约；router 按 ``quarantine_unreadable`` 的文案 key 本地化。"""
    return {"code": "quarantine_unreadable", "label": "", "message": message, "line": None}


def _read_json(path: Path) -> dict[str, Any] | None:
    """读取并解析结构化 script_plan 文件；缺失 / 非法 JSON / 非对象时返回 None（状态另由指纹派生兜底）。

    容错读取复用 ``lib.infra.json_io.load_json_or_none``（OSError / JSON / 编码错误归 None），与项目
    其余 JSON 读取同口径；本函数再叠加「顶层须为对象」守卫，非对象同样返回 None。
    """
    data = load_json_or_none(path)
    return data if isinstance(data, dict) else None
