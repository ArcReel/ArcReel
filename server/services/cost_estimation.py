"""费用估算服务 — 计算预估 + 汇总实际费用。"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
from lib.grid.layout import calculate_grid_layout
from lib.pricing.strategies import PricingParams
from lib.project_manager import grid_storyboard_enabled, is_reference_video_project
from lib.reference_video import assemble_shots_text
from lib.reference_video.ad_units import derive_ad_reference_units, resolve_ad_unit_shots
from lib.reference_video.units import reference_unit_video_bucket
from lib.script_editor import ScriptEditError
from lib.script_models import get_generated_assets
from lib.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from server.services.reference_video_tasks import (
    ProjectDurationContext,
    precheck_unit,
    resolve_project_duration_context,
)

logger = logging.getLogger(__name__)

CostBreakdown = dict[str, float]
ActualBySegment = dict[str, dict[str, CostBreakdown]]
# 费用页展示的记账类型；text 类调用不写 segment_id，只会落在项目级汇总里。
ACTUAL_COST_TYPES = ("image", "video", "audio")


#: 读侧定桶要枚举的全部视频能力桶。分镜路线整项目走 i2v 桶；参考路线按 unit 声明的参考集
#: 逐 unit 分桶（有参考图 → r2v，无参考图退化镜头 → i2v）。两个桶都在这里预解析，省去按
#: 路线与镜头分支判断该解析哪个桶的复杂度——桶只有两个，代价有界。
_VIDEO_BUCKETS: tuple[VideoCapability, ...] = ("i2v", "r2v")


@dataclass(frozen=True)
class _VideoPricing:
    """一个能力桶下的视频计价参数——解析出的模型身份、分辨率、有效 generate_audio 与自定义单价。

    五项总是结伴传给三条估算路径，且必须同源于一次解析：分辨率与 generate_audio 都按模型身份
    求值，混用不同桶的分项会算出任何一个模型都不会产生的价。
    """

    provider: str
    model: str | None
    resolution: str | None
    generate_audio: bool
    price: Any


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
    """一个参考视频 unit 取档后秒数的视频估值。计价失败返回空 breakdown（该 unit 不计费）。

    两条参考视频估算路径（ad 摊回镜头、narration/drama 按 unit 展示）的展示颗粒度不同，
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


class CostEstimationService:
    def __init__(self, resolver: ConfigResolver, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._resolver = resolver
        self._session_factory = session_factory

    async def compute(
        self,
        project_data: dict[str, Any],
        scripts: dict[str, dict[str, Any]],
        *,
        project_name: str,
    ) -> dict[str, Any]:
        episodes_meta = project_data.get("episodes", [])

        # Resolve current model config（共享单一 session）。估价以 T2I 为准（T2I/I2I 是正交能力槽，
        # T2I 缺失不应回落 I2I —— 那会拿错误能力的价目算费用）。
        # image/video 的项目覆盖优先级由 ConfigResolver 统一解析，与执行路径共用同一套
        # payload>project>全局默认 链路，此处 payload 传 None（预估无历史任务 payload 可排空）。
        async with self._resolver.session() as r:
            try:
                resolved_image = await r.resolve_image_backend(project_data, None, capability="t2i")
                image_provider, image_model = resolved_image.provider_id, resolved_image.model_id
            except Exception:
                image_provider, image_model = "unknown", "unknown"

            # 视频按能力桶解析（``docs/adr/0054``），与执行扣费同一个模型：图生视频 / 宫格算
            # i2v 桶的价；参考生视频按 unit 声明的参考集逐 unit 分桶（有参考图 → r2v，无参考图
            # 退化镜头 → i2v）。两个桶都在这里解析出来（见 ``_VIDEO_BUCKETS``），分辨率与
            # generate_audio 随各自的模型身份求值。
            video_identity: dict[VideoCapability, tuple[str, str, str | None, bool]] = {}
            for capability in _VIDEO_BUCKETS:
                try:
                    resolved_video = await r.resolve_video_backend(project_data, None, capability=capability)
                    bucket_provider, bucket_model = resolved_video.provider_id, resolved_video.model_id
                except Exception:
                    bucket_provider, bucket_model = "unknown", "unknown"
                # 有效 generate_audio 是 backend 实现内知识，此处只消费解析结果、不自行推断。
                bucket_audio = await r.video_pricing_generate_audio(bucket_provider, bucket_model, project_data)
                try:
                    bucket_resolution = await r.resolve_resolution(project_data, bucket_provider, bucket_model or "")
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
                PricingParams(call_type="image", model=image_model, resolution="1K"),
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
                    PricingParams(call_type="image", model=image_model, resolution="2K"),
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

        content_mode = project_data.get("content_mode", "narration")
        # 惰性解析、按能力桶各至多一次：只有项目里真出现按 unit 计费的参考视频集时才触发这次
        # 额外 IO（见 :func:`server.services.reference_video_tasks.resolve_project_duration_context`）。
        # unit 按声明的参考集分桶取档 / 计价（无参考图退化镜头 → i2v 桶模型），i2v 桶的 ctx
        # 只在真出现退化 unit 时才解析。
        duration_ctxs: dict[VideoCapability, ProjectDurationContext] = {}

        async def _duration_ctx(bucket: VideoCapability) -> ProjectDurationContext:
            if bucket not in duration_ctxs:
                duration_ctxs[bucket] = await resolve_project_duration_context(project_data, capability=bucket)
            return duration_ctxs[bucket]

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

        # 参考生视频路径跳过分镜步骤，分镜图维度按路径显式跳过；视频估值按实际计费颗粒度
        # （reference_unit，而非镜头）计算。两条子路径的展示颗粒度不同：ad 的 unit 只是
        # 镜头分组索引，费用要摊回自带 ``shot_id`` 的成员镜头（``_estimate_ad_reference_video_episode``）；
        # narration/drama 的 unit 自带 ``unit_id`` 且成员 shot 无独立 ID，unit 本身即展示
        # 颗粒度（``_estimate_unit_reference_video_episode``）。
        #
        # 生成路径以项目路线为唯一真相源，整个项目同一条路线、逐集不变（剧本不携带路线信息）；
        # 参考路线内的定桶再按 unit 声明的参考集逐 unit 分流，与执行侧同口径。
        is_reference_video = is_reference_video_project(project_data)

        for ep_meta in episodes_meta:
            script_file = ep_meta.get("script_file", "")
            script = scripts.get(script_file)
            if not script:
                continue

            raw_units = script.get("video_units")
            video_units: list[Any] = raw_units if isinstance(raw_units, list) else []
            estimate_by_unit = is_reference_video

            if estimate_by_unit:
                if content_mode == "ad":
                    segments_result, ep_est, ep_act = await self._estimate_ad_reference_video_episode(
                        script=script,
                        episode=ep_meta.get("episode"),
                        get_duration_ctx=_duration_ctx,
                        video_pricing=video_pricing,
                        actual_by_segment=actual_by_segment,
                        claimed_actual=claimed_actual,
                    )
                else:
                    segments_result, ep_est, ep_act = await self._estimate_unit_reference_video_episode(
                        units=video_units,
                        get_duration_ctx=_duration_ctx,
                        video_pricing=video_pricing,
                        actual_by_segment=actual_by_segment,
                        claimed_actual=claimed_actual,
                    )
                _accumulate_episode(ep_meta, segments_result, ep_est, ep_act)
                continue

            # 算价的桶与执行同源：分镜路径入队 i2v 桶；参考视频路径按 unit 声明的参考集
            # 逐 unit 分桶（有参考图 → r2v，无参考图退化镜头 → i2v），在上方两条 unit
            # 估算路径内完成。
            episode_video = video_pricing["i2v"]

            try:
                raw_segments, id_key, _, _, _ = get_storyboard_items(script)
            except ScriptEditError as exc:
                # 单集脏脚本(segments/scenes 键损坏)不应让整个项目费用估算 5xx;降级把该集
                # 估算为 0(raw_segments=[]) + warning 让运维知道,UI 仍能展示其他正常集的估算。
                logger.warning("费用估算跳过脏脚本 %s: %s", script_file, exc)
                raw_segments, id_key = [], "segment_id"

            # Grid 模式：预计算每个 segment 的图片分摊费用
            grid_cost_per_segment: dict[str, tuple[float, str]] = {}
            if grid_enabled and grid_image_unit_cost:
                groups = group_scenes_by_segment_break(raw_segments, id_key)
                for group in groups:
                    n = len(group)
                    layout = calculate_grid_layout(n, aspect_ratio)
                    if layout is None:
                        continue
                    grid_count = math.ceil(n / layout.cell_count) if n > layout.cell_count else 1
                    per_scene_cost = round(grid_image_unit_cost[0] * grid_count / n, 6)
                    for seg in group:
                        grid_cost_per_segment[seg.get(id_key, "")] = (per_scene_cost, grid_image_unit_cost[1])

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

                if grid_enabled and seg_id in grid_cost_per_segment:
                    cost_amount, cost_currency = grid_cost_per_segment[seg_id]
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

                # 旁白配音按 novel_text 字符数估价（仅说书模式 segment 携带原文）
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

    async def _estimate_ad_reference_video_episode(
        self,
        *,
        script: dict[str, Any],
        episode: int | None,
        get_duration_ctx: Callable[[VideoCapability], Awaitable[ProjectDurationContext]],
        video_pricing: dict[VideoCapability, _VideoPricing],
        actual_by_segment: ActualBySegment,
        claimed_actual: set[tuple[str, str]],
    ) -> tuple[list[dict[str, Any]], dict[str, CostBreakdown], dict[str, CostBreakdown]]:
        """ad + reference_video 集的估值：计费颗粒度是 unit，展示颗粒度是 shot。

        ad 骨架恒为 shots（ADR-0033），但 reference_video 路径把镜头派生分组为 unit 后
        按 unit 整体送 provider（``execute_reference_video_task``），一个 unit 只申请一次
        取档后的秒数，不是逐镜头分别计费——估值必须按 unit 取档后计算，否则「按镜头合计」
        与「按 unit 取档」在档位向上/向下取整时永远对不上。

        算出的 unit 费用再均摊回成员镜头输出，与 grid 模式「按 grid 计费、分摊到 scene
        展示」的口径同构：前端按镜头 ID 索引 segment（``frontend/src/stores/cost-store.ts``），
        输出 unit ID 会让镜头面板查不到费用。除不尽的余数补给末镜，保证分摊后的合计与
        unit 原值分文不差。视频实付按 unit 分摊（读 ``actual_by_segment[unit_id]``，
        ``lib/media_generator.py`` 对 ``resource_type == "reference_videos"`` 的记账
        以 unit_id 写入 usage 的 segment_id，与本函数的分摊口径一致），与 shot_id 记账的
        历史视频实付合并而非互相替换：项目若曾在 storyboard 模式为该镜头生成过视频、之后
        切到 reference_video，这笔旧支出与新 unit 的分摊额都要计入。图片/音频实付同样按
        shot_id 原样回填（不受 unit 分摊影响）：切换模式前产生的镜头图/配音费用仍需展示。

        取档与计价按成员镜头现算的参考集分桶（``reference_unit_video_bucket``）：有参考图
        → r2v 桶模型，无参考图退化 unit → i2v 桶模型，与执行侧定桶同口径。

        分组优先读剧本已持久化的 ``reference_units``（与执行时同一份索引，见
        ``lib.reference_video.ad_units.sync_ad_reference_units``）；用户尚未打开过参考
        视频面板/触发过入队、索引还未派生时，按纯函数现推一份仅用于估算、不写回剧本——
        时长上限按项目路线桶（r2v）解析，与真实派生（``resolve_max_unit_duration`` 按项目
        路线定桶）同约束，真实分组仍以执行时派生结果为准。

        无图片/音频估值维度：ad 参考视频不产生分镜图（跳过分镜步骤）、镜头口播文案不产生
        旁白配音估值（同 storyboard 模式口径，见 ``test_ad_voiceover_does_not_produce_audio_estimate``）。
        实付维度不受此限——见上段。
        """
        units = script.get("reference_units")
        if not isinstance(units, list) or not units:
            units = derive_ad_reference_units(
                script.get("shots"),
                episode=episode or 0,
                max_unit_duration=(await get_duration_ctx("r2v")).max_duration,
            )

        segments_result: list[dict[str, Any]] = []
        ep_est: dict[str, CostBreakdown] = {}
        ep_act: dict[str, CostBreakdown] = {}

        for unit in units:
            if not isinstance(unit, dict):
                continue
            # ad 的 unit_id 由 derive_ad_reference_units 内部拼接（f"E{episode}U{n}"），
            # 恒为字符串，不是 agent/外部可裸写的剧本字段，无需像 narration/drama 路径那样
            # 防非字符串 unit_id（见 _estimate_unit_reference_video_episode 同名判断的说明）。
            unit_id = str(unit.get("unit_id") or "")
            try:
                ad_shots = resolve_ad_unit_shots(script, unit)
            except ValueError as exc:
                # 分组索引过期（镜头被删/改 ID 后未重新派生）：估算对此不可恢复，该 unit
                # 整体跳过 + 日志，不让整个项目费用估算失败。成员镜头无从解析，也就无处
                # 分摊——按 0 估值硬造一条记录只会展示一笔查无实据的费用。
                logger.debug("费用估算跳过过期的 reference_unit %s: %s", unit_id, exc)
                continue
            if not ad_shots:
                continue

            bucket = reference_unit_video_bucket(unit, ad_shots=ad_shots)
            # 不需要 narration/drama 路径的逐 unit 脏时长容错：ad 的 unit 时长经
            # ad_script_total_duration 求和，该函数对脏数据按 0 计、不抛（全脏回退
            # FALLBACK_UNIT_DURATION），precheck_unit 在 ad 分支没有 unit 级脏数据
            # 异常可捕；悬空分组索引已在上方 resolve_ad_unit_shots 处按 unit 跳过。
            slot = precheck_unit(await get_duration_ctx(bucket), unit, ad_shots)

            est_video = _estimate_unit_video_cost(
                unit_id=unit_id,
                duration_seconds=slot.seconds,
                video=video_pricing[bucket],
            )

            act_video: CostBreakdown = _claim_actual(
                actual_by_segment,
                claimed_actual,
                unit_id,
                ("video",),
            ).get("video", {})
            est_by_shot = _split_cost_across(est_video, len(ad_shots))
            act_by_shot = _split_cost_across(act_video, len(ad_shots))

            for idx, shot in enumerate(ad_shots):
                shot_est = est_by_shot[idx]
                shot_id = shot.get("shot_id", "")
                # 模式切换前（storyboard 阶段）按 shot_id 记的镜头图/视频/音频实付仍要展示，
                # 不因当前是 reference_video 模式而清空——那是用户已经花掉的真实费用。video
                # 维度与 unit 分摊额合并而非互相替换：同一镜头可能既有切换前的历史视频调用
                # （shot_id 记账），也有切换后的 unit 调用（unit_id 分摊），两者都是真实支出。
                shot_actual = _claim_actual(actual_by_segment, claimed_actual, shot_id)
                shot_act_image = shot_actual.get("image", {})
                shot_act_audio = shot_actual.get("audio", {})
                shot_act_video = _merge_breakdowns(act_by_shot[idx], shot_actual.get("video", {}))
                segments_result.append(
                    {
                        "segment_id": shot_id,
                        # 展示镜头自身的编排时长：取档发生在 unit 级，摊不回单个镜头
                        "duration_seconds": shot.get("duration_seconds", 8),
                        "estimate": {"image": {}, "video": shot_est, "audio": {}},
                        "actual": {"image": shot_act_image, "video": shot_act_video, "audio": shot_act_audio},
                    }
                )
                ep_est["video"] = _merge_breakdowns(ep_est.get("video", {}), shot_est)
                for cost_type, amounts in (
                    ("image", shot_act_image),
                    ("video", shot_act_video),
                    ("audio", shot_act_audio),
                ):
                    ep_act[cost_type] = _merge_breakdowns(ep_act.get(cost_type, {}), amounts)

        return segments_result, ep_est, ep_act

    async def _estimate_unit_reference_video_episode(
        self,
        *,
        units: list[Any],
        get_duration_ctx: Callable[[VideoCapability], Awaitable[ProjectDurationContext]],
        video_pricing: dict[VideoCapability, _VideoPricing],
        actual_by_segment: ActualBySegment,
        claimed_actual: set[tuple[str, str]],
    ) -> tuple[list[dict[str, Any]], dict[str, CostBreakdown], dict[str, CostBreakdown]]:
        """narration/drama + reference_video 集的估值：unit 本身就是展示颗粒度，无需分摊。

        与 ad 路径不同——``AdShot`` 自带 ``shot_id``，``reference_units`` 只是把镜头分组的
        轻量索引，unit 费用要摊回成员镜头才能被前端按镜头 ID 查到；narration/drama 的
        ``video_units[*].shots``（``Shot``）没有独立 ID，unit 本身就是最小可寻址单位，
        前端画布与费用面板均按 ``unit_id`` 索引（见 ``ReferenceVideoCanvas`` 读
        ``cost-store`` 的 ``_segmentIndex.get(unit.unit_id)``），故此处不需要
        ``_split_cost_across`` 这一步。

        取档口径与 ad 路径同构：按 unit 声明的参考集定桶（有参考图 → r2v，无参考图退化
        unit → i2v，与执行侧同口径）解析该桶模型的能力，对 unit 剧本时长
        （``unit.duration_seconds``，unit 级单一真相）取档后按同桶模型计费，而非原始剧本
        时长——与 ``execute_reference_video_task`` 实际申请的秒数对齐。

        无图片/音频估值维度：该模式跳过分镜步骤（无分镜图），``Shot`` 没有独立的旁白/口播
        文案字段可供计价，同 ad 参考视频口径（见 ``_estimate_ad_reference_video_episode``
        docstring）。实付按 ``actual_by_segment[unit_id]`` 三个维度原样透传——``lib/media_generator.py``
        对 ``resource_type == "reference_videos"`` 的记账以 unit_id 写入 usage 的 segment_id，
        与本函数的输出 identity 一致。切换模式前按分镜 ID（``E1S1`` 等）记的历史支出不在此
        呈现：unit 与分镜之间没有映射关系，无处归属。

        没有 shots 或拼接文本为空的 unit 不产生预估：这类 unit 不可入队（``enqueue_videos.py::_reference_unit_spec``
        对没有 shots 的 unit 直接拒绝、``TaskSpec.from_request`` 对空提示词同样拒绝），估值给出
        非零金额会展示一笔查无实据的费用；判空标准与入队侧同一份 ``assemble_shots_text``，
        不能自行另起一套字符串处理否则两处会漂移。但该 unit 仍要整条保留、纳入汇总——不可入队
        只影响能否产生新预估，不影响该 unit 是否曾经成功生成过（``actual_by_segment[unit_id]``
        记的是历史实付，与 unit 当前编辑状态无关）：unit 曾成功生成、随后剧本被编辑成空 shots，
        其历史支出不能因此从段级/集级/项目级合计里消失。
        """
        segments_result: list[dict[str, Any]] = []
        ep_est: dict[str, CostBreakdown] = {}
        ep_act: dict[str, CostBreakdown] = {}

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

            # shots 非 list（如裸写 "shots": true/1）会让 assemble_shots_text 的遍历抛
            # TypeError；先做类型检查再拼接，与下方 duration 解析同样不能让单条脏数据中断
            # 整次估算。
            shots = unit.get("shots")
            enqueueable = isinstance(shots, list) and bool(shots) and bool(assemble_shots_text(shots).strip())

            est_video: CostBreakdown = {}
            if enqueueable:
                # agent/外部编辑过的剧本可能写入非数值 duration_seconds（如 "bad"/列表/字典）；
                # SDK 侧入队预检（enqueue_videos.py）对每个 unit 单独 catch ValueError 跳过，
                # 此处须跟随同一容错口径——否则一个 unit 的脏时长会让整个项目估算 500，
                # 拖累其余正常集。int() 转换对非数值字符串抛 ValueError，对 list/dict 抛
                # TypeError，两者都要接住。ctx 解析在 try 外：能力配置错误是项目级问题，
                # 须 fail loud 上抛，不得被当成单 unit 脏时长静默跳过（SDK 预检同口径——
                # 解析在批次层、逐 unit 只 catch 取档）。
                bucket = reference_unit_video_bucket(unit)
                duration_ctx = await get_duration_ctx(bucket)
                try:
                    slot = precheck_unit(duration_ctx, unit, None)
                except (ValueError, TypeError):
                    logger.warning("费用估算跳过时长非法的 unit %s", unit_id, exc_info=True)
                    slot = None
                if slot is not None:
                    est_video = _estimate_unit_video_cost(
                        unit_id=unit_id,
                        duration_seconds=slot.seconds,
                        video=video_pricing[bucket],
                    )

            unit_actual = _claim_actual(actual_by_segment, claimed_actual, unit_id)
            act_image: CostBreakdown = unit_actual.get("image", {})
            act_video: CostBreakdown = unit_actual.get("video", {})
            act_audio: CostBreakdown = unit_actual.get("audio", {})

            segments_result.append(
                {
                    "segment_id": unit_id,
                    "duration_seconds": unit.get("duration_seconds", 8),
                    "estimate": {"image": {}, "video": est_video, "audio": {}},
                    "actual": {"image": act_image, "video": act_video, "audio": act_audio},
                }
            )
            ep_est["video"] = _merge_breakdowns(ep_est.get("video", {}), est_video)
            for cost_type, amounts in (("image", act_image), ("video", act_video), ("audio", act_audio)):
                ep_act[cost_type] = _merge_breakdowns(ep_act.get(cost_type, {}), amounts)

        return segments_result, ep_est, ep_act
