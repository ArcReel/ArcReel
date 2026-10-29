"""费用估算服务 — 计算预估 + 汇总实际费用。"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.backends.backend_assembly.specs import builtin_video_metered_usage
from lib.backends.image_backends.base import ImageRequestFacts
from lib.backends.image_usage import estimate_builtin_image_usage
from lib.billing.catalog_pricing import is_builtin_provider, price_builtin_call
from lib.billing.metering import Priced, Usage
from lib.billing.multipliers import get_provider_multiplier
from lib.config.resolver import (
    ConfigResolver,
    VideoGenerationType,
    video_bucket_for_generation_mode,
)
from lib.custom_provider.metering import CustomPriceSource, load_custom_price_source
from lib.db.repositories.custom_provider_repo import CustomModelPricing
from lib.db.repositories.usage_repo import PROJECT_LEVEL_SEGMENT_KEY, UsageRepository
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    VideoRequestCostFacts,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    evaluate_video_request_facts,
)
from lib.infra.schema_guards import is_int
from lib.project.asset_types import ASSET_SPECS
from lib.project.project_manager import grid_storyboard_enabled, is_reference_video_project
from lib.script.grid.grid_resolution import grid_image_size, resolve_image_resolution
from lib.script.grid.layout import large_grid_allowed, plan_grid_chunks
from lib.script.reference_video.artifact_selection import CurrentReferenceAssets
from lib.script.reference_video.request_projection import (
    MemoizedReferenceAssets,
    ProjectionProblem,
    ReferenceAssetAvailability,
    ReferenceExpansionCache,
    ReferenceRequestFactsLookup,
    ReferenceRequestOptions,
    ReferenceUnitHydration,
    ReferenceUnitRequestProjector,
    ResolvedReferenceAsset,
    configured_reference_request_facts,
    hydrate_unit_references,
    resolve_reference_assets,
    unit_reference_declarations,
)
from lib.script.script_editor import ScriptEditError
from lib.script.script_models import get_generated_assets
from lib.script.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from lib.speech.narration_config import USE_TTS, project_narration_delivery, project_tts_settings
from lib.speech.speech_composition import video_unit_replan_problems

logger = logging.getLogger(__name__)

CostBreakdown = dict[str, float]
ActualBySegment = dict[str, dict[str, CostBreakdown]]
#: 没有价格的预估项或调用，按 (call_type, provider, model) 计数。
UnpricedCounts = Counter[tuple[str, str, str]]
# 费用页展示的记账类型；text 类调用不写 segment_id，只会落在项目级汇总里。
ACTUAL_COST_TYPES = ("image", "video", "audio")


#: 读侧定桶要枚举的全部视频任务类型桶。分镜图生视频项目整体走 i2v 桶；参考生视频由公共
#: request projection 按每个 unit 当前实际可用资产分桶。两个桶都在这里预解析，省去按
#: 生成模式与分镜分支判断该解析哪个桶的复杂度——桶只有两个，代价有界。
_VIDEO_BUCKETS: tuple[VideoGenerationType, ...] = ("i2v", "r2v")

#: 剧本条目 ``generated_assets`` 里代表已生成媒体的字段，用于识别「有产物却没有本机调用记录」。
_GENERATED_MEDIA_FIELDS = ("storyboard_image", "video_clip", "narration_audio")
_SCRIPT_ITEM_KEYS = ("segments", "scenes", "shots", "video_units")


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
    price: VideoPriceSource


@dataclass(frozen=True, slots=True)
class BatchCostEstimate:
    """一整批生成的预估费用：只合计已定价的部分，另数出算不出价的项数。

    ``total`` 按币种分开；``unpriced_count`` 不为 0 时，``total`` 低于这批的真实费用，界面据此提示，
    不把偏低的合计当作整批的价。
    """

    total: CostBreakdown
    unpriced_count: int

    def to_payload(self) -> dict[str, object]:
        """批量预览端点里的两个字段；没有任何已定价的项时 ``estimated_cost`` 为 ``None``。"""

        return {"estimated_cost": self.total or None, "unpriced_count": self.unpriced_count}


@dataclass(frozen=True, slots=True)
class VideoRequestQuote:
    """一次视频请求按当前价格的报价；``amount`` 为 ``None`` 表示未定价，原因见 ``unpriced_reason``。"""

    amount: float | None
    currency: str | None
    provider_id: str
    model_id: str
    request_duration_seconds: int
    unpriced_reason: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "amount": self.amount,
            "currency": self.currency,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "request_duration_seconds": self.request_duration_seconds,
            "unpriced_reason": self.unpriced_reason,
        }


@dataclass(frozen=True, slots=True)
class VideoPriceSource:
    """视频报价要读库的两项：供应商倍率，以及自定义供应商模型的报价来源（内置供应商为 ``None``）。"""

    custom: CustomPriceSource | None
    provider_multiplier: float


async def load_video_price_source(session: AsyncSession, provider_id: str, model_id: str) -> VideoPriceSource:
    """读一个视频模型报价所需的价格来源，与结算同一组读取。"""
    return VideoPriceSource(
        custom=await load_custom_price_source(session, provider_id, model_id),
        provider_multiplier=await get_provider_multiplier(session, provider_id),
    )


def quote_video_request_from_price(facts: VideoRequestCostFacts, source: VideoPriceSource) -> VideoRequestQuote:
    """按与结算同一个目录查询与计算器给一次视频请求报价。

    内置供应商由调用通道按用量契约给出这次请求的计量（需要换算的计量项用通道的估算函数），再交模型目录的
    价目表计价；有计量项既不能从请求事实得出、也没有估算函数时记为未定价。自定义供应商的模型按所对应条目的
    价目表计价，计量取调用端点的声明或通用口径，对应不上条目时记为未定价。
    """
    if is_builtin_provider(facts.provider_id):
        cost = price_builtin_call(
            provider=facts.provider_id,
            model=facts.model_id,
            media_type="video",
            requests=(builtin_video_metered_usage(facts.provider_id, facts.model_id, facts.provider_request()),),
            provider_multiplier=source.provider_multiplier,
            estimate=True,
        )
    else:
        custom = source.custom or CustomPriceSource(CustomModelPricing())
        cost = custom.price(
            model=facts.model_id,
            media_type="video",
            requests=(custom.video_usage(facts.model_id, facts.provider_request()),),
            provider_multiplier=source.provider_multiplier,
            estimate=True,
        )
    if isinstance(cost, Priced):
        amount, currency, reason = round(cost.amount, 6), cost.currency, None
    else:
        amount, currency, reason = None, None, str(cost.reason)
    return VideoRequestQuote(
        amount=amount,
        currency=currency,
        provider_id=facts.provider_id,
        model_id=facts.model_id,
        request_duration_seconds=facts.duration_seconds,
        unpriced_reason=reason,
    )


async def quote_video_request(
    facts: VideoRequestCostFacts,
    session_factory: async_sessionmaker[AsyncSession],
) -> VideoRequestQuote | None:
    """读取当前价格来源，给一次视频请求报价；读库失败或调用通道认不出请求时返回 ``None``。"""

    try:
        async with session_factory() as session:
            source = await load_video_price_source(session, facts.provider_id, facts.model_id)
        return quote_video_request_from_price(facts, source)
    except (SQLAlchemyError, ValueError):
        logger.warning(
            "无法为 current video request 计算精确费用 provider=%s model=%s duration=%s",
            facts.provider_id,
            facts.model_id,
            facts.duration_seconds,
            exc_info=True,
        )
        return None


def _storyboard_aspect_ratio(project: dict[str, Any]) -> str:
    """分镜图的比例：``aspect_ratio`` 可能是 str 或按资源类型分的 dict，复用生成任务的解析口径。"""
    raw_ar = project.get("aspect_ratio")
    if isinstance(raw_ar, str):
        return raw_ar
    if isinstance(raw_ar, dict):
        return raw_ar.get("storyboards", "9:16")
    # narration/ad 默认竖屏，drama（含未知值的历史兜底）默认横屏
    return "9:16" if project.get("content_mode", "narration") in {"narration", "ad"} else "16:9"


def _quote_image(
    provider: str,
    model: str,
    price: CustomPriceSource | None,
    *,
    multiplier: float,
    image_size: str | None,
    aspect_ratio: str,
    reference_count: int | None,
) -> tuple[float, str] | None:
    """一张图的预估费用；未定价时返回 ``None``。

    内置供应商与结算同一个目录查询与计算器，用量由调用通道按请求事实推出；``image_size`` 是执行时会下发的
    分辨率档，没有就不下发，不另设计价保底档。自定义供应商（``price`` 是它的报价来源）按所对应条目的价目表
    计价，用量取调用端点的预估声明或通用口径。
    """
    facts = ImageRequestFacts(
        model=model, aspect_ratio=aspect_ratio, image_size=image_size, reference_count=reference_count
    )
    if is_builtin_provider(provider):
        try:
            usage = estimate_builtin_image_usage(provider, facts)
        except ValueError:
            logger.debug("无法推出 image 预估用量 provider=%s model=%s", provider, model, exc_info=True)
            return None
        cost = price_builtin_call(
            provider=provider,
            model=model,
            media_type="image",
            requests=(usage,),
            provider_multiplier=multiplier,
            estimate=True,
        )
        return (cost.amount, cost.currency) if isinstance(cost, Priced) else None
    if price is None:
        return None
    cost = price.price(
        model=model,
        media_type="image",
        requests=(price.image_usage(facts),),
        provider_multiplier=multiplier,
        estimate=True,
    )
    return (cost.amount, cost.currency) if isinstance(cost, Priced) else None


def _has_generated_media(project: dict[str, Any], scripts: dict[str, dict[str, Any]]) -> bool:
    """项目里是否已有生成出的资产图、分镜图、视频或旁白配音。"""
    for spec in ASSET_SPECS.values():
        bucket = project.get(spec.bucket_key)
        if isinstance(bucket, dict) and any(
            isinstance(asset, dict) and asset.get(spec.sheet_field) for asset in bucket.values()
        ):
            return True
    for script in scripts.values():
        for key in _SCRIPT_ITEM_KEYS:
            items = script.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and any(
                    get_generated_assets(item).get(field) for field in _GENERATED_MEDIA_FIELDS
                ):
                    return True
    return False


def _unpriced_payload(counts: UnpricedCounts, provider_names: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "call_type": call_type,
            "provider": provider,
            "provider_name": provider_names.get(provider, provider),
            "model": model,
            "count": count,
        }
        for (call_type, provider, model), count in sorted(counts.items())
    ]


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
    若每份都独立 round，误差会随分镜数放大到用户可见的总价上。
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

    async def compute(
        self,
        project_data: dict[str, Any],
        scripts: dict[str, dict[str, Any]],
        *,
        project_name: str,
    ) -> dict[str, Any]:
        episodes_meta = project_data.get("episodes", [])
        is_reference_video = is_reference_video_project(project_data)

        # Resolve current model config（共享单一 session）。估价以 T2I 为准（T2I/I2I 是正交能力槽，
        # T2I 缺失不应回落 I2I —— 那会拿错误能力的价目算费用）。
        # image/video 的项目覆盖优先级由 ConfigResolver 统一解析，与执行路径共用同一套
        # payload>project>全局默认 链路，此处 payload 传 None（预估无历史任务 payload 可排空）。
        request_facts_lookup = configured_reference_request_facts(project_data, self._resolver)
        reference_facts: dict[VideoGenerationType, VideoRequestFacts] = {}
        if is_reference_video:
            for generation_type in _VIDEO_BUCKETS:
                # 真正使用该 bucket 的 unit 会由 projector 返回结构化 blocker；未使用 bucket
                # 的配置问题不应拖垮整份费用页。
                evaluated = await request_facts_lookup(generation_type)
                if isinstance(evaluated, VideoRequestFacts):
                    reference_facts[generation_type] = evaluated
        async with self._resolver.session() as r:
            storyboard_facts = (
                await evaluate_video_request_facts(
                    project_data,
                    route="storyboard",
                    generation_type="i2v",
                    identity=CONFIGURED_VIDEO_IDENTITY,
                    resolver=r,
                )
                if not is_reference_video
                else None
            )
            try:
                resolved_image = await r.resolve_image_backend(project_data, None, generation_type="t2i")
                image_provider, image_model = resolved_image.provider_id, resolved_image.model_id
            except (ValueError, SQLAlchemyError):
                image_provider, image_model = "unknown", "unknown"

            # T2I 槽分辨率档：与路由入队、Agent 工具共用 ``grid_resolution`` 的取档，估算的宫格
            # 张数才不会与实际入队张数漂移；同一档位又是两路分镜图的计价档——宫格图未配置时按
            # ``grid_image_size`` 取保底档（与 ``execute_grid_task`` 下发的档位同源），普通
            # 分镜图未配置时与执行侧一样不下发分辨率，按价目表的默认档计。解析在两路之前，宫格
            # 与非宫格项目共用这一次 IO。计价与执行取的是同一个 T2I 槽、同一套项目配置，但身份
            # 键不同（此处 registry ``model_id``，执行侧构造后的 backend 型号），两者分叉的供应
            # 商上估价档位可能与实际渲染档位不一致。
            image_resolution = await resolve_image_resolution(r, project_data)
            grid_allow_large = large_grid_allowed(image_resolution)

            # 视频按任务类型桶解析（``docs/adr/0054``），与执行扣费同一个模型：图生视频 / 宫格算
            # i2v 桶的价；参考生视频逐 unit 水合当前资产后分桶。两个桶都在这里
            # 解析出来（见 ``_VIDEO_BUCKETS``），分辨率与
            # generate_audio 随各自的模型身份求值。
            video_identity: dict[VideoGenerationType, tuple[str, str, str | None, bool]] = {}
            for generation_type in _VIDEO_BUCKETS:
                bucket_facts = reference_facts.get(generation_type)
                if generation_type == "i2v" and isinstance(storyboard_facts, VideoRequestFacts):
                    bucket_facts = storyboard_facts
                if bucket_facts is not None:
                    bucket_provider = bucket_facts.provider_id
                    bucket_model = bucket_facts.model_id
                    bucket_resolution = bucket_facts.resolution
                    bucket_audio = bucket_facts.generate_audio
                else:
                    try:
                        resolved_video = await r.resolve_video_backend(
                            project_data, None, generation_type=generation_type
                        )
                        bucket_provider, bucket_model = resolved_video.provider_id, resolved_video.model_id
                    except (ValueError, SQLAlchemyError):
                        bucket_provider, bucket_model = "unknown", "unknown"
                    # 事实解析不出的桶只保留模型展示坐标；分镜按分镜返回阻断问题，参考生视频逐 unit
                    # 的阻断由 request projector 给出，两者都不产生视频报价。
                    bucket_audio = await r.video_pricing_generate_audio(bucket_provider, bucket_model, project_data)
                    try:
                        bucket_resolution = await r.resolve_resolution(
                            project_data,
                            bucket_provider,
                            bucket_model or "",
                        )
                    except (ValueError, SQLAlchemyError):
                        bucket_resolution = None
                video_identity[generation_type] = (
                    bucket_provider,
                    bucket_model,
                    bucket_resolution,
                    bucket_audio,
                )

            # 旁白配音（TTS）模型取项目快照，不读全局默认；后期配音项目或没有快照时回落 unknown，
            # 该维度预估为空
            tts_settings = (
                project_tts_settings(project_data) if project_narration_delivery(project_data) == USE_TTS else None
            )
            audio_provider, audio_model = (
                (tts_settings.provider_id, tts_settings.model_id)
                if tts_settings is not None
                else ("unknown", "unknown")
            )

        # Get actual costs + 自定义供应商模型的报价来源（与实际记账同源预查 DB）
        async with self._session_factory() as session:
            actual_by_segment = await UsageRepository(session).get_actual_costs_by_segment(project_name)
            image_price = await load_custom_price_source(session, image_provider, image_model)
            image_multiplier = await get_provider_multiplier(session, image_provider)
            audio_price = await load_custom_price_source(session, audio_provider, audio_model)
            audio_multiplier = await get_provider_multiplier(session, audio_provider)
            # 两个桶常解析到同一个模型，按身份去重后再查单价，不重复打 DB。
            video_prices: dict[tuple[str, str], VideoPriceSource] = {}
            for bucket_provider, bucket_model, _, _ in video_identity.values():
                if (bucket_provider, bucket_model) not in video_prices:
                    video_prices[(bucket_provider, bucket_model)] = await load_video_price_source(
                        session, bucket_provider, bucket_model
                    )

        video_pricing: dict[VideoGenerationType, _VideoPricing] = {
            generation_type: _VideoPricing(
                provider=bucket_provider,
                model=bucket_model,
                resolution=bucket_resolution,
                generate_audio=bucket_audio,
                price=video_prices[(bucket_provider, bucket_model)],
            )
            for generation_type, (
                bucket_provider,
                bucket_model,
                bucket_resolution,
                bucket_audio,
            ) in video_identity.items()
        }
        # 项目层展示的视频模型按项目 generation_mode 定桶：``models`` 回答的是「当前项目配置」
        # 的生成模式主桶；参考生视频内无参考图视频单元的逐 unit 降级计价在集级估算路径内完成，不改变项目层
        # 展示身份。
        project_video = video_pricing[video_bucket_for_generation_mode(project_data.get("generation_mode"))]

        grid_enabled = grid_storyboard_enabled(project_data)
        aspect_ratio = _storyboard_aspect_ratio(project_data)
        # 视频请求发出的画面比例，与生成任务同一个解析；方舟按它换算视频 token
        video_aspect_ratio = resolve_video_aspect_ratio(project_data)

        # 预计算普通分镜图单价（按配置的分辨率档，估价以文生图为准，不带参考图）。宫格图按每张宫格的比例
        # 在集内逐张估价，分辨率未配置时与执行侧同经 ``grid_image_size`` 取档。
        image_unit_cost = _quote_image(
            image_provider,
            image_model,
            image_price,
            multiplier=image_multiplier,
            image_size=image_resolution,
            aspect_ratio=aspect_ratio,
            reference_count=0,
        )
        grid_quotes: dict[str, tuple[float, str] | None] = {}

        def _grid_quote(grid_aspect_ratio: str) -> tuple[float, str] | None:
            if grid_aspect_ratio not in grid_quotes:
                grid_quotes[grid_aspect_ratio] = _quote_image(
                    image_provider,
                    image_model,
                    image_price,
                    multiplier=image_multiplier,
                    image_size=grid_image_size(image_resolution, image_provider, image_model),
                    aspect_ratio=grid_aspect_ratio,
                    reference_count=0,
                )
            return grid_quotes[grid_aspect_ratio]

        episodes_result = []
        proj_est: dict[str, CostBreakdown] = {}
        # 应当有预估、却因模型没有价格而没算进去的项；与实际侧一起让前端区分「0」与「未知」
        estimate_unpriced: UnpricedCounts = Counter()
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

        # 参考生视频路径跳过分镜步骤，所有创作类型都按自包含 reference_unit 计费与展示。
        #
        # 生成路径以项目生成模式为唯一真相源，整个项目同一种生成模式、逐集不变（剧本不携带生成模式信息）；
        # 参考生视频内的定桶再由 request projection 按当前资产逐 unit 分流。
        availability: ReferenceAssetAvailability = _AssumeResolvedAssetsAvailable()
        expansion: ReferenceExpansionCache | None = None
        if is_reference_video and self._project_path is not None:
            # 整次估算共用一份判定：构造时读一份清单快照，只读，放进工作线程。
            current = await asyncio.to_thread(CurrentReferenceAssets, self._project_path, project_data)
            availability = MemoizedReferenceAssets(current)
            # 引用目录与候选路径解析同样整次估算共用一份，不随单元数重复构造。
            expansion = ReferenceExpansionCache(project_data, self._project_path)

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
                    script_file=script_file,
                    units=video_units,
                    availability=availability,
                    expansion=expansion,
                    request_facts_lookup=request_facts_lookup,
                    video_prices=video_prices,
                    actual_by_segment=actual_by_segment,
                    claimed_actual=claimed_actual,
                    unpriced=estimate_unpriced,
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

            # 宫格装配：预计算每个 segment 的图片分摊费用。份额以条目在 ``raw_segments`` 中的
            # 位置为身份，与下方实付均摊同口径（理由见该处）；分组由
            # ``group_scenes_by_segment_break`` 按顺序切出、连续且不重不漏，故位置即组内序号
            # 加上前序各组的长度。
            grid_cost_per_index: dict[int, tuple[float, str]] = {}
            grid_unpriced_indices: set[int] = set()
            if grid_enabled:
                group_offset = 0
                for group in group_scenes_by_segment_break(raw_segments, id_key):
                    n = len(group)
                    # 宫格张数与实际入队同源（plan_grid_chunks）：超上限分组按切块后的
                    # 张数计费，避免估算与执行漂移。
                    plans = plan_grid_chunks(group, aspect_ratio, allow_large_grid=grid_allow_large)
                    quotes = [_grid_quote(layout.grid_aspect_ratio) for _, layout in plans]
                    indices = range(group_offset, group_offset + n)
                    if quotes and all(quote is not None for quote in quotes):
                        priced = [quote for quote in quotes if quote is not None]
                        per_scene_cost = round(sum(amount for amount, _ in priced) / n, 6)
                        grid_cost_per_index.update(dict.fromkeys(indices, (per_scene_cost, priced[0][1])))
                    elif quotes:
                        grid_unpriced_indices.update(indices)
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
                    grid_actual_per_index.update(zip(indices, _split_cost_across(grid_cost, len(indices)), strict=True))

            segments_result = []
            ep_est: dict[str, CostBreakdown] = {}
            ep_act: dict[str, CostBreakdown] = {}

            for idx, seg in enumerate(raw_segments):
                seg_id = seg.get(id_key, "")
                duration = seg.get("duration_seconds", 8)

                est_image: CostBreakdown = {}
                est_video: CostBreakdown = {}
                est_audio: CostBreakdown = {}

                if idx in grid_cost_per_index:
                    cost_amount, cost_currency = grid_cost_per_index[idx]
                    _add_cost(est_image, cost_amount, cost_currency)
                elif idx in grid_unpriced_indices or image_unit_cost is None:
                    estimate_unpriced["image", image_provider, image_model] += 1
                else:
                    _add_cost(est_image, image_unit_cost[0], image_unit_cost[1])

                # 剧本上的秒数可能被外部编辑成非整数：单条脏数据只让该分镜没有视频报价，不进计价。
                if isinstance(storyboard_facts, VideoRequestFacts) and is_int(duration, minimum=1):
                    try:
                        video_quote = quote_video_request_from_price(
                            VideoRequestCostFacts(
                                storyboard_facts,
                                duration,
                                aspect_ratio=video_aspect_ratio,
                                has_first_frame=True,
                            ),
                            episode_video.price,
                        )
                        if video_quote.amount is None or video_quote.currency is None:
                            estimate_unpriced["video", storyboard_facts.provider_id, storyboard_facts.model_id] += 1
                        else:
                            _add_cost(est_video, video_quote.amount, video_quote.currency)
                    except ValueError:
                        logger.debug("无法计算 video 预估 for %s", seg_id, exc_info=True)

                # 旁白配音按 novel_text 估价（仅旁白/解说 segment 携带原文）
                novel_text = seg.get("novel_text")
                narration_text = novel_text.strip() if isinstance(novel_text, str) else ""
                narration_chars = len(narration_text)
                if narration_chars and tts_settings is not None:
                    # 与结算同一个目录查询与计算器：字符数按价目表的「汉字计 2」统计；按 token 计价的模型没有
                    # 估算函数，记为未定价。自定义供应商的模型按所对应条目的价目表计价。
                    audio_requests = (Usage(text=narration_text),)
                    audio_cost = (
                        audio_price.price(
                            model=audio_model,
                            media_type="audio",
                            requests=audio_requests,
                            provider_multiplier=audio_multiplier,
                            estimate=True,
                        )
                        if audio_price is not None
                        else price_builtin_call(
                            provider=audio_provider,
                            model=audio_model,
                            media_type="audio",
                            requests=audio_requests,
                            provider_multiplier=audio_multiplier,
                            estimate=True,
                        )
                    )
                    if isinstance(audio_cost, Priced):
                        _add_cost(est_audio, audio_cost.amount, audio_cost.currency)
                    else:
                        estimate_unpriced["audio", audio_provider, audio_model] += 1

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
                        **(
                            {
                                "request_projection": {
                                    "allowed": False,
                                    "problems": [
                                        ProjectionProblem.from_request_facts_failure(
                                            storyboard_facts, locations=(("video_provider_i2v",),)
                                        ).to_payload(unit_id=seg_id)
                                    ],
                                }
                            }
                            if isinstance(storyboard_facts, VideoRequestFactsFailure)
                            else {}
                        ),
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

        # Project-level actual costs (characters/scenes/props/products 资产图—— segment_id is null)
        async with self._session_factory() as session:
            usage_repo = UsageRepository(session)
            project_image_by_type = await usage_repo.get_project_image_costs_by_asset_type(project_name)
            # 成功调用里费用为 NULL 的即未定价；定价为零（0）的调用是真实的 0，不在其中。
            actual_unpriced: UnpricedCounts = Counter()
            for call_type, provider, model, calls in await usage_repo.get_unpriced_calls_by_model(project_name):
                actual_unpriced[call_type, provider, model] += calls
            provider_names = await usage_repo.provider_display_names(
                {provider for _, provider, _ in (*estimate_unpriced, *actual_unpriced)}
            )
            # 有产物却没有本机的媒体调用记录（如导入的项目、在别的机器上生成）：实际费用无从统计
            missing_local_calls = not await usage_repo.has_media_calls(project_name) and _has_generated_media(
                project_data, scripts
            )
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
            "unpriced": {
                "estimate": _unpriced_payload(estimate_unpriced, provider_names),
                "actual": _unpriced_payload(actual_unpriced, provider_names),
            },
            "missing_local_calls": missing_local_calls,
        }

    async def _estimate_unit_reference_video_episode(
        self,
        *,
        project_name: str,
        project: dict[str, Any],
        script_file: str,
        units: list[Any],
        availability: ReferenceAssetAvailability,
        expansion: ReferenceExpansionCache | None,
        request_facts_lookup: ReferenceRequestFactsLookup,
        video_prices: dict[tuple[str, str], VideoPriceSource],
        actual_by_segment: ActualBySegment,
        claimed_actual: set[tuple[str, str]],
        unpriced: UnpricedCounts,
    ) -> tuple[list[dict[str, Any]], dict[str, CostBreakdown], dict[str, CostBreakdown]]:
        """reference_video 集的估值：unit 本身就是展示与计费颗粒度。

        unit 本身就是最小可寻址单位，
        前端画布与费用面板均按 ``unit_id`` 索引（见 ``ReferenceVideoCanvas`` 读
        ``cost-store`` 的 ``_segmentIndex.get(unit.unit_id)``），故此处不需要
        ``_split_cost_across`` 这一步。

        取档先水合 unit 引用的当前可用图片（文件存在且产物清单认领，与准入、执行同判据；
        有图 → r2v，无图退化 unit → i2v），再解析该桶模型的能力；声明引用与实际资产分裂时返回
        结构化 blocker，不换桶伪报价。
        请求时长基准是 ``unit.duration_seconds``，按它取档后用同桶模型计费，与执行请求的秒数对齐；
        旁白交付方式不影响视频请求，也不影响这里的报价。

        无图片/音频估值维度：该模式跳过分镜步骤（无分镜图），unit 正文是一整段、没有可供
        独立音频计价的旁白/口播文案字段。实付按 ``actual_by_segment[unit_id]`` 三个维度原样透传——``lib/generation/media_generator.py``
        对 ``resource_type == "reference_videos"`` 的记账以 unit_id 写入 usage 的 segment_id，
        与本函数的输出 identity 一致。切换模式前按分镜 ID（``E1S1`` 等）记的历史支出不在此
        呈现：unit 与分镜之间没有映射关系，无处归属。

        正文为空或命中 ``video_unit_replan_problems`` 的 unit 不产生预估：这些 unit 会被
        ``video_batch_admission.reference_unit_task_spec`` 拒绝，估值给出非零金额会展示一笔查无实据的
        费用；判据与入队侧共用同一个正文与重规划问题模型，不能自行另起一套处理否则两处会漂移。但该 unit 仍要整条保留、纳入汇总——不可入队只影响能否产生新预估，不影响该
        unit 是否曾经成功生成过（``actual_by_segment[unit_id]`` 记的是历史实付，与 unit 当前编辑状态
        无关）：unit 曾成功生成、随后剧本被编辑成不可入队状态，其历史支出不能因此从段级/集级/项目级
        合计里消失。
        """
        segments_result: list[dict[str, Any]] = []
        ep_est: dict[str, CostBreakdown] = {}
        ep_act: dict[str, CostBreakdown] = {}

        projector = ReferenceUnitRequestProjector(request_facts_lookup, availability)

        def _screen_candidates() -> list[tuple[dict[str, Any], str, bool]]:
            # (unit, unit_id, 可入队)；先筛出有效 unit，水合只对可入队的做。
            candidates: list[tuple[dict[str, Any], str, bool]] = []
            for unit in units:
                if not isinstance(unit, dict):
                    continue
                # unit_id 必须原本就是字符串：入队执行时按该字符串与剧本原始（未转型）值比较定位
                # unit（``execute_reference_video_task``），数字/布尔等裸写 truthy 值 str() 后能通过
                # 这里的估算，但执行时永远因类型不等找不到 unit——估算不能展示一笔实际跑不起来的费用。
                raw_unit_id = unit.get("unit_id")
                if not isinstance(raw_unit_id, str) or not raw_unit_id:
                    continue
                # text 非字符串（如裸写 "text": true/1）同样不能让单条脏数据中断整次估算。
                text = unit.get("text")
                enqueueable = isinstance(text, str) and bool(text.strip()) and not video_unit_replan_problems(unit)
                candidates.append((unit, raw_unit_id, enqueueable))
            return candidates

        # Agent/外部编辑过的剧本可能写入非数值 duration_seconds（如 "bad"/列表/字典）；
        # 单个 unit 的无效内容不应让整个项目估算失败，因此资产解析、水合与 request projection
        # 的 ValueError/TypeError 只跳过该 unit。能力解析错误由 projector 转为结构化 blocker，
        # 正常保留在该 unit 的报价结果中。
        def _hydrate_episode() -> tuple[list[tuple[dict[str, Any], str, bool]], list[ReferenceUnitHydration | None]]:
            candidates = _screen_candidates()
            # 与 candidates 逐项对齐；不可入队或水合失败的位置为 None。
            hydrations: list[ReferenceUnitHydration | None] = []
            for unit, unit_id, enqueueable in candidates:
                hydration = None
                if not enqueueable:
                    hydrations.append(hydration)
                    continue
                try:
                    if self._project_path is None or expansion is None:
                        resolved_assets: Sequence[ResolvedReferenceAsset] = [
                            ResolvedReferenceAsset(
                                path=Path(f"{reference.type}/{reference.name}.png"),
                                reference=reference,
                            )
                            for reference in unit_reference_declarations(project, unit)
                        ]
                    else:
                        resolved_assets = resolve_reference_assets(
                            project, self._project_path, unit, expansion=expansion
                        )
                    hydration = hydrate_unit_references(
                        project,
                        unit,
                        resolved_assets,
                        availability,
                        catalog=expansion.catalog if expansion is not None else None,
                    )
                except (ValueError, TypeError):
                    logger.warning("费用估算跳过时长非法的 unit %s", unit_id, exc_info=True)
                hydrations.append(hydration)
            return candidates, hydrations

        # 逐 unit 解析台词与提及是大块 CPU 工作，展开候选与可用性判定碰文件系统；
        # 筛选与水合整集一次进工作线程，不按 unit 逐个排队。
        candidates, hydrations = await asyncio.to_thread(_hydrate_episode)

        for (unit, unit_id, enqueueable), hydration in zip(candidates, hydrations, strict=True):
            est_video: CostBreakdown = {}
            projection_problems: list[dict[str, Any]] = []
            projection = None
            if enqueueable:
                if hydration is not None:
                    try:
                        projection = await projector.project_hydrated(
                            project=project,
                            unit=unit,
                            hydration=hydration,
                            options=ReferenceRequestOptions(),
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
                        except ValueError:
                            logger.warning(
                                "无法为 reference unit %s 计算精确费用 provider=%s model=%s duration=%s",
                                unit_id,
                                cost.provider_id,
                                cost.model_id,
                                cost.duration_seconds,
                                exc_info=True,
                            )
                        else:
                            if priced_quote.amount is None or priced_quote.currency is None:
                                unpriced["video", cost.provider_id, cost.model_id] += 1
                            else:
                                _add_cost(est_video, priced_quote.amount, priced_quote.currency)

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
                            "capability": projection.hydrated_generation_type,
                            "problems": projection_problems,
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


ImageLane = Literal["t2i", "i2i"]


async def estimate_image_batch_cost(
    project: dict[str, Any],
    lanes: Sequence[ImageLane],
    *,
    aspect_ratio: str,
    resolver: ConfigResolver,
    session_factory: async_sessionmaker[AsyncSession],
) -> BatchCostEstimate:
    """一批图片生成按当前项目配置的预估费用：合计已定价的部分，并数出未定价的张数。

    每张图按自己的生图通道计价：文生图与图生图是两个正交的能力槽，可能配置成不同的模型。
    分辨率档与执行侧同样按通道解析出的模型取，取不到时不下发，按价目表的默认档计。``aspect_ratio`` 是这批
    图下发的比例（按尺寸计价的模型要用）。供应商解析不出、读库失败或算不出价的通道，它名下的每一张都
    计入未定价。
    """

    counts = Counter(lanes)
    total: CostBreakdown = {}
    unpriced = 0
    for lane, count in counts.items():
        quote = await _quote_image_lane(
            project, lane, aspect_ratio=aspect_ratio, resolver=resolver, session_factory=session_factory
        )
        if quote is None:
            unpriced += count
        else:
            _add_cost(total, quote[0] * count, quote[1])
    return BatchCostEstimate(total=total, unpriced_count=unpriced)


async def _quote_image_lane(
    project: dict[str, Any],
    lane: ImageLane,
    *,
    aspect_ratio: str,
    resolver: ConfigResolver,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[float, str] | None:
    """一条生图通道上每张图的预估单价；通道解析不出、读库失败或未定价时返回 ``None``。"""

    try:
        async with resolver.session() as r:
            resolved = await r.resolve_image_backend(project, None, generation_type=lane)
            provider, model = resolved.provider_id, resolved.model_id or ""
            try:
                resolution = await r.resolve_resolution(project, provider, model)
            except (ValueError, SQLAlchemyError):
                resolution = None
        async with session_factory() as session:
            return _quote_image(
                provider,
                model,
                await load_custom_price_source(session, provider, model),
                multiplier=await get_provider_multiplier(session, provider),
                image_size=resolution,
                aspect_ratio=aspect_ratio,
                # 图生图只知道走参考图，不知道几张：需要参考图张数的价目表记为未定价。
                reference_count=0 if lane == "t2i" else None,
            )
    except (ValueError, SQLAlchemyError):
        logger.debug("无法估算这批图片的费用 lane=%s", lane, exc_info=True)
        return None
