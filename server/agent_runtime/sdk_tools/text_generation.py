"""SDK MCP adapters for text generation and video capability queries."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, NamedTuple, Protocol, cast

from claude_agent_sdk import tool
from pydantic import ValidationError

from lib import script_review
from lib.artifact_manifest import ArtifactBasis
from lib.config.resolver import ConfigResolver
from lib.draft_quarantine import (
    PROMOTE_TOOL_NAME,
    QUARANTINE_KIND_DRAMA_STEP1,
    QUARANTINE_KIND_NARRATION_STEP1,
    QUARANTINE_KIND_STEP1,
    QUARANTINE_KIND_STEP2,
    STEP1_EDIT_TOOL_NAME,
    QuarantinedDraft,
    clear_quarantine,
    quarantine_and_report,
    quarantine_exists,
    quarantine_path,
    read_quarantine,
    write_quarantine,
)
from lib.draft_violation import DraftViolation
from lib.episode_paths import (
    STEP1_FILENAMES,
    episode_drafts_dir,
)
from lib.json_io import load_json_or_none
from lib.reference_video.script_preview import (
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_EPISODE,
    WARN_SILENT_MODEL,
    WARN_SPEAKER_WITHOUT_AUDIO,
)
from lib.script_generator import ScriptGenerator
from lib.script_models import (
    NarrationStep1Draft,
    build_drama_normalized_script_model,
    build_reference_units_step1_model,
)
from lib.speech_composition import admit_script_unit
from server.agent_runtime.sdk_tools._context import (
    MAX_INSTRUCTIONS_LEN,
    ToolContext,
    tool_error,
    tool_outcome_response,
    tool_services,
)
from server.text_generation import (
    ReferenceSplitCaps,
    _build_reference_units_from_flat,
    _collect_narration_violations,
    _collect_reference_flat_violations,
    _coverage_source_scope,
    _fetch_caps_with_fallback,
    _fetch_reference_caps_with_fallback,
    _load_novel_source,
    _load_step1_source_with_basis,
    _narration_step1_path,
    _reference_result_text,
    _reference_voice_warning_lines,
    _uses_reference_video_units,
)
from server.tool_runtime import (
    TextGenerationRequest as ToolTextGenerationRequest,
)
from server.tool_runtime import (
    ToolRequest,
    get_video_capabilities,
)
from server.tool_runtime import (
    confirm_script_review as run_confirm_script_review,
)
from server.tool_runtime import (
    generate_episode_script as run_generate_episode_script,
)
from server.tool_runtime import (
    generate_step1 as run_generate_step1,
)

# 四个分集数据生成工具共用的 instructions 参数 schema：用户意见原样注入 prompt 末尾的
# 「用户意见」分节，遵循强度由正文表达（需要强约束时在正文写明）。
_INSTRUCTIONS_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": (
        "用户对本次生成的意见原文（可选）；原样注入 prompt 末尾的「用户意见」分节，"
        f"遵循强度由正文表达，缺省/空白视同未传，最长 {MAX_INSTRUCTIONS_LEN} 字符"
    ),
}

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# get_video_capabilities
# ---------------------------------------------------------------------------

# 本模块的能力查询函数（``_fetch_caps_with_fallback`` / ``_fetch_reference_caps_with_fallback``
# 及 ``_context`` 的 ``resolve_video_caps`` /
# ``fetch_video_caps``）未注入解析器时一律省略 ``config_resolver`` 关键字，不传 ``None``：
# 这些符号会被整体替换为不接受该关键字的替身，调用形状须与不带该关键字的签名兼容。


def get_video_capabilities_tool(ctx: ToolContext):
    @tool(
        "get_video_capabilities",
        "查视频模型能力（model 粒度）+ 用户项目偏好。返回 JSON；"
        "参考生视频项目另含 reference_unit_durations（按 unit 有无 @ 引用分开的两套生效档位）。"
        "能力按项目生成模式定轴，全项目同一口径，无需指定剧集。",
        {"type": "object", "properties": {}},
    )
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        outcome = await get_video_capabilities(ToolRequest(None), ctx.scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("video_capabilities", outcome)

    return _handler


# ---------------------------------------------------------------------------
# generate_episode_script
# ---------------------------------------------------------------------------


def _generate_episode_script_tool(ctx: ToolContext):
    return generate_episode_script_tool(ctx)


# ---------------------------------------------------------------------------
# confirm_script_review
# ---------------------------------------------------------------------------


def _confirm_script_review_tool(ctx: ToolContext):
    return confirm_script_review_tool(ctx)


# ---------------------------------------------------------------------------
# drama generate_step1 variant
# ---------------------------------------------------------------------------


def _generate_drama_step1_tool(ctx: ToolContext):
    return generate_step1_tool(ctx)


def _generate_reference_step1_tool(ctx: ToolContext):
    return generate_step1_tool(ctx)


def _generate_narration_step1_tool(ctx: ToolContext):
    return generate_step1_tool(ctx)


# ---------------------------------------------------------------------------
# reference-video generate_step1 variant
# ---------------------------------------------------------------------------


#: 落盘照常、只随产物呈现的容忍 warning（声音降级）。其余 warning 键（未登记 mention /
#: 说话人、语法误用）在机器产物这条路上是阻断违约，不走容忍分支。
_TOLERATED_VOICE_WARNINGS = (
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_MODEL,
    WARN_SILENT_EPISODE,
)


class ReferenceDraftRevalidation(NamedTuple):
    """step1 草稿读时重判的结果。

    ``schema_failed`` 显式区分两个阶段：True 表示草稿连产出时的 schema 都没过（``flat_units``
    必为空，调用方只能按 ``draft.content`` 原样呈现）；False 时 ``flat_units`` 是收编后的扁平
    产出，``violations`` 为空即可晋升。两者的处置不同（原样 vs 收编），故不靠 ``flat_units``
    是否为空来反推。
    """

    violations: list[DraftViolation]
    flat_units: list[dict[str, Any]]
    caps: ReferenceSplitCaps
    schema_failed: bool
    basis: ArtifactBasis | None


async def revalidate_reference_step1_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> ReferenceDraftRevalidation:
    """按产出时那套校验器全量重判 step1 草稿，只读、不写盘、不清草稿。

    重判走的是拆分工具用的同一个函数（``_collect_reference_flat_violations``），不是它的简化
    副本：晋升口径、内容确认的读时重算与产出口径必须同一份代码，否则「这里放行、下次
    生成时被拒」这类分叉会重新出现。能力与源文都重新解析——草稿在场期间用户可能改过模型配置或
    源文，重判要对着现值判。

    不依赖 ``ToolContext``（``project_path`` / ``project`` 由调用方传入而非从 ctx 派生）：
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
    novel_text, _prompt_inputs, step1_basis = await asyncio.to_thread(
        _load_step1_source_with_basis,
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

    # 手改过的草稿先过产出时那份 schema：拆分侧由 response_schema 与 _parse_step1_json 卡住时长
    # 枚举与字段非空，晋升侧漏掉这一层的话，把 duration_seconds 改成非档位值、或整个删掉（收成
    # 0 秒）都能一路晋升进正式文件——正是本机制要防的「正式文件被污染」。schema 违约在这条路上
    # 没有 backend 可重试（内容是 Agent 写的），故同样回报告让它继续改。
    #
    # 外层形状（units 缺失 / 不是数组 / 空数组）与逐 unit 的字段违约走同一条报告路径：两者都是
    # Agent 编辑草稿时会犯的错，只有后者刷新报告的话，前者就把它甩出了「改完再晋升」的循环。
    raw_units = draft.content.get("units")
    schema = build_reference_units_step1_model(split_caps.durations)
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
                    f"草稿的 content 不符合 step1 产出结构：{exc}；"
                    f"每个 unit 须有非空 source_text / text，且 duration_seconds 取自模型档位 {split_caps.durations}",
                    code="schema_invalid",
                )
            ]
    if violations:
        return ReferenceDraftRevalidation(violations, [], split_caps, schema_failed=True, basis=step1_basis)

    source_language = project.get("source_language")
    violations = _collect_reference_flat_violations(
        flat_units,
        project,
        episode=episode,
        novel_text=novel_text,
        caps=split_caps,
        source_language=source_language,
    )
    return ReferenceDraftRevalidation(violations, flat_units, split_caps, schema_failed=False, basis=step1_basis)


async def _promote_reference_step1(ctx: ToolContext, episode: int, draft: QuarantinedDraft) -> dict[str, Any]:
    """按产出时那套校验器全量重判 step1 草稿，通过则晋升为正式 step1 并清除草稿。"""
    project_path = ctx.project_path
    project = ctx.pm.load_project(ctx.project_name)
    revalidation = await revalidate_reference_step1_draft(
        project_path,
        project,
        episode,
        draft,
        config_resolver=ctx.config_resolver,
    )
    violations, flat_units, split_caps = revalidation.violations, revalidation.flat_units, revalidation.caps
    if revalidation.schema_failed:
        # schema 违约：写回 Agent 手里那份原样内容，不做收编——字段被改坏时收编会把它的原稿
        # 改形，它照着报告回去看反而对不上自己写的东西。
        report = quarantine_and_report(
            project_path,
            episode,
            QUARANTINE_KIND_STEP1,
            content=draft.content,
            violations=violations,
            meta=draft.meta,
        )
        return {"content": [{"type": "text", "text": report}], "is_error": True}
    if violations:
        report = quarantine_and_report(
            project_path,
            episode,
            QUARANTINE_KIND_STEP1,
            content={"units": flat_units},
            violations=violations,
            meta=draft.meta,
        )
        return {"content": [{"type": "text", "text": report}], "is_error": True}

    units = _build_reference_units_from_flat(flat_units, project, episode=episode, max_refs=split_caps.max_refs)
    # 写盘经单一出口（lib.script_review.write_step1_locked）：锁、基线比对、step2 草稿清理
    # 只存在那一处。基线指纹取自取回 / 草稿产出时记进 meta 的 base_fingerprint——正式文件在草稿
    # 产出后被其他写入方（Web 端保存、另一次拆分）改过时晋升中止、返回冲突报告让 Agent 合并，
    # 不静默覆盖对方的修改。引入基线前产出的存量草稿缺该键，按无基线晋升。
    expected = (
        draft.meta["base_fingerprint"] if "base_fingerprint" in draft.meta else script_review.UNCHECKED_FINGERPRINT
    )
    try:
        with script_review.step1_write_lock(project_path, episode) as step1_path:
            script_review.write_step1_locked(
                project_path,
                episode,
                {"units": units},
                expected_fingerprint=expected,
                basis=revalidation.basis,
            )
            # 落盘成功后才清草稿：写盘失败（含冲突）时草稿还在，改完重试晋升即可，不会两头皆空。
            # 清理与写盘同一临界区：并发的取回请求不会在两步之间看到「正式文件已是新内容、
            # 草稿却还在场」的中间态。
            clear_quarantine(project_path, episode, QUARANTINE_KIND_STEP1)
    except script_review.Step1WriteConflict as conflict:
        return {
            "content": [
                {
                    "type": "text",
                    "text": _render_step1_conflict_report(
                        episode,
                        draft,
                        conflict,
                        to_draft_shape=_reference_step1_draft_shape,
                        field_hint="content.units",
                    ),
                }
            ],
            "is_error": True,
        }
    warning_lines = _reference_voice_warning_lines([f["text"] for f in flat_units], project, split_caps.voice)
    return {
        "content": [{"type": "text", "text": _reference_result_text(step1_path, units, warning_lines, action="晋升")}]
    }


def _render_step1_conflict_report(
    episode: int,
    draft: QuarantinedDraft,
    conflict: script_review.Step1WriteConflict,
    *,
    to_draft_shape: Callable[[dict[str, Any]], dict[str, Any] | None],
    field_hint: str,
) -> str:
    """渲染晋升遇乐观并发冲突时回给 Agent 的结构化报告：最新内容 + 合并指引。

    报告要让编辑方能就地合并：附上盘上现值转成草稿那一层的形状（与草稿 ``content`` 同形，可逐条
    对照），并指明确认合并后把 ``meta.base_fingerprint`` 更新为现值指纹——这一步是显式确认
    「我已看过并合并了对方的修改」，之后重新晋升才会放行；不更新就重试只会拿到同一份报告。

    ``to_draft_shape`` 与 ``field_hint`` 由各变体传入：草稿层的形状与可改字段按变体不同，
    附一份对不上形状的「最新内容」比不附更误导。
    """
    latest_content = to_draft_shape(conflict.current_content) if conflict.current_content is not None else None
    if latest_content is not None:
        latest = json.dumps(latest_content, ensure_ascii=False, indent=2)
        latest_block = f"当前正式 step1 的最新内容（与草稿 content 同形）：\n{latest}"
    else:
        latest_block = "当前正式文件不存在或不是合法的 step1 JSON，无法附上最新内容；请自行读取该文件确认。"
    # 指纹按 JSON 字面量给：正式文件已被删除时现值是 null，写成 "None" 会让 Agent 把这串字符
    # 当基线填回 meta，之后每次重晋升都比对不上、拿到同一份报告，冲突再也解不掉。
    actual_literal = json.dumps(conflict.actual)
    return (
        "❌ 晋升中止（并发冲突）：正式 step1 在本草稿产出后已被其他写入方（如 Web 端保存）修改，"
        "直接晋升会覆盖对方的修改，本次未写盘、草稿仍在场。\n"
        f"草稿基线指纹: {json.dumps(conflict.expected)}；盘上现值指纹: {actual_literal}\n\n"
        f"{latest_block}\n\n"
        f"处置：对照上方最新内容与草稿 {draft.path} 的 {field_hint}，把对方的修改合并进草稿；"
        f"合并完成后把草稿 meta.base_fingerprint 更新为 {actual_literal}，"
        f'再调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 重新晋升。'
    )


def _flatten_reference_step1_units(units: list[Any]) -> list[dict[str, Any]]:
    """正式 step1 的结构化 unit 表 → 扁平草稿单元（``_build_reference_units_from_flat`` 的逆向）。

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


def _reference_step1_draft_shape(content: dict[str, Any]) -> dict[str, Any] | None:
    """正式参考 step1 内容 → 扁平草稿结构；不是合法 step1 时返回 None。"""
    units = content.get("units")
    if not isinstance(units, list) or not units:
        return None
    return {"units": _flatten_reference_step1_units(units)}


def _drama_step1_draft_shape(content: dict[str, Any]) -> dict[str, Any] | None:
    """正式 drama step1 内容 → 可编辑草稿装的分镜结构；不是合法 step1 时返回 None。

    只剥 ``needs_replan``：它是按台词准入机械派生的标记，让 Agent 编辑派生物等于给漂移开
    口子——晋升时照样按 ``content`` 现值重新派生。其余字段原样带过，包括 ``scene_id``：它是
    step2 视觉层的对齐锚，草稿里写坏了要由晋升侧的 schema 逐条报出来，不能在这一层替它填。
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


class SingleStep1DraftRevalidation(NamedTuple):
    """只有一个草稿位的两条路线（drama / narration）的 step1 草稿读时重判结果。

    ``schema_failed`` 显式区分两个阶段：True 表示草稿连产出时的 schema 都没过（``content`` 必为
    空 dict，调用方只能按 ``draft.content`` 原样呈现）；False 时 ``content`` 是经 schema 收编后的
    草稿层形状（drama 的 ``{title, scenes}``、narration 的 ``{segments}``），``violations`` 为空即
    可晋升。两者的处置不同（原样 vs 收编），故不靠 ``content`` 是否为空来反推。

    两条路线共用一个类型而非各立一个：字段与语义逐字相同，分成两个只会让 ``revalidate_step1_draft``
    的归一分支按类型各写一遍同样的事。参考生视频另有 ``ReferenceDraftRevalidation``——它的产出是
    扁平 units 且要带回档位，形状本就不同。
    """

    violations: list[DraftViolation]
    content: dict[str, Any]
    schema_failed: bool
    basis: ArtifactBasis | None


async def revalidate_drama_step1_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> SingleStep1DraftRevalidation:
    """按产出时那套校验器全量重判 drama step1 草稿，只读、不写盘、不清草稿。

    校验器就是产出时那一个（按当前能力档位构造的 ``DramaNormalizedScript``），不是它的副本：
    档位随项目配置变化，草稿里那个曾经合法的秒数今天可能已不在档位内，用旧枚举放行等于把一份
    供应商不接的时长固化进正式文件。``needs_replan`` 同样按现值重新派生，与生成侧同一口径。

    与另两条路线的重判器同样不依赖 ``ToolContext``：晋升工具与内容确认的读时重算共用
    本函数，后者只有 ``ProjectManager``，没有 Agent 工具的 ctx。

    源文不可读（``meta.source`` 指向缺失 / 改名的路径）时抛 ``ValueError``。
    """
    # 源文可能达数百 KB，同步读盘卸到线程：内容确认的读时重算在请求协程里跑，直接读会
    # 占用事件循环、拖慢并发的其它请求。与另两条路线同口径。
    _novel_text, _prompt_inputs, step1_basis = await asyncio.to_thread(
        _load_step1_source_with_basis,
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
            f"草稿的 content 不符合 step1 规范化产出结构：{exc}；"
            f"顶层须为 {{title, scenes}}，每个分镜的 duration_seconds 取自模型档位 {supported_durations}",
            code="schema_invalid",
        )
        return SingleStep1DraftRevalidation([violation], {}, schema_failed=True, basis=step1_basis)

    raw_scenes = content.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        violation = DraftViolation("草稿的 content.scenes 必须是非空的分镜对象数组", code="schema_invalid")
        return SingleStep1DraftRevalidation([violation], {}, schema_failed=True, basis=step1_basis)
    for scene in raw_scenes:
        admission = admit_script_unit("scenes", scene, ignore_marker=True)
        if admission.allowed:
            scene.pop("needs_replan", None)
        else:
            scene["needs_replan"] = True
    return SingleStep1DraftRevalidation([], content, schema_failed=False, basis=step1_basis)


async def _promote_drama_step1(ctx: ToolContext, episode: int, draft: QuarantinedDraft) -> dict[str, Any]:
    """按产出时那套校验器全量重判 drama step1 草稿，通过则晋升为正式 step1 并清除草稿。"""
    project_path = ctx.project_path
    project = ctx.pm.load_project(ctx.project_name)
    try:
        revalidation = await revalidate_drama_step1_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=ctx.config_resolver,
        )
    except ValueError as exc:
        return {"content": [{"type": "text", "text": f"❌ {exc}"}], "is_error": True}

    if revalidation.violations:
        # schema 违约时写回 Agent 手里那份原样内容，不做收编——字段被改坏时收编会把它的原稿
        # 改形，它照着报告回去看反而对不上自己写的东西。过了 schema 的那份则回写收编后的内容。
        report = quarantine_and_report(
            project_path,
            episode,
            QUARANTINE_KIND_DRAMA_STEP1,
            content=draft.content if revalidation.schema_failed else revalidation.content,
            violations=revalidation.violations,
            meta=draft.meta,
        )
        return {"content": [{"type": "text", "text": report}], "is_error": True}

    content = revalidation.content
    step1_basis = revalidation.basis
    raw_scenes = content["scenes"]

    # 基线指纹取自取回时记进 meta 的 base_fingerprint：正式文件在草稿产出后被其他写入方
    # （Web 端保存、重跑 normalize）改过时晋升中止、返回冲突报告让 Agent 合并，不静默覆盖。
    expected = (
        draft.meta["base_fingerprint"] if "base_fingerprint" in draft.meta else script_review.UNCHECKED_FINGERPRINT
    )
    step1_path = episode_drafts_dir(project_path, episode) / STEP1_FILENAMES["drama"]
    try:
        with script_review.formal_step1_lock(project_path, episode, step1_path):
            script_review.write_formal_step1_locked(
                project_path,
                episode,
                step1_path,
                content,
                expected_fingerprint=expected,
                basis=step1_basis,
            )
            # 落盘成功后才清草稿：写盘失败（含冲突）时草稿还在，改完重试晋升即可，不会两头皆空。
            # 清理与写盘同一临界区：并发的取回请求不会在两步之间看到「正式文件已是新内容、
            # 草稿却还在场」的中间态。
            clear_quarantine(project_path, episode, QUARANTINE_KIND_DRAMA_STEP1)
    except script_review.Step1WriteConflict as conflict:
        return {
            "content": [
                {
                    "type": "text",
                    "text": _render_step1_conflict_report(
                        episode,
                        draft,
                        conflict,
                        to_draft_shape=_drama_step1_draft_shape,
                        field_hint="content.scenes",
                    ),
                }
            ],
            "is_error": True,
        }
    replan = sum(1 for scene in raw_scenes if scene.get("needs_replan") is True)
    replan_note = f"；其中 {replan} 个分镜被标记为需重新规划（台词量未过准入）" if replan else ""
    return {
        "content": [
            {
                "type": "text",
                "text": f"✅ step1 规范化内容已校验通过并晋升: {step1_path}\n📊 {len(raw_scenes)} 个分镜{replan_note}",
            }
        ]
    }


async def _open_drama_step1_for_edit(ctx: ToolContext, episode: int, source: str | None) -> dict[str, Any]:
    """把本集正式 drama step1 取回为草稿（正式文件保持原样），返回给 Agent 的编辑指引。

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
            _load_novel_source(project_path, source)
        except ValueError as exc:
            return {"content": [{"type": "text", "text": f"❌ {exc}"}], "is_error": True}

    step1_path = episode_drafts_dir(project_path, episode) / STEP1_FILENAMES["drama"]
    with script_review.formal_step1_lock(project_path, episode, step1_path):
        # 已有草稿在场时不覆盖：那份草稿可能已含 Agent 未晋升的修改，拿正式文件盖过去等于
        # 抹掉它手上的工作。出路是继续改那份草稿再晋升。
        if quarantine_exists(project_path, episode, QUARANTINE_KIND_DRAMA_STEP1):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"❌ 第 {episode} 集已有 step1 草稿在场："
                            f"{quarantine_path(project_path, episode, QUARANTINE_KIND_DRAMA_STEP1)}\n"
                            "不覆盖它（可能已含未晋升的修改）；请直接编辑该草稿的 content.scenes[i]，"
                            f'改完调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 晋升。'
                        ),
                    }
                ],
                "is_error": True,
            }

        data = load_json_or_none(step1_path)
        draft_content = _drama_step1_draft_shape(data) if isinstance(data, dict) else None
        if draft_content is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"❌ 第 {episode} 集没有可编辑的正式 step1（{step1_path} 不存在、不是合法 JSON，"
                            "或 scenes 不是非空数组）；首次生成请调用 generate_step1"
                        ),
                    }
                ],
                "is_error": True,
            }

        draft_path = write_quarantine(
            project_path,
            episode,
            QUARANTINE_KIND_DRAMA_STEP1,
            content=draft_content,
            # 取回时无违约可报：草稿在这条路上是「编辑工位」而非「待修复草稿」，报告为空即可，
            # 晋升时照常全量重判。
            violations=[],
            # source 键一律写出（未指定时为 null），与生成侧同口径。base_fingerprint 记下此刻
            # 正式文件的指纹（与本临界区读到的 data 同一份内容）：晋升前按它做基线比对，取回与
            # 晋升之间正式文件被 Web 端保存等并发写入改过时中止晋升、报冲突让 Agent 合并。
            meta={
                "source": source or None,
                "base_fingerprint": script_review.content_fingerprint_of_data(data),
            },
        )
    scenes = draft_content["scenes"]
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"✅ 第 {episode} 集 step1 已取回可编辑草稿：{draft_path}\n"
                    f"📊 {len(scenes)} 个分镜（正式文件 {step1_path} 保持原样，未改动）\n\n"
                    "编辑口径：改 content.scenes[i] 的 scene_description / utterances / source_text / "
                    "duration_seconds / segment_break / 出场资产；needs_replan 是按台词准入派生的标记，"
                    "不在草稿里、也不要手写。增删分镜即增删数组元素。\n"
                    f'改完调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 全量校验并晋升回正式文件；'
                    "违约时返回逐条报告，继续改再晋升，无轮次上限。\n"
                    "草稿在场期间，内容确认与 step2 生成被阻塞；放弃修改就原样晋升（内容未变即等于回写原稿）。"
                ),
            }
        ]
    }


# ---------------------------------------------------------------------------
# narration step1 的草稿通道（校验、重判、取回、晋升）
# ---------------------------------------------------------------------------


async def revalidate_narration_step1_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> SingleStep1DraftRevalidation:
    """按产出时那套校验器全量重判 narration step1 草稿，只读、不写盘、不清草稿。

    重判走的是拆分工具用的同一个函数（``_collect_narration_violations``），不是它的简化副本：
    晋升口径、内容确认的读时重算与产出口径必须同一份代码，否则「这里放行、下次生成时被拒」
    这类分叉会重新出现。能力档位与源文都重新解析——隔离期间用户可能改过模型配置或源文，重判要
    对着现值判。

    与 ``revalidate_reference_step1_draft`` 同样不依赖 ``ToolContext``：内容确认的读时重算
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
    novel_text, prompt_inputs, step1_basis = await asyncio.to_thread(
        _load_step1_source_with_basis,
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

    # 手改过的草稿先过产出时那份 schema：拆分侧由 response_schema 与 _parse_step1_json 卡住字段与
    # 类型，晋升侧漏掉这一层的话，把 duration_seconds 改成字符串、或整个删掉 novel_text 都能一路
    # 晋升进正式文件——正是本机制要防的「正式文件被污染」。外层形状（segments 缺失 / 不是数组 /
    # 空数组）与逐分镜的字段违约走同一条报告路径：两者都是 Agent 编辑草稿时会犯的错。
    raw_segments = draft.content.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        logger.debug("草稿 content.segments 形状非法: %s", type(raw_segments).__name__)
        violation = DraftViolation("草稿的 content.segments 必须是非空的分镜对象数组", code="schema_invalid")
        return SingleStep1DraftRevalidation([violation], {}, schema_failed=True, basis=step1_basis)
    try:
        content = NarrationStep1Draft.model_validate(draft.content).model_dump()
    except ValidationError as exc:
        violation = DraftViolation(
            f"草稿的 content 不符合 step1 分镜拆分产出结构：{exc}；"
            "每个分镜须有非空 segment_id / novel_text、整数 duration_seconds、布尔 segment_break，"
            "以及 characters_in_segment / scenes / props 三个数组（无对应资产时写空数组）",
            code="schema_invalid",
        )
        return SingleStep1DraftRevalidation([violation], {}, schema_failed=True, basis=step1_basis)

    violations = _collect_narration_violations(
        content["segments"],
        supported_durations=supported_durations,
        characters=cast(dict[str, Any], prompt_inputs["characters"]),
        scenes=cast(dict[str, Any], prompt_inputs["scenes"]),
        props=cast(dict[str, Any], prompt_inputs["props"]),
        novel_text=novel_text,
        # 重判用的源文范围来自草稿自己的 meta.source，它是 Agent 可改的字段：取回时未指定 source
        # 的草稿记的是 null（整个 source/），若本集正式 step1 当初是按单个源文件产出的，这里会把
        # 一份原样取回、一字未改的草稿判成覆盖不全。把范围与改法一并写进消息，Agent 才走得出去
        # ——草稿在场时不能重新取回，改 meta.source 是它唯一的出路。
        source_scope=(
            f"{_coverage_source_scope(cast(str | None, draft.meta['source']))}"
            "，取自草稿的 meta.source；若该范围与产出本集正式 step1 时不同，"
            "请把 meta.source 改为当初那个源文件的相对路径后重试"
        ),
    )
    return SingleStep1DraftRevalidation(violations, content, schema_failed=False, basis=step1_basis)


def _narration_step1_draft_shape(content: dict[str, Any]) -> dict[str, Any] | None:
    """正式 narration step1 内容 → 草稿装的分镜结构；不是合法 step1 时返回 None。

    该变体没有机器派生字段可剥（``segment_id`` 是模型自己写的对齐锚、不由序号派生），草稿层与
    落盘层同形，只丢掉 ``segments`` 之外的顶层键。分镜项原样带过、包括非 dict 的项：跳过会让
    数组变短，若剩余分镜恰好都能过校验，晋升会悄悄覆盖正式文件、丢掉这一段而无人知晓。
    """
    segments = content.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    return {"segments": list(segments)}


async def _promote_narration_step1(ctx: ToolContext, episode: int, draft: QuarantinedDraft) -> dict[str, Any]:
    """按产出时那套校验器全量重判 narration step1 草稿，通过则晋升为正式 step1 并清除草稿。"""
    project_path = ctx.project_path
    project = ctx.pm.load_project(ctx.project_name)
    try:
        revalidation = await revalidate_narration_step1_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=ctx.config_resolver,
        )
    except ValueError as exc:
        return {"content": [{"type": "text", "text": f"❌ {exc}"}], "is_error": True}

    # schema 违约时写回 Agent 手里那份原样内容，不做收编——字段被改坏时收编会把它的原稿改形，
    # 它照着报告回去看反而对不上自己写的东西。过了 schema 的那份则回写收编后的内容。
    if revalidation.violations:
        content = draft.content if revalidation.schema_failed else revalidation.content
        report = quarantine_and_report(
            project_path,
            episode,
            QUARANTINE_KIND_NARRATION_STEP1,
            content=content,
            violations=revalidation.violations,
            meta=draft.meta,
        )
        return {"content": [{"type": "text", "text": report}], "is_error": True}

    # 基线指纹取自取回 / 隔离时记进 meta 的 base_fingerprint：正式文件在草稿产出后被其他写入方
    # （Web 端保存、重跑拆分）改过时晋升中止、返回冲突报告让 Agent 合并，不静默覆盖对方的修改。
    # 引入基线前产出的存量草稿缺该键，按无基线晋升。
    expected = (
        draft.meta["base_fingerprint"] if "base_fingerprint" in draft.meta else script_review.UNCHECKED_FINGERPRINT
    )
    step1_path = _narration_step1_path(project_path, episode)
    try:
        with script_review.formal_step1_lock(project_path, episode, step1_path):
            script_review.write_formal_step1_locked(
                project_path,
                episode,
                step1_path,
                revalidation.content,
                expected_fingerprint=expected,
                basis=revalidation.basis,
            )
            # 落盘成功后才清草稿：写盘失败（含冲突）时草稿还在，改完重试晋升即可，不会两头皆空。
            # 清理与写盘同一临界区：并发的取回请求不会在两步之间看到「正式文件已是新内容、草稿
            # 却还在场」的中间态。
            clear_quarantine(project_path, episode, QUARANTINE_KIND_NARRATION_STEP1)
    except script_review.Step1WriteConflict as conflict:
        conflict_report = _render_step1_conflict_report(
            episode,
            draft,
            conflict,
            to_draft_shape=_narration_step1_draft_shape,
            field_hint="content.segments",
        )
        return {"content": [{"type": "text", "text": conflict_report}], "is_error": True}
    segments = revalidation.content["segments"]
    summary = f"✅ step1 分镜拆分已校验通过并晋升: {step1_path}\n📊 {len(segments)} 个分镜"
    return {"content": [{"type": "text", "text": summary}]}


async def _open_narration_step1_for_edit(ctx: ToolContext, episode: int, source: str | None) -> dict[str, Any]:
    """把本集正式 narration step1 取回为草稿（正式文件保持原样），返回给 Agent 的编辑指引。

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
            _load_novel_source(project_path, source)
        except ValueError as exc:
            return {"content": [{"type": "text", "text": f"❌ {exc}"}], "is_error": True}

    step1_path = _narration_step1_path(project_path, episode)
    with script_review.formal_step1_lock(project_path, episode, step1_path):
        # 已有草稿在场时不覆盖：那份草稿可能已含 Agent 未晋升的修改，拿正式文件盖过去等于抹掉
        # 它手上的工作。出路是继续改那份草稿再晋升。
        if quarantine_exists(project_path, episode, QUARANTINE_KIND_NARRATION_STEP1):
            occupied = (
                f"❌ 第 {episode} 集已有 step1 草稿在场："
                f"{quarantine_path(project_path, episode, QUARANTINE_KIND_NARRATION_STEP1)}\n"
                "不覆盖它（可能已含未晋升的修改）；请直接编辑该草稿的 content.segments[i]，"
                f'改完调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 晋升。'
            )
            return {"content": [{"type": "text", "text": occupied}], "is_error": True}

        data = load_json_or_none(step1_path)
        draft_content = _narration_step1_draft_shape(data) if isinstance(data, dict) else None
        if draft_content is None:
            missing = (
                f"❌ 第 {episode} 集没有可编辑的正式 step1（{step1_path} 不存在、不是合法 JSON，"
                "或 segments 不是非空数组）；首次生成请调用 generate_step1"
            )
            return {"content": [{"type": "text", "text": missing}], "is_error": True}

        draft_path = write_quarantine(
            project_path,
            episode,
            QUARANTINE_KIND_NARRATION_STEP1,
            content=draft_content,
            # 取回时无违约可报：草稿在这条路上是「编辑工位」而非「待修复草稿」，报告为空即可，
            # 晋升时照常全量重判。
            violations=[],
            # source 键一律写出（未指定时为 null），与生成侧同口径。base_fingerprint 记下此刻正式
            # 文件的指纹（与本临界区读到的 data 同一份内容）：晋升前按它做基线比对，取回与晋升
            # 之间正式文件被 Web 端保存等并发写入改过时中止晋升、报冲突让 Agent 合并。
            meta={
                "source": source or None,
                "base_fingerprint": script_review.content_fingerprint_of_data(data),
            },
        )
    segments = draft_content["segments"]
    guide = (
        f"✅ 第 {episode} 集 step1 已取回可编辑草稿：{draft_path}\n"
        f"📊 {len(segments)} 个分镜（正式文件 {step1_path} 保持原样，未改动）\n\n"
        "编辑口径：改 content.segments[i] 的 novel_text / duration_seconds / segment_break / "
        "characters_in_segment / scenes / props；segment_id 是 step2 视觉层的对齐锚，改动后须保持"
        "全集唯一。增删分镜即增删数组元素。\n"
        "novel_text 逐字取自原文：全部分镜按序拼接后须与源文逐字相同，晋升时按此机械重判。\n"
        f"晋升重判的源文范围：{_coverage_source_scope(source)}（记在草稿 meta.source）；"
        "本集正式 step1 当初若按别的源文件产出，请先把 meta.source 改成那个路径，否则一字未改也判不过。\n"
        f'改完调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 全量校验并晋升回正式文件；'
        "违约时返回逐条报告，继续改再晋升，无轮次上限。\n"
        "草稿在场期间，内容确认与 step2 生成被阻塞；放弃修改就原样晋升（内容未变即等于回写原稿）。"
    )
    return {"content": [{"type": "text", "text": guide}]}


# ---------------------------------------------------------------------------
# step1 草稿的读时重判（按 kind 分派）
# ---------------------------------------------------------------------------


class Step1DraftRevalidation(NamedTuple):
    """按 kind 分派后的 step1 草稿重判结果，归一到呈现层口径。

    ``content`` 是要展示给用户的那一份草稿正文：过了 schema 时是收编后的现值形状（时长等已按
    当前档位重判），没过时为 None——调用方据此改用 ``draft.content`` 原样呈现 Agent 手改的文本。
    形状随变体不同（参考生视频 units、drama title+scenes、narration segments），呈现层按自己那条
    路线的卡片渲染。
    """

    violations: list[DraftViolation]
    content: dict[str, Any] | None


#: 只有一个草稿位的两条路线（drama / narration）→ 该变体的重判器。两者的结果同型
#: （``SingleStep1DraftRevalidation``），归一到呈现层口径的那一步逐字相同，故按 kind 查表而非
#: 各写一条分支。参考生视频不在表内：它的重判结果另带扁平 units 与档位，归一方式本就不同。
class _SingleStep1Revalidator(Protocol):
    """表内重判器的调用形状：草稿定位参数同形，能力解析器按关键字注入。"""

    def __call__(
        self,
        project_path: Path,
        project: dict[str, Any],
        episode: int,
        draft: QuarantinedDraft,
        *,
        config_resolver: ConfigResolver | None = None,
    ) -> Awaitable[SingleStep1DraftRevalidation]: ...


_SINGLE_STEP1_REVALIDATORS: dict[str, _SingleStep1Revalidator] = {
    QUARANTINE_KIND_DRAMA_STEP1: revalidate_drama_step1_draft,
    QUARANTINE_KIND_NARRATION_STEP1: revalidate_narration_step1_draft,
}


async def revalidate_step1_draft(
    project_path: Path,
    project: dict[str, Any],
    episode: int,
    draft: QuarantinedDraft,
    *,
    config_resolver: ConfigResolver | None = None,
) -> Step1DraftRevalidation:
    """把一份 step1 草稿交给它那条路线的重判器，返回路线中立的重判结果。

    内容确认的读时重算按 kind 走这一个入口：三条路线的重判器签名同形、结果同构，内容确认
    因此不必认得任一条路线的内部形状，也不会在新增变体时漏掉一处分派。晋升侧仍各自直接调用
    自己那个重判器——它们要用到 basis 与 schema_failed 这些落盘所需、呈现层不关心的位。

    ``draft.kind`` 不是 step1 的三个来源之一（如误传 step2 草稿）时抛 ``ValueError``。
    """
    if draft.kind == QUARANTINE_KIND_STEP1:
        reference = await revalidate_reference_step1_draft(
            project_path,
            project,
            episode,
            draft,
            config_resolver=config_resolver,
        )
        content = None if reference.schema_failed else {"units": reference.flat_units}
        return Step1DraftRevalidation(reference.violations, content)
    revalidator = _SINGLE_STEP1_REVALIDATORS.get(draft.kind)
    if revalidator is None:
        raise ValueError(f"不是 step1 草稿来源，无法重判: {draft.kind}")
    single = await revalidator(project_path, project, episode, draft, config_resolver=config_resolver)
    return Step1DraftRevalidation(single.violations, None if single.schema_failed else single.content)


async def _open_reference_step1_for_edit(ctx: ToolContext, episode: int, source: str | None) -> dict[str, Any]:
    """把本集正式参考生视频 step1 取回为草稿（正式文件保持原样），返回给 Agent 的编辑指引。"""
    project_path = ctx.project_path
    # source 在写草稿前校验：草稿一旦落盘就把它记进 meta.source 供晋升重判用，若此刻
    # 是个缺失/改名/写错的路径，晋升会在 _load_novel_source 上反复报错，而草稿已在场
    # 又挡住重新取回改正 source——Agent 会卡在一个自己改不动的死角。校验失败时不落盘，
    # 无效参数不留持久副作用。
    if source is not None:
        try:
            _load_novel_source(project_path, source)
        except ValueError as exc:
            return {"content": [{"type": "text", "text": f"❌ {exc}"}], "is_error": True}

    # 草稿有无的检查、正式文件的读取、草稿的写入须在同一把锁的临界区内完成：拆开在锁外
    # 各做一次的话，同一集的两个并发取回请求可能都先看到「无草稿」，再都各自写入草稿，
    # 后写者悄悄覆盖前者的 content 与 meta.source。写临界区与 Web 端保存、迁移同一把锁，
    # 读也持锁避免取回一份写到一半的 step1。
    with script_review.step1_write_lock(project_path, episode) as step1_path:
        # 已有草稿在场时不覆盖：那份草稿要么是待修复草稿、要么是上一轮取回后 Agent 已改了
        # 一半，拿正式文件盖过去等于抹掉它手上的修改。两种情况的出路相同——继续改那份
        # 草稿再晋升。
        if quarantine_exists(project_path, episode, QUARANTINE_KIND_STEP1):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"❌ 第 {episode} 集已有 step1 草稿在场："
                            f"{quarantine_path(project_path, episode, QUARANTINE_KIND_STEP1)}\n"
                            "不覆盖它（可能已含未晋升的修改）；请直接编辑该草稿的 content.units[i]，"
                            f'改完调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 晋升。'
                        ),
                    }
                ],
                "is_error": True,
            }

        data = load_json_or_none(step1_path)
        if not isinstance(data, dict):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"❌ 第 {episode} 集没有可编辑的正式 step1（{step1_path} 不存在或不是合法 "
                            "JSON）；首次生成请调用 generate_step1"
                        ),
                    }
                ],
                "is_error": True,
            }
        raw_units = data.get("units")
        if not isinstance(raw_units, list) or not raw_units:
            return {
                "content": [{"type": "text", "text": f"❌ {step1_path} 的 units 不是非空数组，无法取回编辑"}],
                "is_error": True,
            }

        draft_path = write_quarantine(
            project_path,
            episode,
            QUARANTINE_KIND_STEP1,
            content={"units": _flatten_reference_step1_units(raw_units)},
            # 取回时无违约可报：草稿在这条路上是「编辑工位」而非「待修复草稿」，报告为空即可，
            # 晋升时照常全量重判。
            violations=[],
            # source 键一律写出（未指定时为 null），与拆分侧同口径：晋升侧据此区分「本就按
            # 整个 source/ 判锚」与「meta 被改坏」。base_fingerprint 记下此刻正式文件的
            # 指纹（与本临界区读到的 data 同一份内容）：晋升前按它做基线比对，取回与晋升
            # 之间正式文件被 Web 端保存等并发写入改过时中止晋升、报冲突让 Agent 合并。
            meta={
                "source": source or None,
                "base_fingerprint": script_review.content_fingerprint_of_data(data),
            },
        )
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"✅ 第 {episode} 集 step1 已取回可编辑草稿：{draft_path}\n"
                    f"📊 {len(raw_units)} 个 unit（正式文件 {step1_path} 保持原样，未改动）\n\n"
                    "编辑口径：改 content.units[i] 的 text / source_text / duration_seconds；"
                    "unit_id 是派生物，不在草稿里、也不要手写。"
                    "增删 unit 即增删数组元素，unit_id 按新顺序重编。\n"
                    f'改完调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 全量校验并晋升回正式文件；'
                    "违约时返回逐条报告，继续改再晋升，无轮次上限。\n"
                    "草稿在场期间，内容确认与 step2 生成被阻塞；放弃修改就原样晋升（内容未变即等于回写原稿）。"
                ),
            }
        ]
    }


#: step1 草稿来源 → 该变体的「取回正式 step1 为可编辑草稿」入口。三条路线的草稿结构与
#: 正式文件名各不相同，取回流程却同形（持锁 → 拒覆盖在场草稿 → 读正式文件 → 写草稿并记基线），
#: 故按 kind 查表分派；缺席即「该变体无编辑通道」（ad 无结构化 step1，本就取不到变体）。
_STEP1_EDIT_OPENERS: dict[str, Callable[[ToolContext, int, str | None], Awaitable[dict[str, Any]]]] = {
    QUARANTINE_KIND_STEP1: _open_reference_step1_for_edit,
    QUARANTINE_KIND_DRAMA_STEP1: _open_drama_step1_for_edit,
    QUARANTINE_KIND_NARRATION_STEP1: _open_narration_step1_for_edit,
}


def open_step1_for_edit_tool(ctx: ToolContext):
    @tool(
        STEP1_EDIT_TOOL_NAME,
        "把本集已落盘的正式 step1 取回可编辑草稿（草稿结构：参考生视频为时长 + 原文锚 + 引用语法正文，"
        "drama 为分镜内容，narration 为逐字原文分镜），用于修改已有产出。改完调用 "
        f"{PROMOTE_TOOL_NAME} 全量校验并晋升回正式文件。"
        "正式 step1 不可用 Write/Edit 直改——它与 Web 端保存、迁移、重生成共享一把文件锁，"
        "只能经工具写盘。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "description": "剧集编号"},
                "source": {
                    "type": "string",
                    "description": (
                        "本集小说源文件路径（相对项目目录，如 source/episode_1.txt）；"
                        "晋升时按它重判原文锚 / 重取产物依据，不传则按整个 source/ 目录重解析（判定更松）"
                    ),
                },
            },
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            episode = int(args["episode"])
            source = args.get("source")
            project_data = ctx.pm.load_project(ctx.project_name)

            # 与晋升工具同一判据：只打开项目当前生成模式对应的 step1。其他生成模式的遗留
            # step1 与当前生成路径无关，取回来编辑只会诱导 Agent 修改不会被消费的文件；
            # 无结构化 step1 的变体（ad）则本就没有这条编辑通道。
            quarantine_kind = script_review.step1_quarantine_kind(project_data)
            opener = _STEP1_EDIT_OPENERS.get(quarantine_kind) if quarantine_kind is not None else None
            if opener is None:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"❌ 第 {episode} 集的 step1 没有草稿编辑通道（该项目无结构化 step1）",
                        }
                    ],
                    "is_error": True,
                }
            return await opener(ctx, episode, source)
        except Exception as exc:  # noqa: BLE001
            return tool_error(STEP1_EDIT_TOOL_NAME, exc)

    return _handler


# ---------------------------------------------------------------------------
# validate_and_promote_draft
# ---------------------------------------------------------------------------


#: 只有一个草稿位的两条路线（drama / narration）→ 该变体的晋升器。参考生视频不在表内：它另有
#: step2 的草稿位，晋升要按「step1 优先于 step2」的次序分派，不是单点查表。
_SINGLE_STEP1_PROMOTERS: dict[str, Callable[[ToolContext, int, QuarantinedDraft], Awaitable[dict[str, Any]]]] = {
    QUARANTINE_KIND_DRAMA_STEP1: _promote_drama_step1,
    QUARANTINE_KIND_NARRATION_STEP1: _promote_narration_step1,
}


async def _promote_single_step1_kind(ctx: ToolContext, episode: int, kind: str) -> dict[str, Any]:
    """drama / narration 的晋升入口：读回本变体的草稿信封，交给该 kind 的晋升器。

    两条路线各只有一个草稿位，处置只在「读得回 / 信封坏 / 不在场」三态间分派，故共用本函数
    ——各写一遍必然在某一态上分叉。
    """
    project_path = ctx.project_path
    draft = read_quarantine(project_path, episode, kind)
    if draft is not None:
        return await _SINGLE_STEP1_PROMOTERS[kind](ctx, episode, draft)
    if quarantine_exists(project_path, episode, kind):
        raise ValueError(
            f"step1 草稿 {quarantine_path(project_path, episode, kind)} "
            "不是合法的 JSON 信封（顶层须为对象且含 content 对象）；请修正该文件的 JSON 结构后重试"
        )
    return {"content": [{"type": "text", "text": f"❌ 第 {episode} 集没有待处置的草稿"}], "is_error": True}


def validate_and_promote_draft_tool(ctx: ToolContext):
    @tool(
        PROMOTE_TOOL_NAME,
        "重新全量校验本集的草稿（step1 产出、step2 视觉展开的待修复草稿，或取回编辑的正式 step1），"
        "通过则晋升为正式文件并清除草稿，不通过则返回刷新后的违约报告。"
        "在修改过草稿的 content 之后调用；可反复调用，无轮次上限。",
        {
            "type": "object",
            "properties": {"episode": {"type": "integer", "description": "剧集编号"}},
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            episode = int(args["episode"])
            project_path = ctx.project_path
            project_data = ctx.pm.load_project(ctx.project_name)

            # 按项目当前生成模式分派：其他生成模式的遗留草稿不能晋升，否则会以错误形状覆盖
            # 正式产物。与 generate_episode_script 忽略这些遗留草稿使用同一判据。
            quarantine_kind = script_review.step1_quarantine_kind(project_data)
            if quarantine_kind is not None and quarantine_kind in _SINGLE_STEP1_PROMOTERS:
                return await _promote_single_step1_kind(ctx, episode, quarantine_kind)

            if not _uses_reference_video_units(project_data):
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"❌ 第 {episode} 集当前不走参考生视频路径，盘上的参考路径草稿已与该集无关，不作晋升"
                            ),
                        }
                    ],
                    "is_error": True,
                }

            # step1 优先：step2 的保结构 diff 以正式 step1 为基底，step1 草稿还在场时判 step2
            # 只会拿旧基底得出误导性的结论。
            step1_draft = read_quarantine(project_path, episode, QUARANTINE_KIND_STEP1)
            if step1_draft is not None:
                return await _promote_reference_step1(ctx, episode, step1_draft)
            if quarantine_exists(project_path, episode, QUARANTINE_KIND_STEP1):
                raise ValueError(
                    f"step1 草稿 {quarantine_path(project_path, episode, QUARANTINE_KIND_STEP1)} 不是合法的 JSON "
                    "信封（顶层须为对象且含 content 对象）；请修正该文件的 JSON 结构后重试"
                )

            if quarantine_exists(project_path, episode, QUARANTINE_KIND_STEP2):
                # 晋升同样受 step1 内容确认约束：草稿在场期间用户在 Web 端改过 step1 会让确认指纹
                # 失效、该集回到 pending_review，此时晋升等于拿一份用户没确认过的 step1 合成正式
                # 剧本——常规生成路径在工具入口就被内容确认拦下，两条路不该在这一位上分叉。
                if script_review.gate_blocks_step2(project_path, project_data, episode):
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "⏸️ 本集 step1 尚未完成内容确认（或确认后内容又被改），step2 草稿暂不晋升。"
                                    "请在 Web 端审阅并确认本集 step1 内容后再调用本工具。"
                                ),
                            }
                        ],
                        "is_error": True,
                    }
                # 用异步工厂而非裸构造：晋升同样经 _add_metadata 落盘，裸构造会把
                # metadata.generator 记成 "unknown"，与直接生成路径的同一份产物对不上。
                if ctx.config_resolver is None:
                    generator = await ScriptGenerator.create(project_path)
                else:
                    generator = await ScriptGenerator.create(project_path, config_resolver=ctx.config_resolver)
                result_path = await generator.promote_reference_step2_draft(episode)
                return {"content": [{"type": "text", "text": f"✅ step2 视觉展开已校验通过并晋升: {result_path}"}]}

            return {
                "content": [{"type": "text", "text": f"❌ 第 {episode} 集没有待处置的草稿"}],
                "is_error": True,
            }
        except DraftViolation as exc:
            # 报告已由校验侧渲染（含逐条定位与处置指引），原样回传、不再加工具名前缀包裹。
            return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}
        except Exception as exc:  # noqa: BLE001
            return tool_error(PROMOTE_TOOL_NAME, exc)

    return _handler


# ---------------------------------------------------------------------------
# narration generate_step1 variant
# ---------------------------------------------------------------------------


def generate_episode_script_tool(ctx: ToolContext):
    @tool(
        "generate_episode_script",
        "调用项目配置的文本模型生成 JSON 剧本。dry_run=true 时仅返回 prompt。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "description": "剧集编号"},
                "instructions": _INSTRUCTIONS_SCHEMA,
                "dry_run": {"type": "boolean", "description": "仅显示 prompt，不调用模型"},
            },
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        request = ToolTextGenerationRequest(
            episode=int(args["episode"]),
            instructions=args.get("instructions"),
            dry_run=bool(args.get("dry_run")),
        )
        outcome = await run_generate_episode_script(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
        return tool_outcome_response("text_generation", outcome)

    return _handler


def generate_step1_tool(
    ctx: ToolContext,
):
    @tool(
        "generate_step1",
        "按项目创作类型生成结构化 step1：剧情分镜、旁白分镜或参考生视频单元。"
        "广告/短片项目无 step1。dry_run=true 时仅返回 prompt。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "description": "剧集编号"},
                "source": {"type": "string", "description": "可选的项目内源文件相对路径"},
                "instructions": _INSTRUCTIONS_SCHEMA,
                "dry_run": {"type": "boolean", "description": "仅显示 prompt，不调用模型"},
            },
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        request = ToolTextGenerationRequest(
            episode=int(args["episode"]),
            source=args.get("source"),
            instructions=args.get("instructions"),
            dry_run=bool(args.get("dry_run")),
        )
        outcome = await run_generate_step1(
            ToolRequest(request),
            ctx.scope,
            ctx.caller,
            tool_services(ctx),
        )
        return tool_outcome_response("text_generation", outcome)

    return _handler


def confirm_script_review_tool(ctx: ToolContext):
    @tool(
        "confirm_script_review",
        "确认本集 step1 结构化中间态，放行 step2 视觉生成。仅在用户已明确认可进入视觉生成时调用。",
        {
            "type": "object",
            "properties": {"episode": {"type": "integer", "description": "剧集编号"}},
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        outcome = await run_confirm_script_review(
            ToolRequest(int(args["episode"])),
            ctx.scope,
            ctx.caller,
            tool_services(ctx),
        )
        return tool_outcome_response("text_generation", outcome)

    return _handler


__all__ = [
    "get_video_capabilities_tool",
    "generate_episode_script_tool",
    "confirm_script_review_tool",
    "generate_step1_tool",
    "validate_and_promote_draft_tool",
]
