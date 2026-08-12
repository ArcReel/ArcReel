"""参考生视频 executor。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from lib.asset_types import BUCKET_KEY, SHEET_KEY, asset_name_comparison_key, normalize_asset_bucket
from lib.config.resolver import (
    ConfigResolver,
    ProviderModel,
    VideoCapability,
    constrain_durations,
    get_provider_fallback,
)
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.generation_queue import get_generation_queue
from lib.prompt_builders import append_product_fidelity_tail
from lib.reference_video import assemble_shots_text, assemble_shots_text_for_render
from lib.reference_video.duration_slots import DurationSlot, resolve_duration_slot
from lib.reference_video.errors import MissingReferenceError
from lib.reference_video.prompt_render import (
    RenderedUnitPrompt,
    render_unit_prompt,
    resolve_reference_audio_paths,
)
from lib.reference_video.units import reference_video_bucket
from lib.reference_video.voice_settings import VoiceRenderSettings
from lib.script_editor import ScriptEditError
from lib.script_models import ReferenceResource
from lib.speech_composition import video_unit_replan_problems
from lib.thumbnail import extract_video_thumbnail
from lib.version_manager import VersionManager
from server.services.generation_context import VideoLaneRequest, resolve_generation_context
from server.services.generation_tasks import collect_product_references_for_names, get_project_manager
from server.services.video_caps import project_video_caps

logger = logging.getLogger(__name__)


async def _persist_effective_duration(task_id: str, duration_seconds: int) -> None:
    """把取档后实际申请的秒数写回 task payload，供 resume 路径读取（见调用点注释）。

    非致命路径：持久化失败只降级 resume 元数据精度，不影响已在进行的生成，
    故只记日志、不上抛阻断 executor 主流程。
    """
    try:
        await get_generation_queue().persist_effective_duration(task_id, duration_seconds)
    except Exception:
        logger.warning("effective_duration 写回 payload 失败 task_id=%s", task_id, exc_info=True)


async def _persist_execution_identity(
    task_id: str, execution_model: ProviderModel, capability: VideoCapability
) -> None:
    """把执行期实际解析出的身份写回 ``task.provider_id`` 列与 payload 锁定键（见调用点注释）。

    fail-fast：写回失败上抛、任务在向 provider 提交前失败。吞掉异常继续提交会留下
    「身份停留在入队投影、job_id 却已持久化」的任务——重启恢复拿错 backend
    轮询，与 ``persist_provider_job_id`` 防「幽灵任务」是同一语义（ADR 0007）；此处
    失败发生在提交前，没有已计费的 provider 端 job 可丢。
    """
    await get_generation_queue().persist_execution_identity(
        task_id, execution_model=execution_model, capability=capability
    )


def _dedupe_typed_references(references: list[dict]) -> list[dict]:
    """按 (type, 归一名) 去重 reference 字典，保留首次出现顺序。

    PATCH 接口只校验每条 reference 已登记，不校验数组内互相去重：同一资产可能以 NFC/NFD
    两条等价记录同时留在 unit.references 里。参考图解析（``_resolve_unit_references``）与
    prompt 渲染前的裁剪必须共用同一份去重结果——否则图片列表与逻辑引用列表长度不一致，
    ``@图片N`` 的编号会与实际图片错位、按图号绑定的参考音频也会挂到错的图上。
    """
    priority = {"product": 0, "character": 1, "scene": 2, "prop": 3}
    ordered = sorted(
        enumerate(references),
        key=lambda pair: (priority.get(str(pair[1].get("type")), len(priority)), pair[0]),
    )
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for _index, ref in ordered:
        key = (str(ref.get("type")), asset_name_comparison_key(str(ref.get("name"))))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


@dataclass(frozen=True)
class ResolvedReferenceImage:
    """一张实际随视频请求发出的图片及其逻辑主体。

    一个产品引用可展开成标准 sheet 与多张实拍原图，故逻辑 reference 与请求图片不再是
    一一对应。``reference`` 供 prompt 按同一顺序生成 ``@图片N`` 绑定，``kind`` 供超过
    供应商上限时让各产品的标准 sheet 跨产品优先存活。
    """

    path: Path
    reference: ReferenceResource
    kind: str


def _resolve_unit_reference_entries(
    project: dict,
    project_path: Path,
    references: list[dict],
) -> list[ResolvedReferenceImage]:
    """把逻辑引用展开成请求图片条目；产品使用 sheet + 原图保真注入规则。

    逻辑引用先按 product → character → scene → prop 稳定排序并按类型+归一名去重。产品
    sheet 与原图均不可用、或其它资产缺少可用 sheet 时统一 fail loud：``video_units`` 的
    references 是自包含的显式执行契约，不应静默把有参考的单元降成纯文本生成。
    """
    deduped = _dedupe_typed_references(references)
    product_refs = [ref for ref in deduped if ref.get("type") == "product"]
    product_names = [str(ref.get("name")) for ref in product_refs]
    raw_product_entries = collect_product_references_for_names(project, project_path, product_names)

    entries = [
        ResolvedReferenceImage(
            path=entry["image"],
            reference=ReferenceResource(type="product", name=entry["name"]),
            kind=str(entry["kind"]),
        )
        for entry in raw_product_entries
    ]
    injected_products = {asset_name_comparison_key(entry.reference.name) for entry in entries}
    missing: list[tuple[str, str | None]] = [
        ("product", str(ref.get("name")))
        for ref in product_refs
        if asset_name_comparison_key(str(ref.get("name"))) not in injected_products
    ]

    for ref in deduped:
        rtype = ref.get("type")
        rname = ref.get("name")
        if rtype == "product":
            continue
        if rtype not in BUCKET_KEY:
            missing.append((str(rtype), str(rname)))
            continue
        bucket = normalize_asset_bucket(project.get(BUCKET_KEY[rtype]))
        canonical = asset_name_comparison_key(str(rname))
        item = bucket.get(canonical)
        sheet_rel = item.get(SHEET_KEY[rtype]) if isinstance(item, dict) else None
        if not sheet_rel:
            missing.append((rtype, str(rname)))
            continue
        path = project_path / sheet_rel
        if not path.exists():
            missing.append((rtype, str(rname)))
            continue
        entries.append(
            ResolvedReferenceImage(
                path=path,
                reference=ReferenceResource(type=rtype, name=canonical),
                kind="asset",
            )
        )

    if missing:
        raise MissingReferenceError(missing=missing)
    return entries


def _resolve_unit_references(
    project: dict,
    project_path: Path,
    references: list[dict],
) -> list[Path]:
    """兼容路径列表调用方；产品逻辑引用会展开成 sheet + 原图。

    Raises:
        MissingReferenceError: 任一 reference 没有可用参考图片。
    """
    return [entry.path for entry in _resolve_unit_reference_entries(project, project_path, references)]


def _render_unit_prompt(
    unit: dict,
    project: dict,
    settings: VoiceRenderSettings,
    *,
    request_references: list[ReferenceResource] | None = None,
) -> RenderedUnitPrompt:
    """把 unit 的书写文稿渲染成三段论 backend prompt（见 ``lib.reference_video.prompt_render``）。

    画质/字幕/水印约束由渲染的第三段承担，本路径不追加反向尾词；
    ``append_video_negative_tail`` 只服务图生视频路径。

    空提示词的*结构校验*已上移到入队守卫点（``TaskSpec.from_request``），两条入队路径
    （WebUI / SDK）在入队时即拒绝空提示词。此处保留一道防御性空检查，因为参考生视频的
    提示词源是*可变*的 script 文件且执行期从新读取（队列 dedup 不看 payload，无法靠入队
    快照兜底）：若提示词在入队后被改空、或在途遗留任务漏过守卫，这道检查避免空提示词被
    机器生成的第一/三段撑成非空文本绕过 backend 的空值保护、白白消耗付费配额。检查落在
    *书写层文本*而非渲染结果上——三段论渲染恒产出非空第三段，渲染后已无从判别。

    空检查用 ``assemble_shots_text``（不注入 header，空 shots 拼接结果仍为空）；渲染用
    ``assemble_shots_text_for_render``（按数组位置重新注入规范 header），因为
    ``parse_prompt`` 只认文本里的 header 切分镜头，而经解析预览面板编辑回写的 unit，
    其 ``shots[*].text`` 普遍已不带 header——两个以上镜头裸拼接后会被重新解析成一个镜头，
    丢失第二段的分镜结构。
    """
    shots = unit.get("shots") or []
    if not assemble_shots_text(shots).strip():
        raise ValueError("reference video unit prompt is empty: all shots[*].text are blank")
    references = request_references
    if references is None:
        references = [ReferenceResource(type=r["type"], name=r["name"]) for r in (unit.get("references") or [])]
    rendered = render_unit_prompt(
        assemble_shots_text_for_render(shots),
        project,
        references,
        settings,
        style=project.get("style"),
    )
    product_names = list(dict.fromkeys(ref.name for ref in references if ref.type == "product"))
    return replace(rendered, prompt=append_product_fidelity_tail(rendered.prompt, product_names))


def _reference_limit_warning(*, provider: str, model: str | None, count: int, max_refs: int) -> dict[str, Any]:
    """参考图片超限 warning；通用路径与产品优先裁剪共用。"""
    if provider.lower() == "openai" and (model or "").lower().startswith("sora") and max_refs == 1:
        return {"key": "ref_sora_single_ref", "params": {}}
    return {
        "key": "ref_too_many_images",
        "params": {"count": count, "model": model or provider, "max_count": max_refs},
    }


def _clamp_resolved_reference_images(
    entries: list[ResolvedReferenceImage],
    max_refs: int | None,
    *,
    provider: str,
    model: str | None,
) -> tuple[list[ResolvedReferenceImage], list[dict[str, Any]]]:
    """按请求上限裁图片，并让所有产品 sheet 优先于产品原图及其它资产。

    未超限时保留原始稳定顺序；只有必须裁剪时才重排，避免在容量足够时无谓改变同一产品
    sheet 与原图的邻接顺序。``max_refs == 0`` 表示模型不支持参考图，返回空集。
    """
    if max_refs is None or len(entries) <= max_refs:
        return list(entries), []
    ordered = sorted(
        enumerate(entries),
        key=lambda pair: (
            0
            if pair[1].reference.type == "product" and pair[1].kind == "sheet"
            else 1
            if pair[1].reference.type == "product" and pair[1].kind == "original"
            else 2,
            pair[0],
        ),
    )
    clamped = [entry for _index, entry in ordered[:max_refs]]
    return clamped, [_reference_limit_warning(provider=provider, model=model, count=len(entries), max_refs=max_refs)]


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

    档位全集先按该请求的条件收窄再取档（见 :func:`effective_reference_durations`）：参考图
    约束只在裁剪后**确实带图**时施加——单元可以声明空 references，而 backend 同样只在
    ``reference_images`` 非空时施加该约束。收窄用的是规范 registry
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
        warnings.append(
            _reference_limit_warning(provider=provider, model=model, count=len(references), max_refs=max_refs)
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


#: unit 时长缺值时的兜底秒数。执行层取档、入队前预检与新建 unit 的默认时长共用此口径，
#: 避免用户确认的秒数与实际申请的秒数因各处各自兜底而不一致。
FALLBACK_UNIT_DURATION = 8


def unit_script_duration(unit: dict) -> int:
    """unit 的剧本编排时长（秒）。"""
    return int(unit.get("duration_seconds") or FALLBACK_UNIT_DURATION)


def effective_reference_durations(
    provider_id: str,
    model: str | None,
    durations: list[int],
    resolution: str | None,
    *,
    with_reference_images: bool,
) -> list[int]:
    """参考视频路径实际可申请的时长档位：全集与该请求条件的约束求交。

    型号可能对「带参考图」与「按某分辨率下发」各自声明更窄的时长档位。按全集取档会选中
    执行期必然被拒的秒数（如 Veo 3.1 带参考图只接受 8 秒，5 秒剧本按全集取档得 6 秒），
    预检也会向用户展示这个申请不到的秒数。取档与预检因此都要先收窄再取档。

    ``with_reference_images`` 为 false 时不施加参考图约束：单元可以声明空 references，
    backend 同样只在 ``reference_images`` 非空时施加该约束——无图单元
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
    max_duration: int | None = None


async def resolve_project_duration_context(
    project: dict,
    *,
    capability: VideoCapability | None = None,
) -> ProjectDurationContext:
    """一次性解析视频能力（档位全集 + 单次生成时长上限 + 分辨率 + provider/model 身份）。

    ``capability`` 未给定时按项目路线定桶；给定时按指定桶解析——参考路线内按镜头分流的
    调用方（费用估算、逐 unit 预检）以此对无参考图退化镜头按 i2v 桶模型取档。

    解析失败按空档位处理（沿用现状放行，不弹确认）；分辨率仅在档位非空时才解析，
    空档位下分辨率约束无意义。``max_duration`` 与 :func:`resolve_max_unit_duration`
    取自同一份能力解析结果，供需要现推分组的调用方复用而不再触发一次 IO。
    """
    caps = await project_video_caps(project, degraded_to="时长取档不施加档位约束", capability=capability)
    durations = tuple(int(d) for d in caps.get("supported_durations") or [])
    provider_id = str(caps.get("provider_id") or "")
    model = caps.get("model")
    model_name = str(model) if model else None
    max_duration = caps.get("max_duration")
    resolution = await _project_video_resolution(project, provider_id, model_name) if durations else None
    return ProjectDurationContext(
        supported_durations=durations,
        resolution=resolution,
        provider_id=provider_id,
        model_name=model_name,
        max_duration=int(max_duration) if max_duration else None,
    )


def precheck_unit(ctx: ProjectDurationContext, unit: dict) -> DurationSlot:
    """按已解析的项目能力 context 为单个 unit 取档。纯函数，无 IO——批量调用方一次
    :func:`resolve_project_duration_context` 后对每个 unit 反复调用，不重复 DB 往返。

    provider 按 ADR-0001 在执行时才解析，故 ``ctx`` 只是近似：实际档位以执行时的 model
    能力为准。

    「是否带参考图」按 unit 声明的 references 近似；执行层仍按实际解析到的图片判定。
    """
    with_reference_images = bool(unit.get("references"))
    durations = (
        effective_reference_durations(
            ctx.provider_id,
            ctx.model_name,
            list(ctx.supported_durations),
            ctx.resolution,
            with_reference_images=with_reference_images,
        )
        if ctx.supported_durations
        else []
    )
    return resolve_duration_slot(unit_script_duration(unit), durations)


def default_unit_duration(ctx: ProjectDurationContext, project: dict, *, with_references: bool = False) -> int:
    """新建 unit 的默认时长（秒）：项目偏好 > 收窄后的最短档位 > 兜底。

    档位按执行层同一套约束收窄（``effective_reference_durations``），使新建单元拿到的秒数
    落在它真正被生成时能申请到的档位内。``with_references`` 须与 ``precheck_unit`` 对同一
    unit 的判据同源（是否带参考图）。项目偏好不是当前模型的档位成员时（换模型后配置漂移）
    不采信，退到收窄后档位里的最短值（自定义供应商声明的档位可能不按升序排列）；档位不可
    解析时无从校验偏好是否可申请，直接退到 ``FALLBACK_UNIT_DURATION``，与执行层读不到
    unit 时长时的兜底值同源。
    """
    durations = effective_reference_durations(
        ctx.provider_id,
        ctx.model_name,
        list(ctx.supported_durations),
        ctx.resolution,
        with_reference_images=with_references,
    )
    if not durations:
        return FALLBACK_UNIT_DURATION
    preferred = project.get("default_duration")
    if isinstance(preferred, int) and not isinstance(preferred, bool) and preferred in durations:
        return preferred
    return min(durations)


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


def _build_reference_audio_wiring(
    rendered: RenderedUnitPrompt,
    audio_paths: dict[str, Path],
    *,
    reference_audio_per_image: bool,
) -> tuple[list[Path], list[int] | None]:
    """把渲染产物的 ``audio_speakers``（+ 图号对齐下标）组装成请求字段，narration/drama 与 ad
    共用同一份组装口径。

    ``reference_audio_per_image`` 为 True 时（backend 要求音频逐段挂在具体参考素材项上），
    渲染层已按 ``VoiceRenderSettings.requires_reference_image`` 门控排除无参考图的 speaker，
    ``audio_speaker_reference_index`` 此时不应再有 None——仍按下标同时过滤两个列表而非直接
    zip 全量，是为了在渲染层与本处口径将来漂移时，两个列表始终等长同序，不会因为其中一个
    多出未过滤的 None 项而导致音频与参考图下标错位、静默绑错角色。
    """
    if reference_audio_per_image:
        paired = [
            (audio_paths[name], idx)
            for name, idx in zip(rendered.audio_speakers, rendered.audio_speaker_reference_index, strict=True)
            if idx is not None
        ]
        return [path for path, _ in paired], [idx for _, idx in paired]
    return [audio_paths[name] for name in rendered.audio_speakers], None


async def execute_reference_video_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
    task_id: str | None = None,
) -> dict[str, Any]:
    """处理一个 reference_video unit 的生成。

    resource_id 即 unit_id（E{集}U{序号}）；所有内容模式都从自包含 ``video_units`` 读取。
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
        units = script.get("video_units") or []
        unit = next((u for u in units if isinstance(u, dict) and u.get("unit_id") == resource_id), None)
        if unit is None:
            raise ValueError(f"unit not found: {resource_id}")
        if video_unit_replan_problems(unit):
            raise ValueError(f"unit needs replanning: {resource_id}")
        return project, project_path, unit

    project, project_path, unit = await asyncio.to_thread(_load)

    # 2. 产品优先后解析 references；任一声明引用缺图即 fail-loud。产品引用按保真注入规则
    #    展开成标准 sheet + 全量原图，请求图片与逻辑主体一并保留，供裁剪和 prompt 共享。
    source_entries = _resolve_unit_reference_entries(project, project_path, unit.get("references") or [])
    source_refs = [entry.path for entry in source_entries]

    # 3. 单次解析生成上下文（声明 video lane）：构造 generator 并按实际 backend 身份
    #    查能力上限与 resolution。provider 身份解析收口于 GenerationContext
    #    （docs/adr/0049）——executor 不再触碰 MediaGenerator 私有属性、不再手工重建
    #    registry provider_id。桶按本 unit 解析后的实际参考图分流（docs/adr/0054）：
    #    有参考图 → r2v；无参考图的退化镜头降级 → i2v，不送入拒空参考的 r2v 桶模型。
    #    判据取解析结果而非声明，与 backend 的实际请求同源。
    execution_capability = reference_video_bucket(with_references=bool(source_refs))
    ctx = await resolve_generation_context(
        project_name,
        payload,
        project=project,
        user_id=user_id,
        video=VideoLaneRequest(capability=execution_capability),
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

    # 5. 图片超过后端上限时，让各产品的标准 sheet 跨产品优先存活，再保留产品原图与其它
    #    资产。随后仅取时长档位，不让通用路径按路径列表二次裁剪而丢失条目/主体对齐。
    base_duration = unit_script_duration(unit)
    constrained_entries, clamp_warnings = _clamp_resolved_reference_images(
        source_entries,
        max_refs,
        provider=provider_name,
        model=model_name,
    )
    constrained_refs, effective_duration, duration_warnings = _apply_provider_constraints(
        provider=provider_name,
        model=model_name,
        max_refs=None,
        supported_durations=supported_durations,
        references=[entry.path for entry in constrained_entries],
        duration_seconds=base_duration,
        registry_provider_id=registry_provider_id,
        resolution=resolution,
    )
    warnings = [*clamp_warnings, *duration_warnings]

    # 把实际申请的秒数写回 task payload：resume 路径（server.services.resume_executor）
    # 读 payload["duration_seconds"] 重建申请参数，回退顺序是 payload > project.default_duration
    # > 8。入队时（reference_videos 路由 / SDK 工具）payload 从不携带 duration_seconds，
    # 不写回时回退到的是 project 默认时长，而非该 unit 自己的时长——二者不相等是常态，
    # 不能仅在「取档偏移了剧本编排（adjustment != exact）」时才写回，未取档但仍偏离项目
    # 默认值的 unit 同样需要。持久化失败只降级为 resume 元数据不够精确（回退到项目默认
    # 时长，即回退路径本身的行为），不影响当前生成结果，不 fail-fast。
    if task_id is not None:
        await _persist_effective_duration(task_id, effective_duration)
        # task.provider_id 列与 payload 锁定键都是入队时按 unit 声明近似的投影，执行按
        # 解析后实际参考图分桶后两者可能与实际分裂。resume 解析里锁定键优先于列注入——
        # 提交前把实际执行身份写回列与锁定键，否则重启后会按陈旧身份去轮实际 backend 的 provider_job_id，
        # 已提交任务的恢复丢失。
        await _persist_execution_identity(task_id, video.provider_model, execution_capability)

    # 6. 所有内容模式共用三段论渲染。解析条目同时携带请求路径与逻辑主体；产品的一条逻辑
    #    引用可展开成多张图片，裁剪后直接按条目传给渲染，保证 `图片N` 的 1-based 索引与
    #    backend 实际收到的 reference_images 一一对应。产品高保真尾注也只点名仍实际发图的产品。
    #    prompt 始终从执行期新读取的剧本重组（脚本可变 + 队列 dedup 不看 payload，
    #    用入队快照会丢失入队后对镜头文本的编辑）；入队 payload 里的 prompt 仅作守卫点的
    #    校验记录，执行期不使用。
    #    参考音频路径先解析再渲染：渲染层按「确实可用」判定绑定，`@音频N` 的编号与随请求
    #    发出的段数因此严格等长（字段指向已删文件时不会留下指向不存在段的编号）。
    audio_paths = await asyncio.to_thread(resolve_reference_audio_paths, project, project_path)
    voice_settings = VoiceRenderSettings(
        voice_consistency=video.voice_consistency,
        requested_generate_audio=video.requested_generate_audio,
        max_reference_audio=video.max_reference_audio_count,
        model_id=model_name,
        audio_ready=audio_paths,
        requires_reference_image=video.reference_audio_per_image,
    )
    rendered = _render_unit_prompt(
        unit,
        project,
        voice_settings,
        request_references=[entry.reference for entry in constrained_entries],
    )
    rendered_prompt = rendered.prompt
    reference_audio_files, reference_audio_targets = _build_reference_audio_wiring(
        rendered, audio_paths, reference_audio_per_image=voice_settings.requires_reference_image
    )
    # 解析派生的降级提示与生成结果同屏可见：与解析预览面板同一批 {key, params} 条目。
    warnings = [*warnings, *rendered.warnings]

    # 7. 直接把源路径交给咽喉层 generate_video_async —— 参考上传副本的压缩、降档梯子与 413 兜底
    #    统一由 MediaGenerator 负责（发完即删的临时字节），此处不再预压缩、不再管理临时文件，
    #    避免双压。数量裁剪 + 参考图索引对齐已在上游完成，咽喉层压缩 1:1 保序保数，职责不重叠。
    output_path, version, _, video_uri = await generator.generate_video_async(
        prompt=rendered_prompt,
        resource_type="reference_videos",
        resource_id=resource_id,
        reference_images=constrained_refs,
        reference_audio_files=reference_audio_files or None,
        reference_audio_targets=reference_audio_targets,
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


def apply_unit_video_assets(
    script: dict,
    resource_id: str,
    *,
    video_uri: str | None,
    thumb_rel: str | None,
    generated_at: str | None = None,
) -> str | None:
    """在剧本 dict 上写回 unit.generated_assets（video_clip / video_uri / video_thumbnail / status）。

    生成 finalize 与版本还原共用，保证两条路径写出的字段口径一致。所有内容模式的 unit
    都位于 ``video_units``。新结果不含 video_uri / 缩略图时清空旧值，避免指向过期 URI /
    已删除文件。写回失败必须让调用方可见、finalize 不能在剧本未更新时静默成功，
    且两种失败要可区分：unit 不存在抛 KeyError（还原侧跨集同步把它当正常跳过），
    unit 列表结构损坏抛 ScriptEditError（还原侧按脏脚本 warning 降级）。

    ``generated_at`` 缺省戳当前时间（生成 finalize）；版本还原传入被还原版本的入库时间，
    使「片段是否早于当前参考音频设置」按还原回来的旧内容成立，而不是被还原动作洗成最新。

    迁移保留下来的旧 ``source_signature`` 是惰性历史数据，本函数不读取、比较或新增它。
    """
    units = script.get("video_units")
    if not isinstance(units, list):
        raise ScriptEditError("video_units 必须是 list", key="script_edit_unit_lists_invalid")
    for u in units:
        if not isinstance(u, dict) or u.get("unit_id") != resource_id:
            continue
        ga = u.setdefault("generated_assets", {})
        if not isinstance(ga, dict):
            raise ScriptEditError("generated_assets 必须是 dict", key="script_edit_generated_assets_invalid")
        ga["video_clip"] = f"reference_videos/{resource_id}.mp4"
        ga["video_generated_at"] = generated_at or datetime.now(UTC).isoformat()
        if video_uri:
            ga["video_uri"] = video_uri
        else:
            ga.pop("video_uri", None)
        if thumb_rel:
            ga["video_thumbnail"] = thumb_rel
        else:
            ga.pop("video_thumbnail", None)
        ga["status"] = "completed"
        u.pop("stale", None)
        return None
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
    """Normal + resume + 上传共用：抽缩略图、写 unit.generated_assets、返回 result dict。

    迁移保留的旧来源签名不再参与 finalize。
    """
    warnings = warnings if warnings is not None else []

    thumb_dir = project_path / "reference_videos" / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"{resource_id}.jpg"
    if await extract_video_thumbnail(output_path, thumb_path):
        thumb_rel: str | None = f"reference_videos/thumbnails/{resource_id}.jpg"
    else:
        thumb_path.unlink(missing_ok=True)
        thumb_rel = None

    def _update_unit_assets() -> str | None:
        pm = get_project_manager()
        # 资产回写热路径：只动 unit.generated_assets，结构不可能因此变坏，豁免结构校验。
        with pm.locked_script(project_name, script_file, validate=False) as script:
            return apply_unit_video_assets(script, resource_id, video_uri=video_uri, thumb_rel=thumb_rel)

    await asyncio.to_thread(_update_unit_assets)

    def _latest_created_at() -> str | None:
        # 与上面的档案补写读同一个 versions.json，同样在产物与剧本落盘之后：文件不可读时
        # 让结果少一个时间戳，而不是把已成功的付费生成报成失败——只吞掉护栏才成立。
        try:
            history = versions.get_versions("reference_videos", resource_id) or {}
        except Exception:
            logger.warning("读取版本记录取入库时间失败 resource_id=%s", resource_id, exc_info=True)
            return None
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
