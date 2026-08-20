"""费用估算服务 — 计算预估 + 汇总实际费用。"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.resolver import (
    ConfigResolver,
    VideoCapability,
    get_provider_fallback,
    video_bucket_for_generation_mode,
)
from lib.cost_calculator import cost_calculator
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.db.repositories.usage_repo import PROJECT_LEVEL_SEGMENT_KEY, UsageRepository
from lib.generation_queue import GenerationQueue
from lib.grid.layout import GRID_FALLBACK_RESOLUTION, large_grid_allowed, plan_grid_chunks
from lib.narration_delivery import (
    USE_TTS,
    VideoRequestCostFacts,
    video_request_cost_unavailable_problem,
    video_request_requires_exact_quote,
    video_request_reuses_current_visual,
)
from lib.pricing.strategies import PricingParams
from lib.project_manager import grid_storyboard_enabled, is_reference_video_project
from lib.reference_video.request_projection import (
    ConfigReferenceCapabilityProjection,
    FilesystemReferenceAssets,
    ProviderProjectionCandidate,
    ReferenceRequestOptions,
    ReferenceUnitRequestProjector,
    ResolvedReferenceAsset,
    resolve_reference_assets,
    unit_reference_declarations,
)
from lib.script_editor import ScriptEditError
from lib.script_models import get_generated_assets
from lib.speech_composition import video_unit_replan_problems
from lib.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from server.services.grid_resolution import resolve_image_resolution
from server.services.narration_delivery_tasks import (
    active_tts_resource_ids,
    prepare_current_reference_video_request_options,
)

logger = logging.getLogger(__name__)

CostBreakdown = dict[str, float]
ActualBySegment = dict[str, dict[str, CostBreakdown]]
# 费用页展示的记账类型；text 类调用不写 segment_id，只会落在项目级汇总里。
ACTUAL_COST_TYPES = ("image", "video", "audio")


#: 读侧定桶要枚举的全部视频任务类型桶。分镜路线整项目走 i2v 桶；参考路线由公共
#: request projection 按每个 unit 当前实际可用资产分桶。两个桶都在这里预解析，省去按
#: 路线与镜头分支判断该解析哪个桶的复杂度——桶只有两个，代价有界。
_VIDEO_BUCKETS: tuple[VideoCapability, ...] = ("i2v", "r2v")

#: 普通分镜图取不到分辨率档时的计价档。执行侧此路径把 ``None`` 原样下发给 backend、由其自行定档
#: （不像宫格有 ``GRID_FALLBACK_RESOLUTION`` 这一确定的保底档），估价无从同源，只能取最低档保守
#: 计价——宁可低估未配置供应商的项目，也不拿高档单价虚报。取值与分档策略自身的缺省档一致（见
#: ``lib.pricing.strategies``），显式写出是为了让估价侧的保底口径可读、不随策略层缺省漂移。
_IMAGE_PRICING_FALLBACK_RESOLUTION = "1K"


@dataclass(frozen=True)
class _VideoPricing:
    """一个任务类型桶下的视频计价参数——解析出的模型身份、分辨率、有效 generate_audio 与自定义单价。

    五项总是结伴传给三条估算路径，且必须同源于一次解析：分辨率与 generate_audio 都按模型身份
    求值，混用不同桶的分项会算出任何一个模型都不会产生的价。
    """

    provider: str
    model: str | None
    resolution: str | None
    generate_audio: bool
    price: Any


@dataclass(frozen=True, slots=True)
class VideoRequestQuote:
    """Exact current price for one projected provider video request."""

    amount: float
    currency: str
    provider_id: str
    model_id: str
    request_duration_seconds: int

    def to_payload(self) -> dict[str, object]:
        return {
            "amount": self.amount,
            "currency": self.currency,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "request_duration_seconds": self.request_duration_seconds,
        }

    def without_new_video_charge(self) -> VideoRequestQuote:
        return VideoRequestQuote(
            amount=0.0,
            currency=self.currency,
            provider_id=self.provider_id,
            model_id=self.model_id,
            request_duration_seconds=self.request_duration_seconds,
        )


def quote_video_request_from_price(
    facts: VideoRequestCostFacts,
    price: Any,
) -> VideoRequestQuote:
    """Price one request using the same calculator and custom-price coordinates as cost estimation."""

    amount, currency = cost_calculator.calculate_cost(
        facts.provider_id,
        PricingParams(
            call_type="video",
            model=facts.model_id,
            resolution=facts.resolution,
            duration_seconds=facts.duration_seconds,
            generate_audio=facts.generate_audio,
        ),
        custom_price_input=price.price_input,
        custom_price_output=price.price_output,
        custom_currency=price.currency,
        estimate_only=True,
    )
    return VideoRequestQuote(
        amount=round(amount, 6),
        currency=currency,
        provider_id=facts.provider_id,
        model_id=facts.model_id,
        request_duration_seconds=facts.duration_seconds,
    )


async def quote_video_request(
    facts: VideoRequestCostFacts,
    session_factory: async_sessionmaker[AsyncSession],
) -> VideoRequestQuote | None:
    """Resolve current pricing and quote a projected video request."""

    try:
        async with session_factory() as session:
            price = await CustomProviderRepository(session).resolve_price(facts.provider_id, facts.model_id)
        return quote_video_request_from_price(facts, price)
    except Exception:
        logger.warning(
            "无法为 current video request 计算精确费用 provider=%s model=%s duration=%s",
            facts.provider_id,
            facts.model_id,
            facts.duration_seconds,
            exc_info=True,
        )
        return None


def _add_cost(target: CostBreakdown, amount: float, currency: str) -> None:
    if amount <= 0:
        return
    target[currency] = round(target.get(currency, 0) + amount, 6)


def _merge_breakdowns(a: CostBreakdown, b: CostBreakdown) -> CostBreakdown:
    merged = dict(a)
    for cur, amt in b.items():
        merged[cur] = round(merged.get(cur, 0) + amt, 6)
    return merged


def _claim_actual(
    actual_by_segment: ActualBySegment,
    claimed: set[tuple[str, str]],
    segment_id: str,
    cost_types: tuple[str, ...] = ACTUAL_COST_TYPES,
) -> dict[str, CostBreakdown]:
    """认领一份 segment 实付；同一 (记账 key, 类型) 在一次估算中最多返回一次。

    认领粒度到类型而非整条 key：调用方只消费其中一部分类型时，剩下的仍是未认领状态，
    由兜底聚合收进「未归属」，不会被整条认领吞掉。
    """
    if not segment_id:
        return {}
    actual = actual_by_segment.get(segment_id, {})
    claimed_now: dict[str, CostBreakdown] = {}
    for cost_type in cost_types:
        if (segment_id, cost_type) in claimed:
            continue
        claimed.add((segment_id, cost_type))
        amounts = actual.get(cost_type)
        if amounts:
            claimed_now[cost_type] = amounts
    return claimed_now


def _split_cost_across(cost: CostBreakdown, parts: int) -> list[CostBreakdown]:
    """把一笔按整体计费的费用均摊成 ``parts`` 份，除不尽的余数补给最后一份。

    补余数是为了让分摊结果的合计与原值分文不差：调用方按分摊后的份额累加集/项目合计，
    若每份都独立 round，误差会随镜头数放大到用户可见的总价上。
    """
    if parts <= 0:
        return []
    split: list[CostBreakdown] = [{} for _ in range(parts)]
    for currency, amount in cost.items():
        share = round(amount / parts, 6)
        for bucket in split[:-1]:
            bucket[currency] = share
        split[-1][currency] = round(amount - share * (parts - 1), 6)
    return split


def _estimate_unit_video_cost(
    *,
    unit_id: str,
    duration_seconds: int,
    video: _VideoPricing,
) -> CostBreakdown:
    """一个视频单元取档后秒数的视频估值。计价失败返回空 breakdown（该 unit 不计费）。

    两条参考生视频估算路径（ad 摊回镜头、narration/drama 按 unit 展示）的展示颗粒度不同，
    但「按取档后秒数向 provider 询价」这一步与颗粒度无关，共用同一实现避免两处漂移。
    """
    est_video: CostBreakdown = {}
    try:
        amount, currency = cost_calculator.calculate_cost(
            video.provider,
            PricingParams(
                call_type="video",
                model=video.model,
                resolution=video.resolution,
                duration_seconds=duration_seconds,
                generate_audio=video.generate_audio,
            ),
            custom_price_input=video.price.price_input,
            custom_price_output=video.price.price_output,
            custom_currency=video.price.currency,
            estimate_only=True,
        )
        _add_cost(est_video, amount, currency)
    except Exception:
        logger.debug("无法计算 video 预估 for %s", unit_id, exc_info=True)
    return est_video


class _AssumeResolvedAssetsAvailable:
    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        del asset
        return True


class CostEstimationService:
    def __init__(
        self,
        resolver: ConfigResolver,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        project_path: Path | None = None,
    ) -> None:
        self._resolver = resolver
        self._session_factory = session_factory
        self._project_path = project_path
        self._generation_queue = GenerationQueue(session_factory=session_factory)

    async def compute(
        self,
        project_data: dict[str, Any],
        scripts: dict[str, dict[str, Any]],
        *,
        project_name: str,
        reference_request_options: Mapping[str, ReferenceRequestOptions] | None = None,
    ) -> dict[str, Any]:
        episodes_meta = project_data.get("episodes", [])
        is_reference_video = is_reference_video_project(project_data)
        use_tts_ids = {
            unit_id
            for unit_id, options in (reference_request_options or {}).items()
            if options.narration_delivery == USE_TTS
        }
        tts_queries: list[tuple[str, tuple[str, ...]]] = []
        if use_tts_ids:
            for script_file, script in scripts.items():
                raw_units = script.get("video_units")
                if not isinstance(raw_units, list):
                    continue
                unit_ids = tuple(
                    unit_id
                    for unit in raw_units
                    if isinstance(unit, dict)
                    and isinstance(unit_id := unit.get("unit_id"), str)
                    and unit_id in use_tts_ids
                )
                if unit_ids:
                    tts_queries.append((script_file, unit_ids))
        active_batches = await asyncio.gather(
            *(
                active_tts_resource_ids(
                    project_name=project_name,
                    resource_ids=unit_ids,
                    script_file=script_file,
                    queue=self._generation_queue,
                )
                for script_file, unit_ids in tts_queries
            )
        )
        active_tts = frozenset().union(*active_batches)

        # Resolve current model config（共享单一 session）。估价以 T2I 为准（T2I/I2I 是正交能力槽，
        # T2I 缺失不应回落 I2I —— 那会拿错误能力的价目算费用）。
        # image/video 的项目覆盖优先级由 ConfigResolver 统一解析，与执行路径共用同一套
        # payload>project>全局默认 链路，此处 payload 传 None（预估无历史任务 payload 可排空）。
        projection_capabilities = ConfigReferenceCapabilityProjection(self._resolver)
        reference_candidates: dict[VideoCapability, ProviderProjectionCandidate] = {}
        if is_reference_video:
            for capability in _VIDEO_BUCKETS:
                try:
                    reference_candidates[capability] = await projection_capabilities.resolve_candidate(
                        project_data,
                        capability,
                    )
                except Exception:
                    # 真正使用该 bucket 的 unit 会由 projector 返回结构化 blocker；未使用 bucket
                    # 的配置问题不应拖垮整份费用页。
                    logger.debug("reference_video %s bucket 投影预解析失败", capability, exc_info=True)
        async with self._resolver.session() as r:
            try:
                resolved_image = await r.resolve_image_backend(project_data, None, capability="t2i")
                image_provider, image_model = resolved_image.provider_id, resolved_image.model_id
            except Exception:
                image_provider, image_model = "unknown", "unknown"

            # T2I 槽分辨率档：与路由入队、SDK 工具共用 ``grid_resolution`` 的取档，估算的宫格
            # 张数才不会与实际入队张数漂移；同一档位又是两路分镜图的计价档——宫格图未配置时回落
            # ``GRID_FALLBACK_RESOLUTION``（与 ``execute_grid_task`` 下发的保底档同源），普通
            # 分镜图未配置时回落 ``_IMAGE_PRICING_FALLBACK_RESOLUTION``。解析在两路之前，宫格
            # 与非宫格项目共用这一次 IO。计价与执行取的是同一个 T2I 槽、同一套项目配置，但身份
            # 键不同（此处 registry ``model_id``，执行侧构造后的 backend 型号），两者分叉的供应
            # 商上估价档位可能与实际渲染档位不一致。
            image_resolution = await resolve_image_resolution(r, project_data)
            grid_allow_large = large_grid_allowed(image_resolution)

            # 视频按任务类型桶解析（``docs/adr/0054``），与执行扣费同一个模型：图生视频 / 宫格算
            # i2v 桶的价；参考生视频逐 unit 水合当前资产后分桶。两个桶都在这里
            # 解析出来（见 ``_VIDEO_BUCKETS``），分辨率与
            # generate_audio 随各自的模型身份求值。
            video_identity: dict[VideoCapability, tuple[str, str, str | None, bool]] = {}
            for capability in _VIDEO_BUCKETS:
                candidate = reference_candidates.get(capability)
                if candidate is not None:
                    bucket_provider = candidate.provider_id
                    bucket_model = candidate.model_id
                    bucket_resolution = candidate.resolution
                    bucket_audio = candidate.generate_audio
                else:
                    try:
                        resolved_video = await r.resolve_video_backend(project_data, None, capability=capability)
                        bucket_provider, bucket_model = resolved_video.provider_id, resolved_video.model_id
                    except Exception:
                        bucket_provider, bucket_model = "unknown", "unknown"
                    # 分镜路线保持既有宽容报价；参考路线逐 unit 的严格能力校验由 request projector
                    # 完成，能力元数据异常时不会产生 unit 报价。
                    bucket_audio = await r.video_pricing_generate_audio(bucket_provider, bucket_model, project_data)
                    try:
                        bucket_resolution = await r.resolve_resolution(
                            project_data,
                            bucket_provider,
                            bucket_model or "",
                        )
                    except Exception:
                        bucket_resolution = None
                video_identity[capability] = (
                    bucket_provider,
                    bucket_model,
                    bucket_resolution or get_provider_fallback(bucket_provider),
                    bucket_audio,
                )

            # 旁白配音（TTS）模型：project 覆盖 > 全局默认 > auto-resolve；
            # 未配置任何 audio 供应商时回落 unknown，该维度预估为空
            try:
                resolved_audio = await r.resolve_audio_backend(project_data, None)
                audio_provider, audio_model = resolved_audio.provider_id, resolved_audio.model_id
            except Exception:
                audio_provider, audio_model = "unknown", "unknown"

        # Get actual costs + 自定义供应商价格（缺则预估恒为零，需与实际记账同源预查 DB 单价）
        async with self._session_factory() as session:
            actual_by_segment = await UsageRepository(session).get_actual_costs_by_segment(project_name)
            custom_repo = CustomProviderRepository(session)
            image_price = await custom_repo.resolve_price(image_provider, image_model)
            audio_price = await custom_repo.resolve_price(audio_provider, audio_model)
            # 两个桶常解析到同一个模型，按身份去重后再查单价，不重复打 DB。
            video_prices: dict[tuple[str, str], Any] = {}
            for bucket_provider, bucket_model, _, _ in video_identity.values():
                if (bucket_provider, bucket_model) not in video_prices:
                    video_prices[(bucket_provider, bucket_model)] = await custom_repo.resolve_price(
                        bucket_provider, bucket_model
                    )

        video_pricing: dict[VideoCapability, _VideoPricing] = {
            capability: _VideoPricing(
                provider=bucket_provider,
                model=bucket_model,
                resolution=bucket_resolution,
                generate_audio=bucket_audio,
                price=video_prices[(bucket_provider, bucket_model)],
            )
            for capability, (
                bucket_provider,
                bucket_model,
                bucket_resolution,
                bucket_audio,
            ) in video_identity.items()
        }
        # 项目层展示的视频模型按项目 generation_mode 定桶：``models`` 回答的是「当前项目配置」
        # 的路线主桶；参考路线内退化镜头的逐 unit 降级计价在集级估算路径内完成，不改变项目层
        # 展示身份。
        project_video = video_pricing[video_bucket_for_generation_mode(project_data.get("generation_mode"))]

        grid_enabled = grid_storyboard_enabled(project_data)
        # 规范化 aspect_ratio：可能是 str 或 dict，复用生成任务的解析逻辑
        raw_ar = project_data.get("aspect_ratio")
        if isinstance(raw_ar, str):
            aspect_ratio = raw_ar
        elif isinstance(raw_ar, dict):
            aspect_ratio = raw_ar.get("storyboards", "9:16")
        else:
            # narration/ad 默认竖屏，drama（含未知值的历史兜底）默认横屏
            aspect_ratio = "9:16" if project_data.get("content_mode", "narration") in {"narration", "ad"} else "16:9"

        # 预计算图片单价
        image_unit_cost: tuple[float, str] | None = None
        grid_image_unit_cost: tuple[float, str] | None = None
        try:
            image_unit_cost = cost_calculator.calculate_cost(
                image_provider,
                PricingParams(
                    call_type="image",
                    model=image_model,
                    resolution=image_resolution or _IMAGE_PRICING_FALLBACK_RESOLUTION,
                ),
                custom_price_input=image_price.price_input,
                custom_price_output=image_price.price_output,
                custom_currency=image_price.currency,
            )
        except Exception:
            logger.debug("无法计算 image 预估单价", exc_info=True)

        if grid_enabled:
            try:
                grid_image_unit_cost = cost_calculator.calculate_cost(
                    image_provider,
                    PricingParams(
                        call_type="image",
                        model=image_model,
                        resolution=image_resolution or GRID_FALLBACK_RESOLUTION,
                    ),
                    custom_price_input=image_price.price_input,
                    custom_price_output=image_price.price_output,
                    custom_currency=image_price.currency,
                )
            except Exception:
                grid_image_unit_cost = image_unit_cost

        episodes_result = []
        proj_est: dict[str, CostBreakdown] = {}
        proj_act: dict[str, CostBreakdown] = {}
        claimed_actual: set[tuple[str, str]] = set()

        def _accumulate_episode(
            ep_meta: dict[str, Any],
            segments_result: list[dict[str, Any]],
            ep_est: dict[str, CostBreakdown],
            ep_act: dict[str, CostBreakdown],
        ) -> None:
            """收下一集的估算结果并并入项目级合计（两条估算路径共用的收尾）。"""
            episodes_result.append(
                {
                    "episode": ep_meta.get("episode"),
                    "title": ep_meta.get("title", ""),
                    "segments": segments_result,
                    "totals": {"estimate": ep_est, "actual": ep_act},
                }
            )
            for cost_type in ("image", "video", "audio"):
                proj_est[cost_type] = _merge_breakdowns(proj_est.get(cost_type, {}), ep_est.get(cost_type, {}))
                proj_act[cost_type] = _merge_breakdowns(proj_act.get(cost_type, {}), ep_act.get(cost_type, {}))

        # 参考生视频路径跳过分镜步骤，所有内容模式都按自包含 reference_unit 计费与展示。
        #
        # 生成路径以项目路线为唯一真相源，整个项目同一条路线、逐集不变（剧本不携带路线信息）；
        # 参考路线内的定桶再由 request projection 按当前资产逐 unit 分流。
        for ep_meta in episodes_meta:
            script_file = ep_meta.get("script_file", "")
            script = scripts.get(script_file)
            if not script:
                continue

            raw_units = script.get("video_units")
            video_units: list[Any] = raw_units if isinstance(raw_units, list) else []
            estimate_by_unit = is_reference_video

            if estimate_by_unit:
                segments_result, ep_est, ep_act = await self._estimate_unit_reference_video_episode(
                    project_name=project_name,
                    project=project_data,
                    script=script,
                    script_file=script_file,
                    units=video_units,
                    projection_capabilities=projection_capabilities,
                    video_prices=video_prices,
                    actual_by_segment=actual_by_segment,
                    claimed_actual=claimed_actual,
                    request_options=reference_request_options,
                    active_tts=active_tts,
                )
                _accumulate_episode(ep_meta, segments_result, ep_est, ep_act)
                continue

            # 分镜路径固定用 i2v 桶；参考路径已在上方的 unit 投影分支完成定桶。
            episode_video = video_pricing["i2v"]

            try:
                raw_segments, id_key, _, _, _ = get_storyboard_items(script)
            except ScriptEditError as exc:
                # 单集脏脚本(segments/scenes 键损坏)不应让整个项目费用估算 5xx;降级把该集
                # 估算为 0(raw_segments=[]) + warning 让运维知道,UI 仍能展示其他正常集的估算。
                logger.warning("费用估算跳过脏脚本 %s: %s", script_file, exc)
                raw_segments, id_key = [], "segment_id"

            # Grid 模式：预计算每个 segment 的图片分摊费用。份额以条目在 ``raw_segments`` 中的
            # 位置为身份，与下方实付均摊同口径（理由见该处）；分组由
            # ``group_scenes_by_segment_break`` 按顺序切出、连续且不重不漏，故位置即组内序号
            # 加上前序各组的长度。
            grid_cost_per_index: dict[int, tuple[float, str]] = {}
            if grid_enabled and grid_image_unit_cost:
                group_offset = 0
                for group in group_scenes_by_segment_break(raw_segments, id_key):
                    n = len(group)
                    # 宫格张数与实际入队同源（plan_grid_chunks）：超上限分组按切块后的
                    # 张数计费，避免估算与执行漂移。
                    plans = plan_grid_chunks(group, aspect_ratio, allow_large_grid=grid_allow_large)
                    if plans:
                        per_scene_cost = round(grid_image_unit_cost[0] * len(plans) / n, 6)
                        for offset_in_group in range(n):
                            grid_cost_per_index[group_offset + offset_in_group] = (
                                per_scene_cost,
                                grid_image_unit_cost[1],
                            )
                    group_offset += n

            # --- Grid actual cost apportionment ---
            # 均摊份额以条目在 raw_segments 中的位置为身份，而非条目 ID：ADR 0053 接受一张
            # 宫格覆盖的多个条目共用同一 ID，位置唯一而 ID 不唯一，只有按位置组织才能让每个
            # 条目（含同 ID 条目）恰好消费一次自己的份额。
            grid_to_indices: dict[str, list[int]] = {}
            for idx, seg in enumerate(raw_segments):
                gid = get_generated_assets(seg).get("grid_id")
                if gid and seg.get(id_key, ""):
                    grid_to_indices.setdefault(gid, []).append(idx)

            # 逐宫格算出每个位置的份额；``_split_cost_across`` 的余数补偿保证各份之和与冻结
            # 实付分文不差。
            grid_actual_per_index: dict[int, CostBreakdown] = {}
            for gid, indices in grid_to_indices.items():
                grid_cost = _claim_actual(actual_by_segment, claimed_actual, gid, ("image",)).get("image", {})
                if grid_cost:
                    for idx, share in zip(indices, _split_cost_across(grid_cost, len(indices)), strict=True):
                        grid_actual_per_index[idx] = share

            segments_result = []
            ep_est: dict[str, CostBreakdown] = {}
            ep_act: dict[str, CostBreakdown] = {}

            for idx, seg in enumerate(raw_segments):
                seg_id = seg.get(id_key, "")
                duration = seg.get("duration_seconds", 8)

                est_image: CostBreakdown = {}
                est_video: CostBreakdown = {}
                est_audio: CostBreakdown = {}

                if grid_enabled and idx in grid_cost_per_index:
                    cost_amount, cost_currency = grid_cost_per_index[idx]
                    _add_cost(est_image, cost_amount, cost_currency)
                elif image_unit_cost:
                    _add_cost(est_image, image_unit_cost[0], image_unit_cost[1])

                try:
                    vid_amount, vid_currency = cost_calculator.calculate_cost(
                        episode_video.provider,
                        PricingParams(
                            call_type="video",
                            model=episode_video.model,
                            resolution=episode_video.resolution,
                            duration_seconds=duration,
                            generate_audio=episode_video.generate_audio,
                        ),
                        custom_price_input=episode_video.price.price_input,
                        custom_price_output=episode_video.price.price_output,
                        custom_currency=episode_video.price.currency,
                    )
                    _add_cost(est_video, vid_amount, vid_currency)
                except Exception:
                    logger.debug("无法计算 video 预估 for %s", seg_id, exc_info=True)

                # 旁白配音按 novel_text 字符数估价（仅旁白/解说 segment 携带原文）
                novel_text = seg.get("novel_text")
                narration_chars = len(novel_text.strip()) if isinstance(novel_text, str) else 0
                if narration_chars:
                    try:
                        audio_amount, audio_currency = cost_calculator.calculate_cost(
                            audio_provider,
                            PricingParams(call_type="audio", model=audio_model, usage_tokens=narration_chars),
                            custom_price_input=audio_price.price_input,
                            custom_price_output=audio_price.price_output,
                            custom_currency=audio_price.currency,
                        )
                        _add_cost(est_audio, audio_amount, audio_currency)
                    except Exception:
                        logger.debug("无法计算 audio 预估 for %s", seg_id, exc_info=True)

                seg_actual = _claim_actual(actual_by_segment, claimed_actual, seg_id)
                act_image: CostBreakdown = seg_actual.get("image", {})
                if idx in grid_actual_per_index:
                    act_image = _merge_breakdowns(act_image, grid_actual_per_index[idx])
                act_video: CostBreakdown = seg_actual.get("video", {})
                act_audio: CostBreakdown = seg_actual.get("audio", {})

                segments_result.append(
                    {
                        "segment_id": seg_id,
                        "duration_seconds": duration,
                        "estimate": {"image": est_image, "video": est_video, "audio": est_audio},
                        "actual": {"image": act_image, "video": act_video, "audio": act_audio},
                    }
                )

                seg_est_by_type = {"image": est_image, "video": est_video, "audio": est_audio}
                seg_act_by_type = {"image": act_image, "video": act_video, "audio": act_audio}
                for cost_type in ("image", "video", "audio"):
                    ep_est[cost_type] = _merge_breakdowns(
                        ep_est.get(cost_type, {}),
                        seg_est_by_type[cost_type],
                    )
                    ep_act[cost_type] = _merge_breakdowns(
                        ep_act.get(cost_type, {}),
                        seg_act_by_type[cost_type],
                    )

            _accumulate_episode(ep_meta, segments_result, ep_est, ep_act)

        # 当前剧本没有认领到的历史记账仍是真实支出。规范 segment/unit ID 自带 E{n}
        # 前缀，可回填对应集；无法识别或对应集已不存在的记录仍纳入项目合计。
        episodes_by_number = {ep["episode"]: ep for ep in episodes_result}
        # 剧本文件缺失或尚未生成的集不进入估算结果，但它的历史支出仍属于这一集。按需补一条
        # 只含实付的集结果，让这笔钱显示在集行上，而不是静默退到项目合计。
        meta_by_number = {ep_meta.get("episode"): ep_meta for ep_meta in episodes_meta}

        def _attribution_target(episode_number: int) -> dict[str, Any] | None:
            existing = episodes_by_number.get(episode_number)
            if existing is not None:
                return existing
            ep_meta = meta_by_number.get(episode_number)
            if ep_meta is None:
                return None
            created: dict[str, Any] = {
                "episode": episode_number,
                "title": ep_meta.get("title", ""),
                "segments": [],
                "totals": {"estimate": {}, "actual": {}},
            }
            episodes_result.append(created)
            episodes_by_number[episode_number] = created
            return created

        for segment_id, actual_by_type in actual_by_segment.items():
            if segment_id == PROJECT_LEVEL_SEGMENT_KEY:
                continue
            match = re.match(r"^E(\d+)(?:S|U)", segment_id)
            for cost_type in ACTUAL_COST_TYPES:
                amounts = actual_by_type.get(cost_type, {})
                if not amounts or (segment_id, cost_type) in claimed_actual:
                    continue
                proj_act["unassigned"] = _merge_breakdowns(proj_act.get("unassigned", {}), amounts)
                episode_result = _attribution_target(int(match.group(1))) if match else None
                if episode_result is not None:
                    episode_actual = episode_result["totals"]["actual"]
                    episode_actual["unassigned"] = _merge_breakdowns(
                        episode_actual.get("unassigned", {}),
                        amounts,
                    )
        # 补出来的集结果追加在末尾，重排回 project.json 的集顺序，让费用页集行不跳序。
        meta_order = {ep_meta.get("episode"): i for i, ep_meta in enumerate(episodes_meta)}
        episodes_result.sort(key=lambda ep: meta_order.get(ep["episode"], len(meta_order)))

        # Project-level actual costs (characters/scenes/props/products 资产图 —— segment_id is null)
        async with self._session_factory() as session:
            project_image_by_type = await UsageRepository(session).get_project_image_costs_by_asset_type(project_name)
        for asset_type in ("characters", "scenes", "props", "products"):
            bucket = project_image_by_type.get(asset_type)
            if bucket:
                proj_act[asset_type] = bucket
        # segment_id 为空的记账里，资产图已按类型单列，剩下的仍是真实支出：无法按 output_path
        # 归类的图，以及 segment_id 列回填前的历史 video/audio 行。它们没有集归属线索，只并入
        # 项目级「未归属」。
        project_level_actual = actual_by_segment.get(PROJECT_LEVEL_SEGMENT_KEY, {})
        for amounts in (
            project_image_by_type.get("other", {}),
            project_level_actual.get("video", {}),
            project_level_actual.get("audio", {}),
        ):
            if amounts:
                proj_act["unassigned"] = _merge_breakdowns(proj_act.get("unassigned", {}), amounts)

        return {
            "project_name": project_name,
            "models": {
                "image": {"provider": image_provider, "model": image_model},
                "video": {"provider": project_video.provider, "model": project_video.model},
                "audio": {"provider": audio_provider, "model": audio_model},
            },
            "episodes": episodes_result,
            "project_totals": {"estimate": proj_est, "actual": proj_act},
        }

    async def _estimate_unit_reference_video_episode(
        self,
        *,
        project_name: str,
        project: dict[str, Any],
        script: dict[str, Any],
        script_file: str,
        units: list[Any],
        projection_capabilities: ConfigReferenceCapabilityProjection,
        video_prices: dict[tuple[str, str], Any],
        actual_by_segment: ActualBySegment,
        claimed_actual: set[tuple[str, str]],
        request_options: Mapping[str, ReferenceRequestOptions] | None = None,
        active_tts: frozenset[str] = frozenset(),
    ) -> tuple[list[dict[str, Any]], dict[str, CostBreakdown], dict[str, CostBreakdown]]:
        """reference_video 集的估值：unit 本身就是展示与计费颗粒度。

        unit 本身就是最小可寻址单位，
        前端画布与费用面板均按 ``unit_id`` 索引（见 ``ReferenceVideoCanvas`` 读
        ``cost-store`` 的 ``_segmentIndex.get(unit.unit_id)``），故此处不需要
        ``_split_cost_across`` 这一步。

        取档先水合 unit 引用的当前可用图片（有图 → r2v，无图退化 unit → i2v），
        再解析该桶模型的能力；声明引用与实际资产分裂时返回结构化 blocker，不换桶伪报价。
        请求时长基准通常是 ``unit.duration_seconds``；选择 ``use_tts`` 时还会纳入上游提供的
        实际旁白时长下限。按该基准取档后用同桶模型计费，与执行请求的秒数对齐。

        无图片/音频估值维度：该模式跳过分镜步骤（无分镜图），unit 正文是一整段、没有可供
        独立音频计价的旁白/口播文案字段。实付按 ``actual_by_segment[unit_id]`` 三个维度原样透传——``lib/media_generator.py``
        对 ``resource_type == "reference_videos"`` 的记账以 unit_id 写入 usage 的 segment_id，
        与本函数的输出 identity 一致。切换模式前按分镜 ID（``E1S1`` 等）记的历史支出不在此
        呈现：unit 与分镜之间没有映射关系，无处归属。

        正文为空或命中 ``video_unit_replan_problems`` 的 unit 不产生预估：这些 unit 会被
        ``enqueue_videos.py::_reference_unit_spec`` 拒绝，估值给出非零金额会展示一笔查无实据的
        费用；判据与入队侧共用同一个正文与重规划问题模型，不能自行另起一套处理否则两处会漂移。但该 unit 仍要整条保留、纳入汇总——不可入队只影响能否产生新预估，不影响该
        unit 是否曾经成功生成过（``actual_by_segment[unit_id]`` 记的是历史实付，与 unit 当前编辑状态
        无关）：unit 曾成功生成、随后剧本被编辑成不可入队状态，其历史支出不能因此从段级/集级/项目级
        合计里消失。
        """
        segments_result: list[dict[str, Any]] = []
        ep_est: dict[str, CostBreakdown] = {}
        ep_act: dict[str, CostBreakdown] = {}

        if self._project_path is None:
            availability = _AssumeResolvedAssetsAvailable()
        else:
            availability = FilesystemReferenceAssets(self._project_path)
        projector = ReferenceUnitRequestProjector(projection_capabilities, availability)

        for unit in units:
            if not isinstance(unit, dict):
                continue
            # unit_id 必须原本就是字符串：入队执行时按该字符串与剧本原始（未转型）值比较定位
            # unit（``execute_reference_video_task``），数字/布尔等裸写 truthy 值 str() 后能通过
            # 这里的估算，但执行时永远因类型不等找不到 unit——估算不能展示一笔实际跑不起来的费用。
            raw_unit_id = unit.get("unit_id")
            if not isinstance(raw_unit_id, str) or not raw_unit_id:
                continue
            unit_id = raw_unit_id

            # text 非字符串（如裸写 "text": true/1）同样不能让单条脏数据中断整次估算。
            text = unit.get("text")
            enqueueable = isinstance(text, str) and bool(text.strip()) and not video_unit_replan_problems(unit)

            est_video: CostBreakdown = {}
            request_quote: VideoRequestQuote | None = None
            cost_problem_payload: dict[str, object] | None = None
            projection_problems: list[dict[str, Any]] = []
            projection = None
            options = (request_options or {}).get(unit_id, ReferenceRequestOptions())
            if enqueueable:
                # agent/外部编辑过的剧本可能写入非数值 duration_seconds（如 "bad"/列表/字典）；
                # 单个 unit 的无效内容不应让整个项目估算失败，因此资产解析与 request projection
                # 的 ValueError/TypeError 只跳过该 unit。能力解析错误由 projector 转为结构化 blocker，
                # 正常保留在该 unit 的报价结果中。
                try:
                    if self._project_path is None:
                        resolved_assets = [
                            ResolvedReferenceAsset(
                                path=Path(f"{reference.type}/{reference.name}.png"),
                                reference=reference,
                            )
                            for reference in unit_reference_declarations(project, unit)
                        ]
                    else:
                        resolved_assets = resolve_reference_assets(project, self._project_path, unit)
                    if self._project_path is not None:
                        options = await prepare_current_reference_video_request_options(
                            project=project,
                            script=script,
                            script_file=script_file,
                            unit=unit,
                            project_path=self._project_path,
                            options=options,
                            project_name=project_name,
                            tts_in_progress=unit_id in active_tts,
                        )
                    projection = await projector.project_current(
                        project=project,
                        script=script,
                        unit=unit,
                        resolved_assets=resolved_assets,
                        options=options,
                    )
                except (ValueError, TypeError):
                    logger.warning("费用估算跳过时长非法的 unit %s", unit_id, exc_info=True)
                    projection = None
                if projection is not None:
                    projection_problems = projection.problem_payloads()
                    blockers = [
                        problem
                        for problem in projection.blocking_problems
                        if problem.code != "reference_duration_confirmation_required"
                    ]
                else:
                    blockers = []
                if projection is not None and projection.cost is not None and not blockers:
                    cost = projection.cost
                    price = video_prices.get((cost.provider_id, cost.model_id))
                    if price is not None:
                        try:
                            priced_quote = quote_video_request_from_price(cost, price)
                        except Exception:
                            logger.warning(
                                "无法为 reference unit %s 计算精确费用 provider=%s model=%s duration=%s",
                                unit_id,
                                cost.provider_id,
                                cost.model_id,
                                cost.duration_seconds,
                                exc_info=True,
                            )
                        else:
                            if options.narration_delivery == USE_TTS:
                                request_quote = priced_quote
                                if video_request_reuses_current_visual(
                                    request_duration_seconds=cost.duration_seconds,
                                    current_reusable_visual_duration_seconds=(
                                        options.current_reusable_visual_duration_seconds
                                    ),
                                ):
                                    request_quote = request_quote.without_new_video_charge()
                            if request_quote is None or request_quote.amount > 0:
                                _add_cost(est_video, priced_quote.amount, priced_quote.currency)
                    if (
                        options.narration_delivery == USE_TTS
                        and request_quote is None
                        and video_request_requires_exact_quote(
                            request_duration_seconds=cost.duration_seconds,
                            planned_duration_seconds=projection.planned_duration,
                            current_visual_duration_seconds=options.current_visual_duration_seconds,
                            current_reusable_visual_duration_seconds=options.current_reusable_visual_duration_seconds,
                        )
                    ):
                        cost_problem_payload = video_request_cost_unavailable_problem(cost).to_payload(unit_id=unit_id)
                        projection_problems.append(cost_problem_payload)

            unit_actual = _claim_actual(actual_by_segment, claimed_actual, unit_id)
            act_image: CostBreakdown = unit_actual.get("image", {})
            act_video: CostBreakdown = unit_actual.get("video", {})
            act_audio: CostBreakdown = unit_actual.get("audio", {})

            segments_result.append(
                {
                    "segment_id": unit_id,
                    "duration_seconds": unit.get("duration_seconds", 8),
                    "request_projection": (
                        {
                            **projection.to_advisory_payload(),
                            **({"allowed": False} if cost_problem_payload is not None else {}),
                            "capability": projection.hydrated_capability,
                            "problems": projection_problems,
                            **({"request_cost": request_quote.to_payload()} if request_quote is not None else {}),
                        }
                        if enqueueable and projection is not None
                        else {
                            "provider_id": None,
                            "model_id": None,
                            "capability": None,
                            "duration_input": None,
                            "request_duration": None,
                            "problems": projection_problems,
                        }
                    ),
                    "estimate": {"image": {}, "video": est_video, "audio": {}},
                    "actual": {"image": act_image, "video": act_video, "audio": act_audio},
                }
            )
            ep_est["video"] = _merge_breakdowns(ep_est.get("video", {}), est_video)
            for cost_type, amounts in (("image", act_image), ("video", act_video), ("audio", act_audio)):
                ep_act[cost_type] = _merge_breakdowns(ep_act.get(cost_type, {}), amounts)

        return segments_result, ep_est, ep_act
