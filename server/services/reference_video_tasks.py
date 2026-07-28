"""参考生视频 executor。

Spec: docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md §5.2
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from lib.asset_types import ASSET_SPECS, BUCKET_KEY, SHEET_KEY
from lib.config.resolver import ConfigResolver, constrain_durations, get_provider_fallback
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.path_safety import safe_exists
from lib.prompt_builders import append_product_fidelity_tail, append_video_negative_tail
from lib.reference_video import assemble_shots_text, render_prompt_for_backend
from lib.reference_video.ad_units import (
    render_ad_unit_prompt,
    render_reference_legend,
    resolve_ad_unit_shots,
)
from lib.reference_video.duration_slots import DurationSlot, resolve_duration_slot
from lib.reference_video.errors import MissingReferenceError
from lib.script_editor import ScriptEditError
from lib.script_models import ReferenceResource, ad_script_total_duration
from lib.thumbnail import extract_video_thumbnail
from lib.version_manager import VersionManager
from server.services.generation_context import VideoLaneRequest, resolve_generation_context
from server.services.generation_tasks import (
    collect_product_references_for_names,
    get_project_manager,
)

logger = logging.getLogger(__name__)


async def _persist_effective_duration(task_id: str, duration_seconds: int) -> None:
    """把取档后实际申请的秒数写回 task payload，供 resume 路径读取（见调用点注释）。

    非致命路径：持久化失败只降级 resume 元数据精度，不影响本次已在进行的生成，
    故只记日志、不上抛阻断 executor 主流程。
    """
    from lib.generation_queue import get_generation_queue

    try:
        await get_generation_queue().persist_effective_duration(task_id, duration_seconds)
    except Exception:
        logger.warning("effective_duration 写回 payload 失败 task_id=%s", task_id, exc_info=True)


def _resolve_unit_references(
    project: dict,
    project_path: Path,
    references: list[dict],
) -> list[Path]:
    """把 unit.references 转成绝对路径列表（按 references 顺序）。

    Raises:
        MissingReferenceError: 任一 reference 在 project.json 对应 bucket 缺失或 sheet 不存在。
    """
    missing: list[tuple[str, str | None]] = []
    resolved: list[Path] = []
    for ref in references:
        rtype = ref.get("type")
        rname = ref.get("name")
        if rtype not in BUCKET_KEY:
            missing.append((str(rtype), str(rname)))
            continue
        bucket = project.get(BUCKET_KEY[rtype]) or {}
        item = bucket.get(rname)
        sheet_rel = item.get(SHEET_KEY[rtype]) if isinstance(item, dict) else None
        if not sheet_rel:
            missing.append((rtype, rname))
            continue
        path = project_path / sheet_rel
        if not path.exists():
            missing.append((rtype, rname))
            continue
        resolved.append(path)

    if missing:
        raise MissingReferenceError(missing=missing)
    return resolved


def _render_unit_prompt(unit: dict) -> str:
    """从 unit.shots[*].text 拼接 prompt，用 shot_parser 把 @X 替成 [图N]，再追加反向尾词。

    空提示词的*结构校验*已上移到入队守卫点（``TaskSpec.from_request``），两条入队路径
    （WebUI / SDK）在入队时即拒绝空提示词。此处保留一道防御性空检查，因为参考生视频的
    提示词源是*可变*的 script 文件且执行期从新读取（队列 dedup 不看 payload，无法靠入队
    快照兜底）：若提示词在入队后被改空、或在途遗留任务漏过守卫，这道检查避免空提示词被
    尾词追加成非空文本绕过 backend 的空值保护、白白消耗付费配额。
    """
    raw = assemble_shots_text(unit.get("shots") or [])
    references = [ReferenceResource(type=r["type"], name=r["name"]) for r in (unit.get("references") or [])]
    rendered = render_prompt_for_backend(raw, references)
    if not rendered.strip():
        raise ValueError("reference video unit prompt is empty: all shots[*].text are blank")
    return append_video_negative_tail(rendered)


def _apply_provider_constraints(
    *,
    provider: str,
    model: str | None,
    max_refs: int | None,
    supported_durations: Sequence[int],
    references: list[Path],
    duration_seconds: int,
    registry_provider_id: str = "",
    resolution: str | None = None,
) -> tuple[list[Path], int, list[dict]]:
    """按供应商能力取时长档位、裁剪 references；回传 warnings（i18n key + 参数）。

    `max_refs` / `supported_durations` 由调用方从 GenerationContext 的 video lane 取得
    （model 粒度，单一真相源）；`max_refs` 为 None 表示不裁参考图，`supported_durations`
    为空表示能力不可解析、时长原样透传。

    档位全集先按本次调用的条件收窄再取档（见 :func:`effective_reference_durations`）：参考图
    约束只在裁剪后**确实带图**时施加——通用单元允许空 references、ad 缺图会退化为纯文本，
    而 backend 同样只在 ``reference_images`` 非空时施加该约束。收窄用的是规范 registry
    provider id 而非 ``provider``（后者是 backend 族名，如 ark-agent-plan 族用 Ark backend，
    不是 registry key），与 ``supported_durations`` 的查询身份保持一致。

    时长按容量语义取档（见 :func:`resolve_duration_slot`）：申请能装下总时长的最小档位，
    成片不裁剪。取档偏移了剧本编排时记 warning——入队前的用户确认按项目当前配置近似
    判断，实际档位以此处执行时解析的 model 能力为准。
    """
    warnings: list[dict] = []

    new_refs = list(references)
    if max_refs is not None and len(references) > max_refs:
        new_refs = references[:max_refs]
        # Sora 单图走专门的 warning key，其他走通用
        if provider.lower() == "openai" and (model or "").lower().startswith("sora") and max_refs == 1:
            warnings.append({"key": "ref_sora_single_ref", "params": {}})
        else:
            warnings.append(
                {
                    "key": "ref_too_many_images",
                    "params": {
                        "count": len(references),
                        "model": model or provider,
                        "max_count": max_refs,
                    },
                }
            )

    slot = resolve_duration_slot(
        duration_seconds,
        effective_reference_durations(
            registry_provider_id,
            model,
            list(supported_durations),
            resolution,
            with_reference_images=bool(new_refs),
        ),
    )
    new_duration = slot.seconds
    slot_warning = slot.warning(model=model or provider)
    if slot_warning is not None:
        warnings.append(slot_warning)

    return new_refs, new_duration, warnings


def unit_script_duration(unit: dict, ad_shots: list[dict] | None) -> int:
    """unit 的剧本编排时长（秒）。ad 取成员镜头求和，narration/drama 取 unit 字段。

    执行层取档与入队前预检共用此口径（含缺值兜底 8 秒），避免用户确认的秒数与实际
    申请的秒数因两处各自兜底而不一致。
    """
    if ad_shots is not None:
        return ad_script_total_duration(ad_shots) or 8
    return int(unit.get("duration_seconds") or 8)


def effective_reference_durations(
    provider_id: str,
    model: str | None,
    durations: list[int],
    resolution: str | None,
    *,
    with_reference_images: bool,
) -> list[int]:
    """参考视频路径实际可申请的时长档位：全集与本次调用条件的约束求交。

    型号可能对「带参考图」与「按某分辨率下发」各自声明更窄的时长档位。按全集取档会选中
    执行期必然被拒的秒数（如 Veo 3.1 带参考图只接受 8 秒，5 秒剧本按全集取档得 6 秒），
    预检也会向用户展示这个申请不到的秒数。取档与预检因此都要先收窄再取档。

    ``with_reference_images`` 为 false 时不施加参考图约束：通用单元允许空 references、ad
    缺图会退化为纯文本，backend 同样只在 ``reference_images`` 非空时施加该约束——无图单元
    套用它会把 720p 下本可申请的 4 秒错误抬到 8 秒。

    ``provider_id`` 必须是规范 registry provider id（backend 族名不是 registry key）。两条约束
    都遵循「无声明或交集为空时不收窄」的降级口径（见 :func:`lib.config.resolver.constrain_durations`），
    故本函数在能力声明缺位时退化为原全集，不比收窄前更严。
    """
    return constrain_durations(
        provider_id, model, durations, resolution=resolution, uses_reference_images=with_reference_images
    )


@dataclass(frozen=True)
class ProjectDurationContext:
    """项目视频能力的一次性 IO 解析结果：档位全集（未按单个 unit 条件收窄）+ 分辨率 + provider/model 身份。

    由 :func:`resolve_project_duration_context` 产出，供 :func:`precheck_unit` 对批次内每个
    unit 反复调用而不重新触发 DB IO——批量预检 N 个 unit 时项目能力/分辨率解析各只发生一次。
    """

    supported_durations: tuple[int, ...]
    resolution: str | None
    provider_id: str
    model_name: str | None


async def resolve_project_duration_context(project: dict) -> ProjectDurationContext:
    """一次性解析项目视频能力（档位全集 + 分辨率 + provider/model 身份）。

    解析失败按空档位处理（沿用现状放行，不弹确认）；分辨率仅在档位非空时才解析，
    空档位下分辨率约束无意义。
    """
    caps = await _project_video_caps(project, degraded_to="时长取档不施加档位约束")
    durations = tuple(int(d) for d in caps.get("supported_durations") or [])
    provider_id = str(caps.get("provider_id") or "")
    model = caps.get("model")
    model_name = str(model) if model else None
    resolution = await _project_video_resolution(project, provider_id, model_name) if durations else None
    return ProjectDurationContext(
        supported_durations=durations,
        resolution=resolution,
        provider_id=provider_id,
        model_name=model_name,
    )


def precheck_unit(ctx: ProjectDurationContext, unit: dict, ad_shots: list[dict] | None) -> DurationSlot:
    """按已解析的项目能力 context 为单个 unit 取档。纯函数，无 IO——批量调用方一次
    :func:`resolve_project_duration_context` 后对每个 unit 反复调用，不重复 DB 往返。

    provider 按 ADR-0001 在执行时才解析，故 ``ctx`` 只是近似：实际档位以执行时的 model
    能力为准。

    「是否带参考图」按 unit 声明的 references 近似——执行层按解析后的实际图判定，而 ad 路径
    的资产缺图退化为纯文本要读盘才知道。近似方向保守：声明了参考却缺图的异常单元会多收窄
    一档、至多多问一次确认，不会漏掉需要确认的情形。
    """
    durations = (
        effective_reference_durations(
            ctx.provider_id,
            ctx.model_name,
            list(ctx.supported_durations),
            ctx.resolution,
            with_reference_images=bool(unit.get("references")),
        )
        if ctx.supported_durations
        else []
    )
    return resolve_duration_slot(unit_script_duration(unit, ad_shots), durations)


async def _project_video_caps(project: dict, *, degraded_to: str) -> dict:
    """项目视频后端的 model 粒度能力；解析失败返回空 dict，由调用方各自降级。

    ``degraded_to`` 只用于日志，说明这次解析失败会让调用方退化成什么行为。
    """
    try:
        resolver = ConfigResolver(async_session_factory)
        return await resolver.video_capabilities_for_project(project)
    except (ValueError, SQLAlchemyError) as exc:
        logger.info("无法解析 video_capabilities，%s：%s", degraded_to, exc)
        return {}


async def _project_video_resolution(project: dict, provider_id: str, model_id: str | None) -> str | None:
    """项目视频后端实际下发的分辨率；解析失败返回 None（该条约束随之不收窄）。

    未显式配置时取 provider fallback，与执行层的 ``resolution_or_fallback`` 同源：预检若在
    这里停在 None，就会漏掉「按 fallback 分辨率才生效」的档位约束——Veo 未配分辨率时执行层
    按 1080p 下发、只接受 8 秒，预检却按全集判 6 秒为档位成员而不弹确认，生成出来的成片比
    剧本长且用户从未被问过。
    """
    if not provider_id or not model_id:
        return None
    try:
        resolution = await ConfigResolver(async_session_factory).resolve_resolution(project, provider_id, model_id)
    except (ValueError, SQLAlchemyError) as exc:
        logger.info("无法解析 video resolution，时长取档不施加分辨率约束：%s", exc)
        return None
    return resolution or get_provider_fallback(provider_id)


async def resolve_max_unit_duration(project: dict) -> int | None:
    """解析项目视频后端的单次生成时长上限（秒），供派生分组约束 unit 总长。

    单一真相源与 executor 取档同口径（``video_capabilities_for_project`` 的
    model 粒度 ``max_duration``）；解析失败返回 None——分组退化为仅按镜头数
    切分，超长 unit 交由执行层取档 + warning 兜底，不阻塞派生。
    """
    caps = await _project_video_caps(project, degraded_to="派生分组不施加时长上限")
    max_duration = caps.get("max_duration")
    return int(max_duration) if max_duration else None


def _resolve_ad_unit_reference_entries(
    project: dict,
    project_path: Path,
    references: list[dict],
) -> tuple[list[dict], list[dict]]:
    """ad 派生 unit 的参考解析：返回 ``(entries, warnings)``。

    与 narration/drama 的 ``_resolve_unit_references``（缺图硬失败）不同，ad 的
    参考集由分组器从镜头字段自动继承而非人工挑选，缺图按软口径跳过并记
    warning——与 ad 分镜路径「保真注入退化为纯文本」的既有口径一致，不让一张
    缺失的场景 sheet 阻塞整个 unit 出片。

    产品沿用注入二元规则：经 ``collect_product_references_for_names`` 全量装配
    （sheet 在前、原图压阵）且排在所有其它参考之前；character/scene/prop 注入
    各自 sheet。条目形如 ``{"image": Path, "label": str, "name": str, "kind":
    "sheet"|"original"|"asset"}``，label 供 [图N] 对照表渲染。
    """
    warnings: list[dict] = []
    product_names: list[str] = []
    asset_refs: list[tuple[str, str]] = []
    for ref in references:
        if not isinstance(ref, dict):
            continue
        rtype = ref.get("type")
        rname = ref.get("name")
        if not isinstance(rname, str) or not rname:
            continue
        if rtype == "product":
            if rname not in product_names:
                product_names.append(rname)
        elif rtype in BUCKET_KEY:
            asset_refs.append((str(rtype), rname))

    entries = collect_product_references_for_names(project, project_path, product_names)
    injected_products = {e["name"] for e in entries}
    for name in product_names:
        if name not in injected_products:
            warnings.append({"key": "ref_ad_reference_skipped", "params": {"type": "product", "name": name}})

    for rtype, rname in asset_refs:
        raw_bucket = project.get(BUCKET_KEY[rtype])
        bucket = raw_bucket if isinstance(raw_bucket, dict) else {}
        item = bucket.get(rname)
        sheet_rel = item.get(SHEET_KEY[rtype]) if isinstance(item, dict) else None
        if sheet_rel and safe_exists(project_path, sheet_rel):
            entries.append(
                {
                    "image": project_path / sheet_rel,
                    "label": f"{ASSET_SPECS[rtype].label_zh}「{rname}」设计图",
                    "name": rname,
                    "kind": "asset",
                }
            )
        else:
            warnings.append({"key": "ref_ad_reference_skipped", "params": {"type": rtype, "name": rname}})
    return entries, warnings


def _clamp_ad_reference_entries(
    entries: list[dict],
    max_refs: int | None,
    *,
    provider: str,
    model: str | None,
) -> tuple[list[dict], list[dict]]:
    """超出后端参考张数上限时裁剪 ad 参考条目；产品参考的 sheet 跨产品稳定前置。

    与视频层二次注入的截断口径一致（每个产品的锚定 sheet 优先存活）；
    非产品参考排在产品之后，截断自然先牺牲它们。``max_refs == 0``（模型不支持
    参考图）裁到空集，与通用路径 ``_apply_provider_constraints`` 的 None 判定同口径。
    """
    if max_refs is None or len(entries) <= max_refs:
        return list(entries), []
    products = [e for e in entries if e.get("kind") in ("sheet", "original")]
    others = [e for e in entries if e.get("kind") not in ("sheet", "original")]
    products = sorted(products, key=lambda e: 0 if e.get("kind") == "sheet" else 1)
    clamped = (products + others)[:max_refs]
    warning = {
        "key": "ref_too_many_images",
        "params": {"count": len(entries), "model": model or provider, "max_count": max_refs},
    }
    return clamped, [warning]


def _render_ad_unit_prompt_for_backend(shots: list[dict], entries: list[dict], *, style: object) -> str:
    """ad 派生 unit 的最终 backend prompt：镜头文本 + [图N] 对照表 + 保真/反向尾词。

    对照表必须基于裁剪后的 ``entries`` 渲染（[图N] 与 backend 实收顺序对齐）；
    高保真指令只点名实际注入了参考的产品。空提示词防御口径同
    ``_render_unit_prompt``（提示词源是可变 script，执行期从新读取）。
    """
    body = render_ad_unit_prompt(shots, style=style if isinstance(style, str) else None)
    if not body.strip():
        raise ValueError("reference video unit prompt is empty: all member shots have no visual content")
    legend = render_reference_legend([str(e.get("label") or "") for e in entries])
    prompt = f"{body}\n\n{legend}" if legend else body
    product_names = list(dict.fromkeys(e["name"] for e in entries if e.get("kind") in ("sheet", "original")))
    prompt = append_product_fidelity_tail(prompt, product_names)
    return append_video_negative_tail(prompt)


async def execute_reference_video_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """处理一个 reference_video unit 的生成。

    resource_id 即 unit_id（E{集}U{序号}）。narration/drama 的 unit 来自
    ``video_units``（内容自包含）；ad 剧本骨架唯一，unit 来自 ``reference_units``
    轻量索引，成员镜头执行期从 shots（内容唯一真相）水合。
    """
    script_file = payload.get("script_file")
    if not script_file:
        raise ValueError("script_file is required for reference_video task")

    # 1. 加载上下文（阻塞 IO，线程池）
    def _load():
        pm = get_project_manager()
        project = pm.load_project(project_name)
        project_path = pm.get_project_path(project_name)
        script = pm.load_script(project_name, script_file)
        is_ad = project.get("content_mode") == "ad"
        units = (script.get("reference_units") if is_ad else script.get("video_units")) or []
        unit = next((u for u in units if isinstance(u, dict) and u.get("unit_id") == resource_id), None)
        if unit is None:
            raise ValueError(f"unit not found: {resource_id}")
        # 索引悬空（镜头被删/改 ID 后未重新派生）在此 fail-loud，提示重新派生
        ad_shots = resolve_ad_unit_shots(script, unit) if is_ad else None
        return project, project_path, unit, ad_shots

    project, project_path, unit, ad_shots = await asyncio.to_thread(_load)
    is_ad = ad_shots is not None

    # 2. 解析 references（narration/drama 缺图直接失败；ad 软口径跳过 + warning）
    ad_entries: list[dict] = []
    ad_warnings: list[dict] = []
    if is_ad:
        ad_entries, ad_warnings = _resolve_ad_unit_reference_entries(
            project, project_path, unit.get("references") or []
        )
        source_refs = [e["image"] for e in ad_entries]
    else:
        source_refs = _resolve_unit_references(project, project_path, unit.get("references") or [])

    # 3. 单次解析生成上下文（声明 video lane）：构造 generator 并按实际 backend 身份
    #    查能力上限与 resolution。provider 身份解析收口于 GenerationContext
    #    （docs/adr/0049）——executor 不再触碰 MediaGenerator 私有属性、不再手工重建
    #    registry provider_id。
    ctx = await resolve_generation_context(
        project_name, payload, project=project, user_id=user_id, video=VideoLaneRequest()
    )
    generator = ctx.generator
    video = ctx.video
    provider_name = video.backend_name
    model_name = video.backend_model

    # 4. model 粒度能力上限（单一真相源：model.supported_durations）。能力按实际 backend
    #    身份（provider_id + backend.model）查得：自定义模型禁用回退时也直接命中活跃 model
    #    的能力，不再出现"按旧模型裁剪、按新模型生成"的错位，原 caps.model 一致性防御分支
    #    随之消解。查询失败时 lane 已把能力降级为空值/None——守卫遇空集放行、clamp 不施加
    #    上限，把决策推给 backend，与 ScriptGenerator._fetch_video_capabilities 的口径一致。
    max_refs = video.max_reference_images

    supported_durations = list(video.supported_durations)

    # 参考视频是唯一需要非空 resolution 档位的调用方：lane 已按 registry provider_id
    # 兜底（resolution 命中空档位时取 provider fallback），executor 直接取非空档位。
    resolution = video.resolution_or_fallback

    # 条件档位收窄读 registry 声明，查询键必须是规范 registry provider_id：backend_name 是
    # 族名（ark-agent-plan 族复用 Ark backend），拿它查表会静默落空、收窄失效。
    registry_provider_id = video.provider_model.provider_id

    # 5. Provider 特判：裁 refs + 取时长档位。ad 的参考裁剪走专用口径（产品 sheet
    #    跨产品稳定前置存活），时长取档与通用路径共用。
    base_duration = unit_script_duration(unit, ad_shots)
    if is_ad:
        ad_entries, clamp_warnings = _clamp_ad_reference_entries(
            ad_entries, max_refs, provider=provider_name, model=model_name
        )
        constrained_refs, effective_duration, duration_warnings = _apply_provider_constraints(
            provider=provider_name,
            model=model_name,
            max_refs=None,
            supported_durations=supported_durations,
            references=[e["image"] for e in ad_entries],
            duration_seconds=base_duration,
            registry_provider_id=registry_provider_id,
            resolution=resolution,
        )
        warnings = [*ad_warnings, *clamp_warnings, *duration_warnings]
    else:
        constrained_refs, effective_duration, warnings = _apply_provider_constraints(
            provider=provider_name,
            model=model_name,
            max_refs=max_refs,
            supported_durations=supported_durations,
            references=source_refs,
            duration_seconds=base_duration,
            registry_provider_id=registry_provider_id,
            resolution=resolution,
        )

    # 取档偏移了剧本编排（adjustment != exact）时，把实际申请的秒数写回 task payload：
    # resume 路径（server.services.resume_executor）读 payload["duration_seconds"] 重建
    # 申请参数，不写回会在重启续传时按剧本原值而非本次实际申请的秒数记录版本元数据。
    # 未偏移时两值相等，写回无意义，跳过省一次 DB 往返。持久化失败只降级为 resume 元数据
    # 不够精确（仍回退到剧本原值，与本次改动前行为一致），不影响本次生成结果，不 fail-fast。
    if task_id is not None and effective_duration != base_duration:
        await _persist_effective_duration(task_id, effective_duration)

    # 6. 渲染 prompt。ad：镜头文本 + 裁剪后参考的 [图N] 对照表 + 保真/反向尾词。
    #    narration/drama：@→[图N] 替换——必须按 `constrained_refs` 的长度裁
    #    `unit.references` 再渲染，保证 [图N] 的 1-based 索引与 backend 实际收到的
    #    reference_images 长度严格对齐；否则裁剪后的 `@clipped_name` 会被替成
    #    `[图N]` 指向不存在的图。
    #    prompt 始终从执行期新读取的剧本重组（脚本可变 + 队列 dedup 不看 payload，
    #    用入队快照会丢失入队后对镜头文本的编辑）；入队 payload 里的 prompt 仅作守卫点的
    #    校验记录，执行期不使用。
    if is_ad:
        rendered_prompt = _render_ad_unit_prompt_for_backend(ad_shots or [], ad_entries, style=project.get("style"))
    else:
        unit_for_prompt = unit
        unit_refs = unit.get("references") or []
        if len(constrained_refs) < len(unit_refs):
            unit_for_prompt = {**unit, "references": unit_refs[: len(constrained_refs)]}
        rendered_prompt = _render_unit_prompt(unit_for_prompt)

    # 7. 直接把源路径交给咽喉层 generate_video_async —— 参考上传副本的压缩、降档梯子与 413 兜底
    #    统一由 MediaGenerator 负责（发完即删的临时字节），此处不再预压缩、不再管理临时文件，
    #    避免双压。数量裁剪 + [图N] 索引对齐已在上游完成，咽喉层压缩 1:1 保序保数，职责不重叠。
    output_path, version, _, video_uri = await generator.generate_video_async(
        prompt=rendered_prompt,
        resource_type="reference_videos",
        resource_id=resource_id,
        reference_images=constrained_refs,
        aspect_ratio=project.get("aspect_ratio", "9:16"),
        duration_seconds=effective_duration,
        resolution=resolution,
        task_id=task_id,
    )

    return await _finalize_reference_video_unit(
        project_name=project_name,
        script_file=script_file,
        project_path=project_path,
        resource_id=resource_id,
        output_path=output_path,
        version=version,
        video_uri=video_uri,
        versions=generator.versions,
        warnings=warnings,
    )


def apply_unit_video_assets(script: dict, resource_id: str, *, video_uri: str | None, thumb_rel: str | None) -> None:
    """在剧本 dict 上写回 unit.generated_assets（video_clip / video_uri / video_thumbnail / status）。

    生成 finalize 与版本还原共用，保证两条路径写出的字段口径一致。unit 在
    narration/drama 剧本中位于 ``video_units``、在 ad 剧本中位于 ``reference_units``
    派生索引——两处都查（同剧本不会同时有两类合法 unit 列表，shots 才是 ad 的
    内容真相）。新结果不含 video_uri / 缩略图时清空旧值，避免指向过期 URI /
    已删除文件。写回失败必须让调用方可见、finalize 不能在剧本未更新时静默成功，
    且两种失败要可区分：unit 不存在抛 KeyError（还原侧跨集同步把它当正常跳过），
    unit 列表结构损坏抛 ScriptEditError（还原侧按脏脚本 warning 降级）。
    """
    unit_lists = [script.get(key) for key in ("video_units", "reference_units")]
    candidates = [units for units in unit_lists if isinstance(units, list)]
    if not candidates:
        raise ScriptEditError("video_units / reference_units 必须是 list", key="script_edit_unit_lists_invalid")
    for units in candidates:
        for u in units:
            if not isinstance(u, dict) or u.get("unit_id") != resource_id:
                continue
            ga = u.setdefault("generated_assets", {})
            if not isinstance(ga, dict):
                raise ScriptEditError("generated_assets 必须是 dict", key="script_edit_generated_assets_invalid")
            ga["video_clip"] = f"reference_videos/{resource_id}.mp4"
            if video_uri:
                ga["video_uri"] = video_uri
            else:
                ga.pop("video_uri", None)
            if thumb_rel:
                ga["video_thumbnail"] = thumb_rel
            else:
                ga.pop("video_thumbnail", None)
            ga["status"] = "completed"
            return
    raise KeyError(resource_id)


async def _finalize_reference_video_unit(
    *,
    project_name: str,
    script_file: str,
    project_path: Path,
    resource_id: str,
    output_path: Path,
    version: int,
    video_uri: str | None,
    versions: VersionManager,
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normal + resume 共用：抽缩略图、写 unit.generated_assets、返回 result dict。"""
    warnings = warnings if warnings is not None else []

    thumb_dir = project_path / "reference_videos" / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"{resource_id}.jpg"
    if await extract_video_thumbnail(output_path, thumb_path):
        thumb_rel: str | None = f"reference_videos/thumbnails/{resource_id}.jpg"
    else:
        thumb_path.unlink(missing_ok=True)
        thumb_rel = None

    def _update_unit_assets():
        pm = get_project_manager()
        # 资产回写热路径：只动 unit.generated_assets，结构不可能因此变坏，豁免结构校验。
        with pm.locked_script(project_name, script_file, validate=False) as script:
            apply_unit_video_assets(script, resource_id, video_uri=video_uri, thumb_rel=thumb_rel)

    await asyncio.to_thread(_update_unit_assets)

    def _latest_created_at() -> str | None:
        history = versions.get_versions("reference_videos", resource_id) or {}
        records = history.get("versions") or []
        if not records:
            return None
        return records[-1].get("created_at")

    created_at = await asyncio.to_thread(_latest_created_at)

    return {
        "version": version,
        "file_path": f"reference_videos/{resource_id}.mp4",
        "created_at": created_at,
        "resource_type": "reference_videos",
        "resource_id": resource_id,
        "video_uri": video_uri,
        "warnings": warnings,
    }
