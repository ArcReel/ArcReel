"""Cost-estimation service: compute estimates and aggregate actual costs."""

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
from lib.script.reference_video.provider_asset_links import configured_asset_link_check
from lib.script.reference_video.request_projection import (
    MemoizedReferenceAssets,
    ProjectionProblem,
    ReferenceAssetAvailability,
    ReferenceAssetLinkCheck,
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
#: Unpriced estimate items/calls, counted by (call_type, provider, model).
UnpricedCounts = Counter[tuple[str, str, str]]
# Accounting types shown on cost pages; text calls have no segment_id and appear only in project-level totals.
ACTUAL_COST_TYPES = ("image", "video", "audio")


#: All video task buckets enumerated for read-side routing. Storyboard projects use i2v throughout; reference-to-video uses shared
#: request projection by each unit's currently available assets. Preresolve both buckets here, avoiding
#: branching by generation mode/storyboard to determine which bucket to resolve; only two buckets keeps cost bounded.
_VIDEO_BUCKETS: tuple[VideoGenerationType, ...] = ("i2v", "r2v")

#: ``generated_assets`` fields representing generated media, detecting artifacts without local call records.
_GENERATED_MEDIA_FIELDS = ("storyboard_image", "video_clip", "narration_audio")
_SCRIPT_ITEM_KEYS = ("segments", "scenes", "shots", "video_units")


@dataclass(frozen=True)
class _VideoPricing:
    """Video pricing parameters for one task bucket: resolved model identity, resolution, effective generate_audio, and custom unit price.

    All five travel together through three estimation paths and must come from one resolution. Resolution
    and generate_audio evaluate by model identity; mixing components across buckets produces prices no model would incur."""

    provider: str
    model: str | None
    resolution: str | None
    generate_audio: bool
    price: VideoPriceSource


@dataclass(frozen=True, slots=True)
class BatchCostEstimate:
    """Whole-batch estimate: sum priced portions only and separately count unpriceable items.

    ``total`` separates currencies. Nonzero ``unpriced_count`` means ``total`` understates actual batch
    cost; the UI warns instead of presenting the low subtotal as the full batch price."""

    total: CostBreakdown
    unpriced_count: int

    def to_payload(self) -> dict[str, object]:
        """Two fields for batch-preview endpoints; ``estimated_cost`` is ``None`` without any priced items."""

        return {"estimated_cost": self.total or None, "unpriced_count": self.unpriced_count}


@dataclass(frozen=True, slots=True)
class VideoRequestQuote:
    """One video request quoted at current prices; ``amount`` of ``None`` means unpriced, explained by ``unpriced_reason``."""

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
    """Two DB-sourced video pricing inputs: provider multiplier and custom-model price source (``None`` for built-ins)."""

    custom: CustomPriceSource | None
    provider_multiplier: float


async def load_video_price_source(session: AsyncSession, provider_id: str, model_id: str) -> VideoPriceSource:
    """Read price sources for one video model, using the same reads as settlement."""
    return VideoPriceSource(
        custom=await load_custom_price_source(session, provider_id, model_id),
        provider_multiplier=await get_provider_multiplier(session, provider_id),
    )


def quote_video_request_from_price(facts: VideoRequestCostFacts, source: VideoPriceSource) -> VideoRequestQuote:
    """Quote one video request with the same catalog query/calculator as settlement.

    Built-in channels derive usage under their usage contracts (estimators for quantities requiring
    conversion), then apply catalog prices. A quantity unavailable from request facts and without an
    estimator means unpriced. Custom models use matched-entry prices and endpoint-declared or generic
    usage; unmatched entries mean unpriced."""
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
    """Read current price sources and quote one video request; ``None`` on DB failure or unrecognized channel request."""

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
    """Storyboard aspect ratio: ``aspect_ratio`` may be str or a resource-type dict; share generation-task resolution."""
    raw_ar = project.get("aspect_ratio")
    if isinstance(raw_ar, str):
        return raw_ar
    if isinstance(raw_ar, dict):
        return raw_ar.get("storyboards", "9:16")
    # narration/ad default portrait; drama (including historical unknown-value fallback) defaults landscape.
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
    """Estimate one image; ``None`` when unpriced.

    Built-ins share settlement's catalog query/calculator, deriving usage from channel request facts.
    ``image_size`` is the execution resolution tier; omit when absent, without a separate pricing minimum.
    Custom providers (with ``price`` as price source) use matched-entry prices and endpoint-estimated or generic usage."""
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
    """Whether the project already has generated asset sheets, storyboard images, video, or narration audio."""
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
    """Claim segment actual costs; return each (accounting key, type) at most once per estimate.

    Claim by type rather than whole key: if callers consume only some types, leave the rest unclaimed
    for fallback unassigned aggregation, never swallowing them through whole-key claiming."""
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
    """Split a whole charge equally into ``parts``, adding indivisible remainder to the last part.

    Remainder compensation preserves the exact original total. Callers accumulate shares into episode/
    project totals; rounding every share independently amplifies errors by storyboard count into visible price differences."""
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

        # Resolve current model configuration in one shared session. Price by T2I (orthogonal to I2I);
        # missing T2I must not fall back to I2I and use the wrong capability's price table.
        # ConfigResolver centralizes image/video project overrides, sharing execution's
        # payload>project>global-default chain. Pass payload None here (estimation has no historical payload to drain).
        request_facts_lookup = configured_reference_request_facts(project_data, self._resolver)
        reference_facts: dict[VideoGenerationType, VideoRequestFacts] = {}
        if is_reference_video:
            for generation_type in _VIDEO_BUCKETS:
                # Projectors return structured blockers for units actually using this bucket; unused-bucket
                # configuration problems must not break the entire cost page.
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

            # T2I resolution tier shares ``grid_resolution`` selection with route enqueue/Agent tools, preventing estimated grid
            # counts from drifting from enqueues. The same tier prices both storyboard paths: unconfigured grids use
            # ``grid_image_size`` minimums (same source as ``execute_grid_task``); ordinary
            # storyboards omit unset resolution like execution and use price-table defaults. Resolve once before both paths, sharing I/O
            # across grid/nongrid projects. Pricing and execution use the same T2I slot/project configuration, but identity
            # keys differ (registry ``model_id`` here, constructed backend model at execution), so providers
            # with divergent identities can have different estimated/rendered tiers.
            image_resolution = await resolve_image_resolution(r, project_data)
            grid_allow_large = large_grid_allowed(image_resolution)

            # Resolve video by task bucket (``docs/adr/0054``), sharing billed execution models: image-to-video/grids use
            # i2v prices; reference-to-video routes per unit after hydrating current assets. Resolve both buckets
            # here (see ``_VIDEO_BUCKETS``), evaluating resolution and
            # generate_audio by each model identity.
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
                    # Unresolved-fact buckets retain only display model coordinates; storyboards report blockers individually, reference units
                    # get blockers from request projectors. Neither produces video quotes.
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

            # TTS models use project snapshots, never global defaults; postproduction dubbing or absent snapshots fall back to unknown,
            # leaving that estimate dimension empty.
            tts_settings = (
                project_tts_settings(project_data) if project_narration_delivery(project_data) == USE_TTS else None
            )
            audio_provider, audio_model = (
                (tts_settings.provider_id, tts_settings.model_id)
                if tts_settings is not None
                else ("unknown", "unknown")
            )

        # Get actual costs + custom-model price sources (DB prequeries shared with actual accounting).
        async with self._session_factory() as session:
            actual_by_segment = await UsageRepository(session).get_actual_costs_by_segment(project_name)
            image_price = await load_custom_price_source(session, image_provider, image_model)
            image_multiplier = await get_provider_multiplier(session, image_provider)
            audio_price = await load_custom_price_source(session, audio_provider, audio_model)
            audio_multiplier = await get_provider_multiplier(session, audio_provider)
            # Buckets often resolve to one model; deduplicate identities before unit-price queries to avoid duplicate DB reads.
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
        # Project video-model displays route by project generation_mode: ``models`` answers the current configuration's
        # primary generation bucket. Unit-level image-free reference-to-video fallback pricing belongs to episode estimates and does not change project
        # display identity.
        project_video = video_pricing[video_bucket_for_generation_mode(project_data.get("generation_mode"))]

        grid_enabled = grid_storyboard_enabled(project_data)
        aspect_ratio = _storyboard_aspect_ratio(project_data)
        # Outgoing video aspect ratio shares generation-task resolution; Ark uses it for video-token conversion.
        video_aspect_ratio = resolve_video_aspect_ratio(project_data)

        # Precompute ordinary storyboard-image unit prices (configured resolution, text-to-image without references). Price grid images by individual grid
        # aspect ratios within episodes; unset resolution uses ``grid_image_size`` like execution.
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
        # Items expected to have estimates but excluded for missing model prices; together with actual costs, let the UI distinguish zero from unknown.
        estimate_unpriced: UnpricedCounts = Counter()
        proj_act: dict[str, CostBreakdown] = {}
        claimed_actual: set[tuple[str, str]] = set()

        def _accumulate_episode(
            ep_meta: dict[str, Any],
            segments_result: list[dict[str, Any]],
            ep_est: dict[str, CostBreakdown],
            ep_act: dict[str, CostBreakdown],
        ) -> None:
            """Collect one episode estimate into project totals, sharing finalization across both estimation paths."""
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

        # Reference-to-video skips storyboard steps; all content modes bill/display self-contained reference_unit values.
        #
        # Project generation mode alone determines generation paths, one mode for the whole project, unchanged by episode (scripts carry no mode);
        # reference-video request projection then routes individual units by current assets.
        availability: ReferenceAssetAvailability = _AssumeResolvedAssetsAvailable()
        expansion: ReferenceExpansionCache | None = None
        # Provider associations share admission checks: unavailable associations or failed ingestion produce blockers without estimates. Without a project directory
        # (declaration-only pricing), there are no images to check.
        asset_links: ReferenceAssetLinkCheck | None = None
        if is_reference_video and self._project_path is not None:
            asset_links = configured_asset_link_check(project_path=self._project_path, resolver=self._resolver)
            # Share one read-only availability decision per estimate, constructed from one manifest snapshot in a worker thread.
            current = await asyncio.to_thread(CurrentReferenceAssets, self._project_path, project_data)
            availability = MemoizedReferenceAssets(current)
            # Share reference catalogs/candidate-path resolution across the estimate too, without per-unit reconstruction.
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
                    asset_links=asset_links,
                    video_prices=video_prices,
                    actual_by_segment=actual_by_segment,
                    claimed_actual=claimed_actual,
                    unpriced=estimate_unpriced,
                )
                _accumulate_episode(ep_meta, segments_result, ep_est, ep_act)
                continue

            # Storyboards always use i2v; references already selected buckets in the unit-projection branch above.
            episode_video = video_pricing["i2v"]

            try:
                raw_segments, id_key, _, _, _ = get_storyboard_items(script)
            except ScriptEditError as exc:
                # One dirty episode script (broken segments/scenes key) must not make project estimates 5xx. Estimate that episode
                # as 0 (raw_segments=[]) and log warning for operations, while UI shows other valid episodes.
                logger.warning("费用估算跳过脏脚本 %s: %s", script_file, exc)
                raw_segments, id_key = [], "segment_id"

            # Grid assembly: precompute image-cost shares per segment. Identify shares by position in ``raw_segments``,
            # as with actual-cost splitting below (see its rationale);
            # ``group_scenes_by_segment_break`` creates ordered, contiguous, complete, nonoverlapping groups, so positions equal within-group indices
            # plus preceding group lengths.
            grid_cost_per_index: dict[int, tuple[float, str]] = {}
            grid_unpriced_indices: set[int] = set()
            if grid_enabled:
                group_offset = 0
                for group in group_scenes_by_segment_break(raw_segments, id_key):
                    n = len(group)
                    # Grid counts share actual enqueue planning (plan_grid_chunks): groups over limits bill by resulting chunk
                    # count, keeping estimation aligned with execution.
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
            # Identify cost shares by raw_segments position, not ID: ADR 0053 permits several entries covered by one grid
            # to share an ID. Positions are unique where IDs are not, so only position-based organization makes every
            # entry (including equal IDs) consume its own share exactly once.
            grid_to_indices: dict[str, list[int]] = {}
            for idx, seg in enumerate(raw_segments):
                gid = get_generated_assets(seg).get("grid_id")
                if gid and seg.get(id_key, ""):
                    grid_to_indices.setdefault(gid, []).append(idx)

            # Compute shares per position/grid; ``_split_cost_across`` remainder compensation preserves the exact frozen
            # actual total.
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

                # External edits may make duration noninteger; one dirty storyboard receives no video quote and does not enter pricing.
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

                # Price narration by novel_text (only narration/commentary segments carry source text).
                novel_text = seg.get("novel_text")
                narration_text = novel_text.strip() if isinstance(novel_text, str) else ""
                narration_chars = len(narration_text)
                if narration_chars and tts_settings is not None:
                    # Share settlement catalog queries/calculator: count characters with the price table's Chinese-characters-count-as-2 rule. Token-priced models lack
                    # estimators and remain unpriced. Custom models use matched-entry prices.
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

        # Historical accounting unclaimed by current scripts remains real spending. Canonical segment/unit IDs carry E{n}
        # prefixes for episode attribution; unrecognized records or deleted episodes still count in project totals.
        episodes_by_number = {ep["episode"]: ep for ep in episodes_result}
        # Episodes without scripts or generation are absent from estimates, but historical costs still belong to them. Add
        # actual-only episode results as needed, showing money on episode rows instead of silently falling back to project totals.
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
        # Append supplemental episodes then restore project.json order, keeping cost-page rows stable.
        meta_order = {ep_meta.get("episode"): i for i, ep_meta in enumerate(episodes_meta)}
        episodes_result.sort(key=lambda ep: meta_order.get(ep["episode"], len(meta_order)))

        # Project-level actual costs (character/scene/prop/product sheets; segment_id is null).
        async with self._session_factory() as session:
            usage_repo = UsageRepository(session)
            project_image_by_type = await usage_repo.get_project_image_costs_by_asset_type(project_name)
            # Successful calls with NULL cost are unpriced; actual zero-priced (0) calls are excluded.
            actual_unpriced: UnpricedCounts = Counter()
            for call_type, provider, model, calls in await usage_repo.get_unpriced_calls_by_model(project_name):
                actual_unpriced[call_type, provider, model] += calls
            provider_names = await usage_repo.provider_display_names(
                {provider for _, provider, _ in (*estimate_unpriced, *actual_unpriced)}
            )
            # Artifacts without local media-call records (e.g. imported projects/other-machine generation) have unknowable actual costs.
            missing_local_calls = not await usage_repo.has_media_calls(project_name) and _has_generated_media(
                project_data, scripts
            )
        for asset_type in ("characters", "scenes", "props", "products"):
            bucket = project_image_by_type.get(asset_type)
            if bucket:
                proj_act[asset_type] = bucket
        # For empty-segment_id accounting, sheets are separately categorized; remaining costs are still real: images unclassifiable by output_path
        # and historical video/audio before segment_id backfill. Without episode clues, include only in
        # project-level unassigned costs.
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
        asset_links: ReferenceAssetLinkCheck | None,
        video_prices: dict[tuple[str, str], VideoPriceSource],
        actual_by_segment: ActualBySegment,
        claimed_actual: set[tuple[str, str]],
        unpriced: UnpricedCounts,
    ) -> tuple[list[dict[str, Any]], dict[str, CostBreakdown], dict[str, CostBreakdown]]:
        """Estimate reference_video episodes: units are both display and billing granularity.

        Units are the smallest addressable entities. Canvas/cost panels index by ``unit_id`` (see
        ``ReferenceVideoCanvas`` reading ``cost-store`` ``_segmentIndex.get(unit.unit_id)``), so no
        ``_split_cost_across`` is needed.

        Hydrate current available unit images before selecting tiers (files exist and are claimed by manifests,
        as in admission/execution: images → r2v, image-free fallback units → i2v), then resolve that bucket's
        capabilities. Divergent declared references/actual assets yield structured blockers, not bucket-switched
        fictional quotes. Base request duration on ``unit.duration_seconds``; select tiers and bill with that
        same bucket model, matching execution seconds. Narration delivery affects neither video requests nor quotes.

        No image/audio estimation dimensions: this mode skips storyboards (no images), and unit bodies are
        whole passages without separate narration/on-camera fields for independent audio pricing. Pass all
        three actual-cost dimensions through from ``actual_by_segment[unit_id]``: ``lib/generation/media_generator.py``
        records ``resource_type == "reference_videos"`` usage segment_id as unit_id, matching output identity here.
        Historical costs under storyboard IDs (``E1S1``, etc.) before switching modes cannot appear here:
        no unit/storyboard mapping exists to attribute them.

        Empty bodies or ``video_unit_replan_problems`` produce no estimate: these units are rejected by
        ``video_batch_admission.reference_unit_task_spec``, so nonzero estimates would show unsupported costs.
        Share the enqueue-side body/replanning model rather than divergent custom processing. Still retain the
        whole unit in aggregates: enqueueability affects only new estimates, not historical successful generation
        (``actual_by_segment[unit_id]`` records historical actuals independently of current edits). Making a
        previously generated unit unenqueueable must not erase its costs from unit/episode/project totals."""
        segments_result: list[dict[str, Any]] = []
        ep_est: dict[str, CostBreakdown] = {}
        ep_act: dict[str, CostBreakdown] = {}

        projector = ReferenceUnitRequestProjector(request_facts_lookup, availability, asset_links)

        def _screen_candidates() -> list[tuple[dict[str, Any], str, bool]]:
            # (unit, unit_id, enqueueable); screen valid units first, hydrating only enqueueable ones.
            candidates: list[tuple[dict[str, Any], str, bool]] = []
            for unit in units:
                if not isinstance(unit, dict):
                    continue
                # unit_id must originally be a string: execution compares it against original, uncast script values to locate
                # units (``execute_reference_video_task``). Numeric/boolean truthy values cast through str() could pass estimates
                # but never match at execution due to type mismatch; do not show costs for unexecutable requests.
                raw_unit_id = unit.get("unit_id")
                if not isinstance(raw_unit_id, str) or not raw_unit_id:
                    continue
                # Nonstring text (e.g. directly written "text": true/1) must not interrupt whole estimates with one dirty entry.
                text = unit.get("text")
                enqueueable = isinstance(text, str) and bool(text.strip()) and not video_unit_replan_problems(unit)
                candidates.append((unit, raw_unit_id, enqueueable))
            return candidates

        # Agent/external edits can write nonnumeric duration_seconds ("bad"/lists/dicts);
        # one invalid unit must not fail project estimates, so ValueError/TypeError from asset resolution, hydration, and request projection
        # skips only that unit. Projectors convert capability errors into structured blockers,
        # retained normally in unit quotes.
        def _hydrate_episode() -> tuple[list[tuple[dict[str, Any], str, bool]], list[ReferenceUnitHydration | None]]:
            candidates = _screen_candidates()
            # Align one-to-one with candidates; None for unenqueueable or failed-hydration positions.
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

        # Per-unit dialogue/mention resolution is substantial CPU work; candidate expansion/availability touches the filesystem.
        # Screen and hydrate the whole episode once in a worker thread rather than queueing units separately.
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
    """Estimate image batches using current project configuration: sum priced portions and count unpriced images.

    Price each image by its own lane: text-to-image and image-to-image are orthogonal capability slots
    that may select different models. Resolve resolution tiers by lane model like execution; omit absent
    resolution and use the price table's default tier. ``aspect_ratio`` is the batch's outgoing ratio
    (needed for dimension-priced models). Every image under unresolved-provider, DB-failed, or
    unpriceable lanes counts as unpriced."""

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
    """Estimated unit price per image in one lane; ``None`` for unresolved lanes, DB failure, or unpriced calls."""

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
                # Image-to-image knows references are used, not their count; price tables requiring reference counts remain unpriced.
                reference_count=0 if lane == "t2i" else None,
            )
    except (ValueError, SQLAlchemyError):
        logger.debug("无法估算这批图片的费用 lane=%s", lane, exc_info=True)
        return None
