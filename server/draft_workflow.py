"""Host-independent draft workflow implementation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib import script_review
from lib.artifact_manifest import ArtifactBasis
from lib.async_thread import run_sync_transaction
from lib.config.resolver import ConfigResolver
from lib.draft_quarantine import (
    DOC_TYPE_TO_QUARANTINE_KIND,
    PROMOTE_TOOL_NAME,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    QUARANTINE_KIND_TO_DOC_TYPE,
    QuarantinedDraft,
    clear_quarantine,
    draft_payload,
    draft_revision,
    quarantine_and_report,
    quarantine_exists,
    quarantine_path,
    read_quarantine,
    write_quarantine,
)
from lib.draft_violation import DraftViolation
from lib.episode_paths import SCRIPT_PLAN_FILENAMES, episode_drafts_dir, episode_script_filename
from lib.json_io import atomic_write_json, load_json_or_none
from lib.project_manager import ProjectManager, ScriptWriteConflict
from lib.script_generator import ScriptGenerator
from lib.script_models import (
    NarrationScriptPlanDraft,
    build_drama_normalized_script_model,
    build_reference_units_script_plan_model,
)
from lib.speech_composition import admit_script_unit
from server.text_generation import (
    SOFT_VIOLATION_NOTE_QUARANTINED,
    ReferenceSplitCaps,
    _build_reference_units_from_flat,
    _collect_narration_violations,
    _collect_reference_flat_violations,
    _commit_single_script_plan,
    _coverage_source_scope,
    _fetch_caps_with_fallback,
    _fetch_reference_caps_with_fallback,
    _load_novel_source,
    _load_script_plan_source_with_basis,
    _narration_script_plan_path,
    _reference_result_text,
    _reference_soft_violation_lines,
    _uses_reference_video_units,
    render_soft_violation_section,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DraftContext:
    project_name: str
    projects_root: Path
    pm: ProjectManager
    config_resolver: ConfigResolver | None = None

    @property
    def project_path(self) -> Path:
        return self.pm.get_project_path(self.project_name)


DraftDocType = Literal[
    "drama_script_plan", "narration_script_plan", "reference_script_plan", "reference_prompt_authoring"
]
PositiveEpisode = Annotated[int, Field(strict=True, ge=1)]


class _DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode: PositiveEpisode
    doc_type: DraftDocType


class DraftLocator(_DraftRequest):
    source: str | None = None


class PatchDraftRequest(_DraftRequest):
    content: dict[str, Any]
    base_revision: str
    accept_formal_revision: str | None = None
    accepts_formal_revision: bool = False
    source: str | None = None
    updates_source: bool = False


class PromoteDraftRequest(_DraftRequest):
    base_revision: str


class DiscardDraftRequest(_DraftRequest):
    base_revision: str


class DraftWorkflowError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ReferenceDraftRevalidation(NamedTuple):
    """script_plan 草稿读时重判的结果。

    ``schema_failed`` 显式区分两个阶段：True 表示草稿连产出时的 schema 都没过（``flat_units``
    必为空，调用方只能按 ``draft.content`` 原样呈现）；False 时 ``flat_units`` 是收编后的扁平
    产出，``violations`` 为空即可晋升。两者的处置不同（原样 vs 收编），故不靠 ``flat_units``
    是否为空来反推。

    ``soft_violations`` 是软违约（声音降级、未引用场景）的已渲染文本行，与 ``violations``
    分属两个字段而非合流：软违约不阻断晋升、不进待修复草稿的违约条目，合成一个列表迟早会有一处
    按「非空即挡下」判定，把降级提示变成硬违约。schema 没过时为空——正文尚未收编，无从逐 unit 判。
    """

    violations: list[DraftViolation]
    flat_units: list[dict[str, Any]]
    caps: ReferenceSplitCaps
    schema_failed: bool
    basis: ArtifactBasis | None
    soft_violations: list[str]


async def revalidate_reference_script_plan_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> ReferenceDraftRevalidation:
    """按产出时那套校验器全量重判 script_plan 草稿，只读、不写盘、不清草稿。

    重判走的是拆分工具用的同一个函数（``_collect_reference_flat_violations``），不是它的简化
    副本：晋升口径、内容确认的读时重算与产出口径必须同一份代码，否则「这里放行、下次
    生成时被拒」这类分叉会重新出现。能力与源文都重新解析——草稿在场期间用户可能改过模型配置或
    源文，重判要对着现值判。

    不依赖 ``DraftContext``（``project_path`` / ``project`` 由调用方传入而非从 ctx 派生）：
    内容确认的读时重算（``server/services/script_review.py``）没有 Agent 工具的 ctx，
    只有 ``ProjectManager``；两处共用本函数而不各自加载 project，调用方各自加载一次即可。

    ``meta.source`` 缺失（草稿被改坏、无从重判）时抛 ``ValueError``。
    """
    # meta.source 记的是产出时的源文范围。缺键说明 meta 被改坏了：不能默默按整个 source/ 重解析
    # ——那比产出时更松，一份从别集抄来的原文锚会恰好命中而被放行。
    if "source" not in draft.meta:
        raise ValueError(
            f"草稿 {draft.path} 的 meta.source 缺失（产出时记录的源文范围）；"
            "请恢复该字段（指定源文时为其相对路径，按整个 source/ 产出时为 null）后重试"
        )
    # 源文可能达数百 KB（整个 source/ 目录拼接），同步读盘直接放在这个 async 函数体里会占用
    # 事件循环——晋升工具走的是独立会话线程不敏感，但内容确认的读时重算（同一份代码）
    # 在请求协程里跑，卸到线程避免拖慢并发的其它请求。
    novel_text, _prompt_inputs, script_plan_basis = await asyncio.to_thread(
        _load_script_plan_source_with_basis,
        project_path,
        draft.meta["source"],
        project,
        episode,
        "reference_video",
    )
    if config_resolver is None:
        split_caps = await _fetch_reference_caps_with_fallback(project, episode)
    else:
        split_caps = await _fetch_reference_caps_with_fallback(
            project,
            episode,
            config_resolver=config_resolver,
        )

    # 修改过的草稿先过产出时那份 schema：拆分侧由 response_schema 与 _parse_script_plan_json 卡住时长
    # 枚举与字段非空，晋升侧漏掉这一层的话，把 duration_seconds 改成非档位值、或整个删掉（收成
    # 0 秒）都能一路晋升进正式文件——正是本机制要防的「正式文件被污染」。schema 违约在这条路上
    # 没有 backend 可重试（内容是 Agent 写的），故同样回报告让它继续改。
    #
    # 外层形状（units 缺失 / 不是数组 / 空数组）与逐 unit 的字段违约走同一条报告路径：两者都是
    # Agent 编辑草稿时会犯的错，只有后者刷新报告的话，前者就把它甩出了「改完再晋升」的循环。
    raw_units = draft.content.get("units")
    schema = build_reference_units_script_plan_model(split_caps.durations)
    violations: list[DraftViolation] = []
    flat_units: list[dict[str, Any]] = []
    if not isinstance(raw_units, list) or not raw_units:
        logger.debug("草稿 content.units 形状非法: %s", type(raw_units).__name__)
        violations = [
            DraftViolation(
                "草稿的 content.units 必须是非空的 unit 对象数组",
                code="schema_invalid",
            )
        ]
    else:
        try:
            flat_units = schema.model_validate({"units": raw_units}).model_dump()["units"]
        except ValidationError as exc:
            violations = [
                DraftViolation(
                    f"草稿的 content 不符合 script_plan 产出结构：{exc}；"
                    f"每个 unit 须有非空 source_text / text，且 duration_seconds 取自模型档位 {split_caps.durations}",
                    code="schema_invalid",
                )
            ]
    if violations:
        return ReferenceDraftRevalidation(
            violations, [], split_caps, schema_failed=True, basis=script_plan_basis, soft_violations=[]
        )

    source_language = project.get("source_language")
    violations = _collect_reference_flat_violations(
        flat_units,
        project,
        episode=episode,
        novel_text=novel_text,
        caps=split_caps,
        source_language=source_language,
    )
    # 软违约与硬违约在同一次重判里一并产出：有硬违约时也要算，晋升被挡下时的报告同样带着它们，
    # 否则 Agent 修草稿的每一轮都看不见降级提示，直到最后一轮晋升成功才第一次读到。
    soft_violations = _reference_soft_violation_lines(
        [str(flat["text"]) for flat in flat_units],
        project,
        episode=episode,
        voice=split_caps.voice,
    )
    return ReferenceDraftRevalidation(
        violations,
        flat_units,
        split_caps,
        schema_failed=False,
        basis=script_plan_basis,
        soft_violations=soft_violations,
    )


def _commit_reference_script_plan(
    project_path: Path,
    episode: int,
    content: dict[str, Any],
    expected_fingerprint: Any,
    basis: ArtifactBasis | None,
    before_commit: Callable[[], None] | None = None,
) -> None:
    if before_commit is not None:
        before_commit()
    script_review.write_script_plan(
        project_path,
        episode,
        content,
        expected_fingerprint=expected_fingerprint,
        basis=basis,
    )
    clear_quarantine(project_path, episode, QUARANTINE_KIND_SCRIPT_PLAN)


def _open_script_plan_draft(
    project_path: Path,
    episode: int,
    script_plan_path: Path,
    kind: str,
    source: str | None,
    to_draft_shape: Callable[[dict[str, Any]], dict[str, Any] | None],
    missing_detail: str,
) -> None:
    with script_review.formal_script_plan_lock(project_path, episode, script_plan_path):
        if quarantine_exists(project_path, episode, kind):
            return
        data = load_json_or_none(script_plan_path)
        content = to_draft_shape(data) if isinstance(data, dict) else None
        if content is None:
            raise DraftWorkflowError("draft_source_missing", missing_detail)
        write_quarantine(
            project_path,
            episode,
            kind,
            content=content,
            violations=[],
            meta={
                "source": source or None,
                "base_fingerprint": script_review.content_fingerprint_of_data(data),
            },
        )


def _validate_open_source(project_path: Path, source: str) -> None:
    _load_novel_source(project_path, source)


def _rewrite_invalid_draft(
    project_path: Path,
    episode: int,
    kind: str,
    content: dict[str, Any],
    violations: list[DraftViolation],
    meta: dict[str, Any],
) -> str:
    return quarantine_and_report(
        project_path,
        episode,
        kind,
        content=content,
        violations=violations,
        meta=meta,
    )


def _soft_violation_section(revalidation: ReferenceDraftRevalidation) -> str:
    """重判结果里的软违约段，供晋升被违约挡下时拼在报告尾部。

    与晋升 / 拆分回执共用 ``render_soft_violation_section``，只换处置说明：此处草稿仍在场、
    这些提示不是要修的违约，说明不换的话 Agent 会把降级提示当成没修干净的违约反复重写。
    """
    return render_soft_violation_section(revalidation.soft_violations, note=SOFT_VIOLATION_NOTE_QUARANTINED)


async def _promote_reference_script_plan(
    ctx: DraftContext,
    episode: int,
    draft: QuarantinedDraft,
    *,
    before_commit: Callable[[], None] | None = None,
) -> str:
    """按产出时那套校验器全量重判 script_plan 草稿，通过则晋升为正式 script_plan 并清除草稿。

    返回晋升回执文本：与拆分回执同一出口（``_reference_result_text``），落盘统计后接软违约段。
    晋升成功前无人向 Agent 交代降级提示——修草稿这条主回路走完时它才第一次拿到，故成功路径不能
    只返回一个「已晋升」的布尔。
    """
    project_path = ctx.project_path
    project = await asyncio.to_thread(ctx.pm.load_project_readonly, ctx.project_name)
    try:
        revalidation = await revalidate_reference_script_plan_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=ctx.config_resolver,
        )
    except ValueError as exc:
        raise DraftWorkflowError("draft_invalid", f"❌ {exc}") from exc
    violations, flat_units, split_caps = revalidation.violations, revalidation.flat_units, revalidation.caps
    if revalidation.schema_failed:
        # schema 违约：写回 Agent 手里那份原样内容，不做收编——字段被改坏时收编会把它的原稿
        # 改形，它照着报告回去看反而对不上自己写的东西。
        report = await run_sync_transaction(
            _rewrite_invalid_draft,
            project_path,
            episode,
            QUARANTINE_KIND_SCRIPT_PLAN,
            draft.content,
            violations,
            draft.meta,
        )
        raise DraftWorkflowError("draft_invalid", report + _soft_violation_section(revalidation))
    if violations:
        report = await run_sync_transaction(
            _rewrite_invalid_draft,
            project_path,
            episode,
            QUARANTINE_KIND_SCRIPT_PLAN,
            {"units": flat_units},
            violations,
            draft.meta,
        )
        raise DraftWorkflowError("draft_invalid", report + _soft_violation_section(revalidation))

    units = _build_reference_units_from_flat(flat_units, project, episode=episode, max_refs=split_caps.max_refs)
    # 写盘经单一出口（lib.script_review.write_script_plan_locked）：锁、基线比对、prompt_authoring 草稿清理
    # 只存在那一处。基线指纹取自取回 / 草稿产出时记进 meta 的 base_fingerprint——正式文件在草稿
    # 产出后被其他写入方（Web 端保存、另一次拆分）改过时晋升中止、返回冲突报告让 Agent 合并，
    # 不静默覆盖对方的修改。缺少 base_fingerprint 的草稿按无基线晋升。
    expected = (
        draft.meta["base_fingerprint"] if "base_fingerprint" in draft.meta else script_review.UNCHECKED_FINGERPRINT
    )
    try:
        await run_sync_transaction(
            _commit_reference_script_plan,
            project_path,
            episode,
            {"units": units},
            expected,
            revalidation.basis,
            before_commit,
        )
    except script_review.ScriptPlanWriteConflict as conflict:
        raise DraftWorkflowError(
            "formal_revision_conflict",
            _render_script_plan_conflict_report(
                episode,
                draft,
                conflict,
                to_draft_shape=_reference_script_plan_draft_shape,
                field_hint="content.units",
            ),
        ) from conflict
    return _reference_result_text(
        script_review.official_reference_script_plan_path(project_path, episode),
        units,
        revalidation.soft_violations,
        action="晋升",
    )


def _render_script_plan_conflict_report(
    episode: int,
    draft: QuarantinedDraft,
    conflict: script_review.ScriptPlanWriteConflict,
    *,
    to_draft_shape: Callable[[dict[str, Any]], dict[str, Any] | None],
    field_hint: str,
) -> str:
    """渲染晋升遇乐观并发冲突时回给 Agent 的结构化报告：最新内容 + 合并指引。

    报告要让编辑方能就地合并：附上盘上现值转成草稿那一层的形状（与草稿 ``content`` 同形，可逐条
    对照），并指明通过 ``patch_draft`` 显式接受已合并的正式版本，之后重新晋升才会放行。

    ``to_draft_shape`` 与 ``field_hint`` 由各变体传入：草稿层的形状与可改字段按变体不同，
    附一份对不上形状的「最新内容」比不附更误导。
    """
    latest_content = to_draft_shape(conflict.current_content) if conflict.current_content is not None else None
    if latest_content is not None:
        latest = json.dumps(latest_content, ensure_ascii=False, indent=2)
        latest_block = f"当前正式 script_plan 的最新内容（与草稿 content 同形）：\n{latest}"
    else:
        latest_block = "当前正式文件不存在或不是合法的 script_plan JSON，无法附上最新内容；请自行读取该文件确认。"
    # 指纹按 JSON 字面量给：正式文件已被删除时现值是 null，写成 "None" 会让 Agent
    # 接受错误的基线，之后每次重晋升都比对不上、拿到同一份报告，冲突再也解不掉。
    actual_literal = json.dumps(conflict.actual)
    doc_type = QUARANTINE_KIND_TO_DOC_TYPE[draft.kind]
    return (
        "❌ 晋升中止（并发冲突）：正式 script_plan 在本草稿产出后已被其他写入方（如 Web 端保存）修改，"
        "直接晋升会覆盖对方的修改，本次未写盘、草稿仍在场。\n"
        f"草稿基线指纹: {json.dumps(conflict.expected)}；盘上现值指纹: {actual_literal}\n\n"
        f"{latest_block}\n\n"
        f"处置：调用 open_draft 读取当前草稿与 formal_revision，对照上方最新内容合并 {field_hint}；"
        "再调用 patch_draft 提交完整 content，并把 formal_revision 作为 accept_formal_revision；"
        "若该值为 null 且使用 remote MCP，还须传 accepts_formal_revision=true；"
        f'最后调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}, "doc_type": "{doc_type}", '
        '"base_revision": "<patch_draft 返回的新 revision>"}) 重新晋升。'
    )


def _render_prompt_authoring_conflict_report(
    episode: int,
    draft: QuarantinedDraft,
    conflict: ScriptWriteConflict,
) -> str:
    latest = (
        json.dumps(conflict.current_content, ensure_ascii=False, indent=2)
        if conflict.current_content is not None
        else "null"
    )
    return (
        "❌ 晋升中止（并发冲突）：正式剧本在本草稿产出后已被修改，本次未写盘、草稿仍在场。\n"
        f"草稿基线指纹: {json.dumps(conflict.expected)}；盘上现值指纹: {json.dumps(conflict.actual)}\n\n"
        f"当前正式剧本的最新内容：\n{latest}\n\n"
        "处置：调用 open_draft 读取当前草稿与 formal_revision，合并最新正式内容；"
        "再调用 patch_draft 提交完整 content，并把 formal_revision 作为 accept_formal_revision；"
        "若该值为 null 且使用 remote MCP，还须传 accepts_formal_revision=true；"
        f'最后调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}, "doc_type": "reference_prompt_authoring", '
        '"base_revision": "<patch_draft 返回的新 revision>"}) 重新晋升。'
    )


def _flatten_reference_script_plan_units(units: list[Any]) -> list[dict[str, Any]]:
    """正式 script_plan 的结构化 unit 表 → 扁平草稿单元（``_build_reference_units_from_flat`` 的逆向）。

    ``unit_id`` 不进草稿：它是按数组序号机械编号的派生物，草稿是给 Agent 改的那一层，带上
    派生字段等于给漂移开口子。

    盘上 unit 不合形状时不 fail-loud：字段缺失或类型不符时**原样带过**（缺失填 None / 空串），
    交由晋升侧的 schema 重判逐条报告给 Agent。原样带过而非归一化成合法值：``8.0`` 被改写成
    ``0`` 后，Agent 从草稿里看到的是一个它没写过的时长，报告说「时长不在档位内」也对不上盘
    上的原值——保留原值，让它自己看见错在哪。非 dict 的 unit 同样不丢弃：填空占位保留在数组
    对应位置，让晋升侧 schema 判它「结构非法」逐条报出——直接跳过会让数组变短，若剩余 unit
    恰好都能过校验，晋升会悄悄覆盖正式文件、丢失这个 unit 而无人知晓。
    """
    flat: list[dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, dict):
            flat.append({"duration_seconds": None, "source_text": "", "text": ""})
            continue
        text = unit.get("text")
        flat.append(
            {
                "duration_seconds": unit.get("duration_seconds"),
                "source_text": unit.get("source_text", ""),
                "text": text if isinstance(text, str) else "",
            }
        )
    return flat


def _reference_script_plan_draft_shape(content: dict[str, Any]) -> dict[str, Any] | None:
    """正式参考 script_plan 内容 → 扁平草稿结构；不是合法 script_plan 时返回 None。"""
    units = content.get("units")
    if not isinstance(units, list) or not units:
        return None
    return {"units": _flatten_reference_script_plan_units(units)}


def _drama_script_plan_draft_shape(content: dict[str, Any]) -> dict[str, Any] | None:
    """正式 drama script_plan 内容 → 可编辑草稿装的分镜结构；不是合法 script_plan 时返回 None。

    只剥 ``needs_replan``：它是按台词准入机械派生的标记，让 Agent 编辑派生物等于给漂移开
    口子——晋升时照样按 ``content`` 现值重新派生。其余字段原样带过，包括 ``scene_id``：它是
    prompt_authoring 视觉层的对齐锚，草稿里写坏了要由晋升侧的 schema 逐条报出来，不能在这一层替它填。
    非 dict 的分镜项同样原样带过而非丢弃：跳过会让数组变短，若剩余分镜恰好都能过校验，晋升
    会悄悄覆盖正式文件、丢掉这一分镜而无人知晓。
    """
    scenes = content.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return None
    flat: list[Any] = []
    for scene in scenes:
        flat.append({k: v for k, v in scene.items() if k != "needs_replan"} if isinstance(scene, dict) else scene)
    return {"title": content.get("title", ""), "scenes": flat}


class SingleScriptPlanDraftRevalidation(NamedTuple):
    """只有一个草稿位的两条路线（drama / narration）的 script_plan 草稿读时重判结果。

    ``schema_failed`` 显式区分两个阶段：True 表示草稿连产出时的 schema 都没过（``content`` 必为
    空 dict，调用方只能按 ``draft.content`` 原样呈现）；False 时 ``content`` 是经 schema 收编后的
    草稿层形状（drama 的 ``{title, scenes}``、narration 的 ``{segments}``），``violations`` 为空即
    可晋升。两者的处置不同（原样 vs 收编），故不靠 ``content`` 是否为空来反推。

    两条路线共用一个类型而非各立一个：字段与语义逐字相同，分成两个只会让 ``revalidate_script_plan_draft``
    的归一分支按类型各写一遍同样的事。参考生视频另有 ``ReferenceDraftRevalidation``——它的产出是
    扁平 units 且要带回档位，形状本就不同。
    """

    violations: list[DraftViolation]
    content: dict[str, Any]
    schema_failed: bool
    basis: ArtifactBasis | None


async def revalidate_drama_script_plan_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> SingleScriptPlanDraftRevalidation:
    """按产出时那套校验器全量重判 drama script_plan 草稿，只读、不写盘、不清草稿。

    校验器就是产出时那一个（按当前能力档位构造的 ``DramaNormalizedScript``），不是它的副本：
    档位随项目配置变化，草稿里那个曾经合法的秒数今天可能已不在档位内，用旧枚举放行等于把一份
    供应商不接的时长固化进正式文件。``needs_replan`` 同样按现值重新派生，与生成侧同一口径。

    与另两条路线的重判器同样不依赖 ``DraftContext``：晋升工具与内容确认的读时重算共用
    本函数，后者只有 ``ProjectManager``，没有 Agent 工具的 ctx。

    源文不可读（``meta.source`` 指向缺失 / 改名的路径）时抛 ``ValueError``。
    """
    # 源文可能达数百 KB，同步读盘卸到线程：内容确认的读时重算在请求协程里跑，直接读会
    # 占用事件循环、拖慢并发的其它请求。与另两条路线同口径。
    _novel_text, _prompt_inputs, script_plan_basis = await asyncio.to_thread(
        _load_script_plan_source_with_basis,
        project_path,
        draft.meta.get("source"),
        project,
        episode,
        "drama",
    )
    if config_resolver is None:
        _default_duration, supported_durations = await _fetch_caps_with_fallback(project, episode)
    else:
        _default_duration, supported_durations = await _fetch_caps_with_fallback(
            project,
            episode,
            config_resolver=config_resolver,
        )
    schema = build_drama_normalized_script_model(supported_durations)
    try:
        content = schema.model_validate(draft.content).model_dump()
    except ValidationError as exc:
        violation = DraftViolation(
            f"草稿的 content 不符合 script_plan 规范化产出结构：{exc}；"
            f"顶层须为 {{title, scenes}}，每个分镜的 duration_seconds 取自模型档位 {supported_durations}",
            code="schema_invalid",
        )
        return SingleScriptPlanDraftRevalidation([violation], {}, schema_failed=True, basis=script_plan_basis)

    raw_scenes = content.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        violation = DraftViolation("草稿的 content.scenes 必须是非空的分镜对象数组", code="schema_invalid")
        return SingleScriptPlanDraftRevalidation([violation], {}, schema_failed=True, basis=script_plan_basis)
    for scene in raw_scenes:
        admission = admit_script_unit("scenes", scene, ignore_marker=True)
        if admission.allowed:
            scene.pop("needs_replan", None)
        else:
            scene["needs_replan"] = True
    return SingleScriptPlanDraftRevalidation([], content, schema_failed=False, basis=script_plan_basis)


async def _promote_drama_script_plan(
    ctx: DraftContext,
    episode: int,
    draft: QuarantinedDraft,
) -> None:
    """按产出时那套校验器全量重判 drama script_plan 草稿，通过则晋升为正式 script_plan 并清除草稿。"""
    project_path = ctx.project_path
    project = await asyncio.to_thread(ctx.pm.load_project_readonly, ctx.project_name)
    try:
        revalidation = await revalidate_drama_script_plan_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=ctx.config_resolver,
        )
    except ValueError as exc:
        raise DraftWorkflowError("draft_invalid", f"❌ {exc}") from exc

    if revalidation.violations:
        # schema 违约时写回 Agent 手里那份原样内容，不做收编——字段被改坏时收编会把它的原稿
        # 改形，它照着报告回去看反而对不上自己写的东西。过了 schema 的那份则回写收编后的内容。
        report = await run_sync_transaction(
            _rewrite_invalid_draft,
            project_path,
            episode,
            QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
            draft.content if revalidation.schema_failed else revalidation.content,
            revalidation.violations,
            draft.meta,
        )
        raise DraftWorkflowError("draft_invalid", report)

    content = revalidation.content
    script_plan_basis = revalidation.basis
    # 基线指纹取自取回时记进 meta 的 base_fingerprint：正式文件在草稿产出后被其他写入方
    # （Web 端保存、重跑 normalize）改过时晋升中止、返回冲突报告让 Agent 合并，不静默覆盖。
    expected = (
        draft.meta["base_fingerprint"] if "base_fingerprint" in draft.meta else script_review.UNCHECKED_FINGERPRINT
    )
    script_plan_path = episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["drama"]
    try:
        await run_sync_transaction(
            _commit_single_script_plan,
            project_path,
            episode,
            script_plan_path,
            QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
            content,
            expected,
            script_plan_basis,
        )
    except script_review.ScriptPlanWriteConflict as conflict:
        raise DraftWorkflowError(
            "formal_revision_conflict",
            _render_script_plan_conflict_report(
                episode,
                draft,
                conflict,
                to_draft_shape=_drama_script_plan_draft_shape,
                field_hint="content.scenes",
            ),
        ) from conflict


async def _open_drama_script_plan_for_edit(
    ctx: DraftContext,
    episode: int,
    source: str | None,
) -> None:
    """把本集正式 drama script_plan 取回为草稿（正式文件保持原样），返回给 Agent 的编辑指引。

    与参考生视频同一条流程：草稿有无的检查、正式文件的读取、草稿的写入整段在同一把 per-path
    锁的临界区内完成——拆开在锁外各做一次的话，同一集的两个并发取回请求会都先看到「无草稿」、
    再各自写入，后写者悄悄覆盖前者的内容与 meta。
    """
    project_path = ctx.project_path
    # source 在写草稿前校验：草稿一旦落盘就把它记进 meta.source 供晋升取产物依据，若此刻是个
    # 缺失 / 改名 / 写错的路径，晋升会反复报错，而草稿已在场又挡住重新取回改正 source——Agent
    # 会卡在一个自己改不动的死角。校验失败时不落盘，无效参数不留持久副作用。
    if source is not None:
        try:
            await asyncio.to_thread(_validate_open_source, project_path, source)
        except ValueError as exc:
            raise DraftWorkflowError("draft_open_failed", f"❌ {exc}") from exc

    script_plan_path = episode_drafts_dir(project_path, episode) / SCRIPT_PLAN_FILENAMES["drama"]
    await run_sync_transaction(
        _open_script_plan_draft,
        project_path,
        episode,
        script_plan_path,
        QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
        source,
        _drama_script_plan_draft_shape,
        f"❌ 第 {episode} 集没有可编辑的正式 script_plan（{script_plan_path} 不存在、不是合法 JSON，"
        "或 scenes 不是非空数组）；首次生成请调用 generate_script_plan",
    )


async def revalidate_narration_script_plan_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> SingleScriptPlanDraftRevalidation:
    """按产出时那套校验器全量重判 narration script_plan 草稿，只读、不写盘、不清草稿。

    重判走的是拆分工具用的同一个函数（``_collect_narration_violations``），不是它的简化副本：
    晋升口径、内容确认的读时重算与产出口径必须同一份代码，否则「这里放行、下次生成时被拒」
    这类分叉会重新出现。能力档位与源文都重新解析——隔离期间用户可能改过模型配置或源文，重判要
    对着现值判。

    与 ``revalidate_reference_script_plan_draft`` 同样不依赖 ``DraftContext``：内容确认的读时重算
    没有 Agent 工具的 ctx，只有 ``ProjectManager``，两处共用本函数而不各自加载 project。

    ``meta.source`` 缺失（草稿被改坏、无从重判）时抛 ``ValueError``。
    """
    # meta.source 记的是产出时的源文范围。缺键说明 meta 被改坏了：不能默默按整个 source/ 重解析
    # ——那比产出时更松，一份删过字的分镜表可能恰好被别集的原文补齐而被放行。
    if "source" not in draft.meta:
        raise ValueError(
            f"草稿 {draft.path} 的 meta.source 缺失（产出时记录的源文范围）；"
            "请恢复该字段（指定源文时为其相对路径，按整个 source/ 产出时为 null）后重试"
        )
    # 源文可能达数百 KB（整个 source/ 目录拼接），同步读盘直接放在这个 async 函数体里会占用事件
    # 循环——晋升工具走的是独立会话线程不敏感，但内容确认的读时重算（同一份代码）在请求
    # 协程里跑，卸到线程避免拖慢并发的其它请求。
    novel_text, prompt_inputs, script_plan_basis = await asyncio.to_thread(
        _load_script_plan_source_with_basis,
        project_path,
        draft.meta["source"],
        project,
        episode,
        "narration",
    )
    if config_resolver is None:
        _default_duration, supported_durations = await _fetch_caps_with_fallback(project, episode)
    else:
        _default_duration, supported_durations = await _fetch_caps_with_fallback(
            project,
            episode,
            config_resolver=config_resolver,
        )

    # 修改过的草稿先过产出时那份 schema：拆分侧由 response_schema 与 _parse_script_plan_json 卡住字段与
    # 类型，晋升侧漏掉这一层的话，把 duration_seconds 改成字符串、或整个删掉 novel_text 都能一路
    # 晋升进正式文件——正是本机制要防的「正式文件被污染」。外层形状（segments 缺失 / 不是数组 /
    # 空数组）与逐分镜的字段违约走同一条报告路径：两者都是 Agent 编辑草稿时会犯的错。
    raw_segments = draft.content.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        logger.debug("草稿 content.segments 形状非法: %s", type(raw_segments).__name__)
        violation = DraftViolation("草稿的 content.segments 必须是非空的分镜对象数组", code="schema_invalid")
        return SingleScriptPlanDraftRevalidation([violation], {}, schema_failed=True, basis=script_plan_basis)
    try:
        content = NarrationScriptPlanDraft.model_validate(draft.content).model_dump()
    except ValidationError as exc:
        violation = DraftViolation(
            f"草稿的 content 不符合 script_plan 分镜拆分产出结构：{exc}；"
            "每个分镜须有非空 segment_id / novel_text、整数 duration_seconds、布尔 segment_break，"
            "以及 characters_in_segment / scenes / props 三个数组（无对应资产时写空数组）",
            code="schema_invalid",
        )
        return SingleScriptPlanDraftRevalidation([violation], {}, schema_failed=True, basis=script_plan_basis)

    violations = _collect_narration_violations(
        content["segments"],
        episode=episode,
        supported_durations=supported_durations,
        characters=cast(dict[str, Any], prompt_inputs["characters"]),
        scenes=cast(dict[str, Any], prompt_inputs["scenes"]),
        props=cast(dict[str, Any], prompt_inputs["props"]),
        novel_text=novel_text,
        # 重判用的源文范围来自草稿自己的 meta.source：取回时未指定 source
        # 的草稿记的是 null（整个 source/），若本集正式 script_plan 当初是按单个源文件产出的，这里会把
        # 一份原样取回、一字未改的草稿判成覆盖不全。把范围与改法一并写进消息，Agent 才走得出去
        # ——草稿在场时不能重新取回，须由 patch_draft 更新源文范围。
        source_scope=(
            f"{_coverage_source_scope(cast(str | None, draft.meta['source']))}"
            "，取自草稿的源文范围；若该范围与产出本集正式 script_plan 时不同，"
            "请调用 patch_draft，用 source 传入当初那个源文件的相对路径后重试"
        ),
    )
    return SingleScriptPlanDraftRevalidation(violations, content, schema_failed=False, basis=script_plan_basis)


def _narration_script_plan_draft_shape(content: dict[str, Any]) -> dict[str, Any] | None:
    """正式 narration script_plan 内容 → 草稿装的分镜结构；不是合法 script_plan 时返回 None。

    该变体没有机器派生字段可剥（``segment_id`` 是模型自己写的对齐锚、不由序号派生），草稿层与
    落盘层同形，只丢掉 ``segments`` 之外的顶层键。分镜项原样带过、包括非 dict 的项：跳过会让
    数组变短，若剩余分镜恰好都能过校验，晋升会悄悄覆盖正式文件、丢掉这一段而无人知晓。
    """
    segments = content.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    return {"segments": list(segments)}


async def _promote_narration_script_plan(
    ctx: DraftContext,
    episode: int,
    draft: QuarantinedDraft,
) -> None:
    """按产出时那套校验器全量重判 narration script_plan 草稿，通过则晋升为正式 script_plan 并清除草稿。"""
    project_path = ctx.project_path
    project = await asyncio.to_thread(ctx.pm.load_project_readonly, ctx.project_name)
    try:
        revalidation = await revalidate_narration_script_plan_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=ctx.config_resolver,
        )
    except ValueError as exc:
        raise DraftWorkflowError("draft_invalid", f"❌ {exc}") from exc

    # schema 违约时写回 Agent 手里那份原样内容，不做收编——字段被改坏时收编会把它的原稿改形，
    # 它照着报告回去看反而对不上自己写的东西。过了 schema 的那份则回写收编后的内容。
    if revalidation.violations:
        content = draft.content if revalidation.schema_failed else revalidation.content
        report = await run_sync_transaction(
            _rewrite_invalid_draft,
            project_path,
            episode,
            QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
            content,
            revalidation.violations,
            draft.meta,
        )
        raise DraftWorkflowError("draft_invalid", report)

    # 基线指纹取自取回 / 草稿产出时记进 meta 的 base_fingerprint：正式文件在草稿产出后被其他写入方
    # （Web 端保存、重跑拆分）改过时晋升中止、返回冲突报告让 Agent 合并，不静默覆盖对方的修改。
    # 缺少 base_fingerprint 的草稿按无基线晋升。
    expected = (
        draft.meta["base_fingerprint"] if "base_fingerprint" in draft.meta else script_review.UNCHECKED_FINGERPRINT
    )
    script_plan_path = _narration_script_plan_path(project_path, episode)
    try:
        await run_sync_transaction(
            _commit_single_script_plan,
            project_path,
            episode,
            script_plan_path,
            QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
            revalidation.content,
            expected,
            revalidation.basis,
        )
    except script_review.ScriptPlanWriteConflict as conflict:
        conflict_report = _render_script_plan_conflict_report(
            episode,
            draft,
            conflict,
            to_draft_shape=_narration_script_plan_draft_shape,
            field_hint="content.segments",
        )
        raise DraftWorkflowError("formal_revision_conflict", conflict_report) from conflict


async def _open_narration_script_plan_for_edit(
    ctx: DraftContext,
    episode: int,
    source: str | None,
) -> None:
    """把本集正式 narration script_plan 取回为草稿（正式文件保持原样），返回给 Agent 的编辑指引。

    与另两条路线同一条流程：草稿有无的检查、正式文件的读取、草稿的写入整段在同一把 per-path 锁
    的临界区内完成——拆开在锁外各做一次的话，同一集的两个并发取回请求会都先看到「无草稿」、再
    各自写入，后写者悄悄覆盖前者的内容与 meta。
    """
    project_path = ctx.project_path
    # source 在写草稿前校验：草稿一旦落盘就把它记进 meta.source 供晋升重判原文覆盖，若此刻是个
    # 缺失 / 改名 / 写错的路径，晋升会反复报错，而草稿已在场又挡住重新取回改正 source——agent
    # 会卡在一个自己改不动的死角。校验失败时不落盘，无效参数不留持久副作用。
    if source is not None:
        try:
            await asyncio.to_thread(_validate_open_source, project_path, source)
        except ValueError as exc:
            raise DraftWorkflowError("draft_open_failed", f"❌ {exc}") from exc

    script_plan_path = _narration_script_plan_path(project_path, episode)
    await run_sync_transaction(
        _open_script_plan_draft,
        project_path,
        episode,
        script_plan_path,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        source,
        _narration_script_plan_draft_shape,
        f"❌ 第 {episode} 集没有可编辑的正式 script_plan（{script_plan_path} 不存在、不是合法 JSON，"
        "或 segments 不是非空数组）；首次生成请调用 generate_script_plan",
    )


class ScriptPlanDraftRevalidation(NamedTuple):
    """按 kind 分派后的 script_plan 草稿重判结果，归一到呈现层口径。

    ``content`` 是要展示给用户的那一份草稿正文：过了 schema 时是收编后的现值形状（时长等已按
    当前档位重判），没过时为 None——调用方据此改用 ``draft.content`` 原样呈现 Agent 手改的文本。
    形状随变体不同（参考生视频 units、drama title+scenes、narration segments），呈现层按自己那条
    路线的卡片渲染。
    """

    violations: list[DraftViolation]
    content: dict[str, Any] | None


class _SingleScriptPlanRevalidator(Protocol):
    """表内重判器的调用形状：草稿定位参数同形，能力解析器按关键字注入。"""

    def __call__(
        self,
        project_path: Path,
        project: dict[str, Any],
        episode: int,
        draft: QuarantinedDraft,
        *,
        config_resolver: ConfigResolver | None = None,
    ) -> Awaitable[SingleScriptPlanDraftRevalidation]: ...


_SINGLE_SCRIPT_PLAN_REVALIDATORS: dict[str, _SingleScriptPlanRevalidator] = {
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: revalidate_drama_script_plan_draft,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: revalidate_narration_script_plan_draft,
}


async def revalidate_script_plan_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> ScriptPlanDraftRevalidation:
    """把一份 script_plan 草稿交给它那条路线的重判器，返回路线中立的重判结果。

    内容确认的读时重算按 kind 走这一个入口：三条路线的重判器签名同形、结果同构，内容确认
    因此不必认得任一条路线的内部形状，也不会在新增变体时漏掉一处分派。晋升侧仍各自直接调用
    自己那个重判器——它们要用到 basis 与 schema_failed 这些落盘所需、呈现层不关心的位。

    软违约不进本结果：内容确认面向创作者，降级提示由编辑器预览面板按当前正文实时判出，服务端
    快照会在用户就地补上引用后仍留在页面上。软违约只随 Agent 侧的报告与回执呈现。

    ``draft.kind`` 不是 script_plan 的三个来源之一（如误传 prompt_authoring 草稿）时抛 ``ValueError``。
    """
    if draft.kind == QUARANTINE_KIND_SCRIPT_PLAN:
        reference = await revalidate_reference_script_plan_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=config_resolver,
        )
        content = None if reference.schema_failed else {"units": reference.flat_units}
        return ScriptPlanDraftRevalidation(reference.violations, content)
    revalidator = _SINGLE_SCRIPT_PLAN_REVALIDATORS.get(draft.kind)
    if revalidator is None:
        raise ValueError(f"不是 script_plan 草稿来源，无法重判: {draft.kind}")
    single = await revalidator(project_path, project, episode, draft, config_resolver=config_resolver)
    return ScriptPlanDraftRevalidation(single.violations, None if single.schema_failed else single.content)


async def _open_reference_script_plan_for_edit(
    ctx: DraftContext,
    episode: int,
    source: str | None,
) -> None:
    """把本集正式参考生视频 script_plan 取回为草稿（正式文件保持原样），返回给 Agent 的编辑指引。"""
    project_path = ctx.project_path
    # source 在写草稿前校验：草稿一旦落盘就把它记进 meta.source 供晋升重判用，若此刻
    # 是个缺失/改名/写错的路径，晋升会在 _load_novel_source 上反复报错，而草稿已在场
    # 又挡住重新取回改正 source——Agent 会卡在一个自己改不动的死角。校验失败时不落盘，
    # 无效参数不留持久副作用。
    if source is not None:
        try:
            await asyncio.to_thread(_validate_open_source, project_path, source)
        except ValueError as exc:
            raise DraftWorkflowError("draft_open_failed", f"❌ {exc}") from exc

    # 草稿有无的检查、正式文件的读取、草稿的写入须在同一把锁的临界区内完成：拆开在锁外
    # 各做一次的话，同一集的两个并发取回请求可能都先看到「无草稿」，再都各自写入草稿，
    # 后写者悄悄覆盖前者的 content 与 meta.source。写临界区与 Web 端保存、迁移同一把锁，
    # 读也持锁避免取回一份写到一半的 script_plan。
    script_plan_path = script_review.official_reference_script_plan_path(project_path, episode)
    await run_sync_transaction(
        _open_script_plan_draft,
        project_path,
        episode,
        script_plan_path,
        QUARANTINE_KIND_SCRIPT_PLAN,
        source,
        _reference_script_plan_draft_shape,
        f"❌ 第 {episode} 集没有可编辑的正式 script_plan（{script_plan_path} 不存在、不是合法 JSON，"
        "或 units 不是非空数组）；首次生成请调用 generate_script_plan",
    )


_SCRIPT_PLAN_EDIT_OPENERS: dict[
    str,
    Callable[[DraftContext, int, str | None], Awaitable[None]],
] = {
    QUARANTINE_KIND_SCRIPT_PLAN: _open_reference_script_plan_for_edit,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: _open_drama_script_plan_for_edit,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: _open_narration_script_plan_for_edit,
}

_SINGLE_SCRIPT_PLAN_PROMOTERS: dict[
    str,
    Callable[[DraftContext, int, QuarantinedDraft], Awaitable[None]],
] = {
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN: _promote_drama_script_plan,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN: _promote_narration_script_plan,
}


class DraftWorkflow:
    """Open, patch, promote, and discard drafts without host envelopes."""

    def __init__(self, ctx: DraftContext):
        self.ctx = ctx

    async def _kind(self, episode: int, doc_type: str, *, allow_stale_discard: bool = False) -> str:
        if isinstance(episode, bool) or episode < 1:
            raise DraftWorkflowError("invalid_request", "episode must be a positive integer")
        kind = DOC_TYPE_TO_QUARANTINE_KIND.get(doc_type)
        if kind is None:
            raise DraftWorkflowError("invalid_request", f"unsupported doc_type: {doc_type}")
        if allow_stale_discard:
            return kind
        project = await asyncio.to_thread(self.ctx.pm.load_project_readonly, self.ctx.project_name)
        active_script_plan = script_review.script_plan_quarantine_kind(project)
        compatible = kind == active_script_plan or (
            kind == QUARANTINE_KIND_PROMPT_AUTHORING and _uses_reference_video_units(project)
        )
        if not compatible:
            raise DraftWorkflowError(
                "doc_type_not_applicable", f"doc_type {doc_type} does not match the project workflow"
            )
        return kind

    def _read_if_present(
        self,
        episode: int,
        kind: str,
    ) -> dict[str, Any] | None:
        draft = read_quarantine(self.ctx.project_path, episode, kind)
        if draft is not None:
            payload = draft_payload(draft)
            payload["formal_revision"] = script_review.content_fingerprint(self._formal_path(episode, kind))
            return payload
        if quarantine_exists(self.ctx.project_path, episode, kind):
            detail = f"episode {episode} {doc_type_for_kind(kind)} draft is not a valid JSON envelope"
            raise DraftWorkflowError("draft_not_found", detail)
        return None

    def _read(
        self,
        episode: int,
        kind: str,
    ) -> dict[str, Any]:
        payload = self._read_if_present(episode, kind)
        if payload is not None:
            return payload
        detail = f"episode {episode} has no {doc_type_for_kind(kind)} draft"
        raise DraftWorkflowError("draft_not_found", detail)

    def _draft_snapshot(
        self,
        episode: int,
        kind: str,
    ) -> tuple[QuarantinedDraft | None, str | None]:
        draft = read_quarantine(self.ctx.project_path, episode, kind)
        return draft, draft_revision(draft) if draft is not None else None

    def _formal_path(self, episode: int, kind: str) -> Path:
        if kind == QUARANTINE_KIND_PROMPT_AUTHORING:
            return self.ctx.project_path / "scripts" / episode_script_filename(episode)
        project = self.ctx.pm.load_project_readonly(self.ctx.project_name)
        path = script_review.script_plan_path(self.ctx.project_path, project, episode)
        if path is None:
            raise ValueError("project has no formal script_plan document")
        return path

    def _open_reference_prompt_authoring(
        self,
        episode: int,
        resolved: str,
    ) -> None:
        script = self.ctx.pm.load_script_readonly(
            self.ctx.project_name,
            episode_script_filename(episode),
        )
        units = script.get("video_units")
        if not isinstance(units, list) or not units:
            raise DraftWorkflowError("draft_source_missing", "formal reference prompt_authoring has no video_units")
        write_quarantine(
            self.ctx.project_path,
            episode,
            resolved,
            content={
                "title": script.get("title") or f"第{episode}集",
                "units": [{"text": unit.get("text", "")} for unit in units if isinstance(unit, dict)],
            },
            violations=[],
            meta={"base_fingerprint": script_review.content_fingerprint_of_data(script)},
        )

    async def open(
        self,
        episode: int,
        doc_type: str,
        source: str | None = None,
    ) -> dict[str, Any]:
        resolved = await self._kind(episode, doc_type)
        path = quarantine_path(self.ctx.project_path, episode, resolved)

        try:
            async with ProjectManager(str(self.ctx.projects_root)).async_file_lock(path):
                existing = await asyncio.to_thread(self._read_if_present, episode, resolved)
                if existing is not None:
                    return existing
                if resolved == QUARANTINE_KIND_PROMPT_AUTHORING:
                    await run_sync_transaction(
                        self._open_reference_prompt_authoring,
                        episode,
                        resolved,
                    )
                else:
                    await _SCRIPT_PLAN_EDIT_OPENERS[resolved](self.ctx, episode, source)
                return await asyncio.to_thread(self._read, episode, resolved)
        except DraftWorkflowError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DraftWorkflowError("draft_open_failed", str(exc)) from exc

    def _patch_locked(
        self,
        episode: int,
        resolved: str,
        content: dict[str, Any],
        base_revision: str,
        accept_formal_revision: str | None,
        accepts_formal_revision: bool,
        source: str | None,
        updates_source: bool,
        before_commit: Callable[[], None] | None,
    ) -> dict[str, Any]:
        path = quarantine_path(self.ctx.project_path, episode, resolved)
        draft = read_quarantine(self.ctx.project_path, episode, resolved)
        if draft is None:
            return self._read(episode, resolved)
        actual_revision = draft_revision(draft)
        if base_revision != actual_revision:
            raise DraftWorkflowError(
                "revision_conflict",
                f"draft revision changed: expected {base_revision}, actual {actual_revision}",
            )
        meta = draft.meta
        if updates_source:
            if resolved == QUARANTINE_KIND_PROMPT_AUTHORING:
                raise DraftWorkflowError("invalid_request", "source is only valid for script_plan drafts")
            _load_novel_source(self.ctx.project_path, source)
            meta = {**meta, "source": source}
        if accepts_formal_revision:
            actual_formal_revision = script_review.content_fingerprint(self._formal_path(episode, resolved))
            if accept_formal_revision != actual_formal_revision:
                raise DraftWorkflowError(
                    "formal_revision_conflict",
                    "formal document changed again: "
                    f"expected {accept_formal_revision}, actual {actual_formal_revision}",
                )
            meta = {**meta, "base_fingerprint": actual_formal_revision}
        if before_commit is not None:
            before_commit()
        atomic_write_json(
            path,
            {
                "kind": draft.kind,
                "episode": draft.episode,
                "meta": meta,
                "violations": draft.violations,
                "content": content,
            },
        )
        return self._read(episode, resolved)

    async def patch(
        self,
        episode: int,
        doc_type: str,
        content: dict[str, Any],
        base_revision: str,
        accept_formal_revision: str | None = None,
        accepts_formal_revision: bool = False,
        source: str | None = None,
        updates_source: bool = False,
        *,
        before_commit: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        resolved = await self._kind(episode, doc_type)
        path = quarantine_path(self.ctx.project_path, episode, resolved)
        async with ProjectManager(str(self.ctx.projects_root)).async_file_lock(path):
            return await run_sync_transaction(
                self._patch_locked,
                episode,
                resolved,
                content,
                base_revision,
                accept_formal_revision,
                accepts_formal_revision,
                source,
                updates_source,
                before_commit,
            )

    async def promote(
        self,
        episode: int,
        doc_type: str,
        base_revision: str,
        *,
        before_commit: Callable[[], None] | None = None,
        before_lock: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        resolved = await self._kind(episode, doc_type)
        path = quarantine_path(self.ctx.project_path, episode, resolved)
        result_path: Path | None = None
        # 晋升回执：参考生视频这条路回一段带软违约段的摘要，其余变体没有可随产物呈现的降级提示，
        # 沿用通用短句。回执由晋升器产出而非在此拼装——软违约的措辞与拆分回执同一出口。
        message: str | None = None
        if before_lock is not None:
            before_lock()
        async with ProjectManager(str(self.ctx.projects_root)).async_file_lock(path):
            draft, actual_revision = await asyncio.to_thread(
                self._draft_snapshot,
                episode,
                resolved,
            )
            if draft is None:
                return await asyncio.to_thread(self._read, episode, resolved)
            if base_revision != actual_revision:
                raise DraftWorkflowError(
                    "revision_conflict",
                    f"draft revision changed: expected {base_revision}, actual {actual_revision}",
                )
            try:
                if resolved in _SINGLE_SCRIPT_PLAN_PROMOTERS:
                    await _SINGLE_SCRIPT_PLAN_PROMOTERS[resolved](self.ctx, episode, draft)
                elif resolved == QUARANTINE_KIND_SCRIPT_PLAN:
                    message = await _promote_reference_script_plan(
                        self.ctx,
                        episode,
                        draft,
                        before_commit=before_commit,
                    )
                else:
                    project = await asyncio.to_thread(self.ctx.pm.load_project_readonly, self.ctx.project_name)
                    if script_review.gate_blocks_prompt_authoring(self.ctx.project_path, project, episode):
                        raise DraftWorkflowError(
                            "review_required", "script_plan content must be confirmed before promoting prompt_authoring"
                        )
                    generator = await ScriptGenerator.create(
                        self.ctx.project_path,
                        config_resolver=self.ctx.config_resolver,
                    )
                    promote_kwargs = (
                        {"expected_fingerprint": draft.meta["base_fingerprint"]}
                        if "base_fingerprint" in draft.meta
                        else {}
                    )
                    result_path = await generator.promote_reference_prompt_authoring_draft(
                        episode,
                        _prompt_authoring_lock_held=True,
                        **promote_kwargs,
                    )
            except DraftWorkflowError:
                raise
            except ScriptWriteConflict as exc:
                raise DraftWorkflowError(
                    "formal_revision_conflict", _render_prompt_authoring_conflict_report(episode, draft, exc)
                ) from exc
            except DraftViolation as exc:
                raise DraftWorkflowError("draft_invalid", str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise DraftWorkflowError("draft_promote_failed", str(exc)) from exc
        value: dict[str, Any] = {
            "episode": episode,
            "doc_type": doc_type,
            "promoted": True,
            "message": message if message is not None else "草稿已校验并晋升",
        }
        if result_path is not None:
            value["path"] = str(result_path)
        return value

    async def discard(
        self,
        episode: int,
        doc_type: str,
        base_revision: str,
    ) -> dict[str, Any]:
        resolved = await self._kind(episode, doc_type, allow_stale_discard=True)
        path = quarantine_path(self.ctx.project_path, episode, resolved)
        async with ProjectManager(str(self.ctx.projects_root)).async_file_lock(path):
            draft, actual_revision = await asyncio.to_thread(
                self._draft_snapshot,
                episode,
                resolved,
            )
            if draft is not None:
                if base_revision != actual_revision:
                    raise DraftWorkflowError(
                        "revision_conflict",
                        f"draft revision changed: expected {base_revision}, actual {actual_revision}",
                    )
            discarded = path.exists()
            path.unlink(missing_ok=True)
        return {"episode": episode, "doc_type": doc_type, "discarded": discarded}


def doc_type_for_kind(kind: str) -> str:
    return next(doc_type for doc_type, mapped in DOC_TYPE_TO_QUARANTINE_KIND.items() if mapped == kind)
