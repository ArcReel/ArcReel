"""费用估算服务 — 计算预估 + 汇总实际费用。"""

from __future__ import annotations

import logging
import math
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.resolver import ConfigResolver, get_provider_fallback
from lib.cost_calculator import cost_calculator
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.db.repositories.usage_repo import UsageRepository
from lib.grid.layout import calculate_grid_layout
from lib.pricing.strategies import PricingParams
from lib.project_manager import effective_mode
from lib.script_editor import ScriptEditError
from lib.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break

logger = logging.getLogger(__name__)

CostBreakdown = dict[str, float]


def _add_cost(target: CostBreakdown, amount: float, currency: str) -> None:
    if amount <= 0:
        return
    target[currency] = round(target.get(currency, 0) + amount, 6)


def _merge_breakdowns(a: CostBreakdown, b: CostBreakdown) -> CostBreakdown:
    merged = dict(a)
    for cur, amt in b.items():
        merged[cur] = round(merged.get(cur, 0) + amt, 6)
    return merged


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

            try:
                resolved_video = await r.resolve_video_backend(project_data, None)
                video_provider, video_model = resolved_video.provider_id, resolved_video.model_id
            except Exception:
                video_provider, video_model = "unknown", "unknown"

            try:
                generate_audio = await r.video_generate_audio(project_name)
            except Exception:
                generate_audio = False

            # 旁白配音（TTS）模型：project 覆盖 > 全局默认 > auto-resolve；
            # 未配置任何 audio 供应商时回落 unknown，该维度预估为空
            try:
                resolved_audio = await r.resolve_audio_backend(project_data, None)
                audio_provider, audio_model = resolved_audio.provider_id, resolved_audio.model_id
            except Exception:
                audio_provider, audio_model = "unknown", "unknown"

            try:
                _resolved_resolution = await r.resolve_resolution(project_data, video_provider, video_model or "")
            except Exception:
                _resolved_resolution = None
        video_resolution = _resolved_resolution or get_provider_fallback(video_provider)

        # Get actual costs + 自定义供应商价格（缺则预估恒为零，需与实际记账同源预查 DB 单价）
        async with self._session_factory() as session:
            actual_by_segment = await UsageRepository(session).get_actual_costs_by_segment(project_name)
            custom_repo = CustomProviderRepository(session)
            image_price = await custom_repo.resolve_price(image_provider, image_model)
            video_price = await custom_repo.resolve_price(video_provider, video_model)
            audio_price = await custom_repo.resolve_price(audio_provider, audio_model)

        generation_mode = project_data.get("generation_mode", "single")
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

        if generation_mode == "grid":
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

        content_mode = project_data.get("content_mode", "narration")

        for ep_meta in episodes_meta:
            script_file = ep_meta.get("script_file", "")
            script = scripts.get(script_file)
            if not script:
                continue

            # ad 参考生视频路径跳过分镜步骤（剧本骨架唯一，shots 不打 generation_mode 戳，
            # 不能像 narration/drama 那样靠剧本戳清空条目），分镜图维度按路径显式跳过；
            # 视频估值仍按镜头时长逐条计算（派生分组按 unit 计费，总时长与逐镜头求和一致）。
            skip_image_estimate = (
                content_mode == "ad" and effective_mode(project=project_data, episode=ep_meta) == "reference_video"
            )

            try:
                raw_segments, id_key, _, _, _ = get_storyboard_items(script)
            except ScriptEditError as exc:
                # 单集脏脚本(segments/scenes 键损坏)不应让整个项目费用估算 5xx;降级把该集
                # 估算为 0(raw_segments=[]) + warning 让运维知道,UI 仍能展示其他正常集的估算。
                logger.warning("费用估算跳过脏脚本 %s: %s", script_file, exc)
                raw_segments, id_key = [], "segment_id"

            # Grid 模式：预计算每个 segment 的图片分摊费用
            grid_cost_per_segment: dict[str, tuple[float, str]] = {}
            if generation_mode == "grid" and grid_image_unit_cost:
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
            # Map grid_id → [scene_ids] from each segment's generated_assets
            grid_to_scenes: dict[str, list[str]] = {}
            for seg in raw_segments:
                assets = seg.get("generated_assets")
                if not isinstance(assets, dict):
                    continue
                gid = assets.get("grid_id")
                sid = seg.get(id_key, "")
                if gid and sid:
                    grid_to_scenes.setdefault(gid, []).append(sid)

            # Compute per-scene share of each grid's actual cost
            grid_actual_per_scene: dict[str, CostBreakdown] = {}
            for gid, sids in grid_to_scenes.items():
                grid_cost = actual_by_segment.get(gid, {}).get("image", {})
                if grid_cost:
                    n = len(sids)
                    per_scene: CostBreakdown = {cur: round(amt / n, 6) for cur, amt in grid_cost.items()}
                    for sid in sids:
                        grid_actual_per_scene[sid] = _merge_breakdowns(grid_actual_per_scene.get(sid, {}), per_scene)

            segments_result = []
            ep_est: dict[str, CostBreakdown] = {}
            ep_act: dict[str, CostBreakdown] = {}

            for seg in raw_segments:
                seg_id = seg.get(id_key, "")
                duration = seg.get("duration_seconds", 8)

                est_image: CostBreakdown = {}
                est_video: CostBreakdown = {}
                est_audio: CostBreakdown = {}

                if not skip_image_estimate:
                    if generation_mode == "grid" and seg_id in grid_cost_per_segment:
                        cost_amount, cost_currency = grid_cost_per_segment[seg_id]
                        _add_cost(est_image, cost_amount, cost_currency)
                    elif image_unit_cost:
                        _add_cost(est_image, image_unit_cost[0], image_unit_cost[1])

                try:
                    vid_amount, vid_currency = cost_calculator.calculate_cost(
                        video_provider,
                        PricingParams(
                            call_type="video",
                            model=video_model,
                            resolution=video_resolution,
                            duration_seconds=duration,
                            generate_audio=generate_audio,
                        ),
                        custom_price_input=video_price.price_input,
                        custom_price_output=video_price.price_output,
                        custom_currency=video_price.currency,
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

                seg_actual = actual_by_segment.get(seg_id, {})
                act_image: CostBreakdown = seg_actual.get("image", {})
                if seg_id in grid_actual_per_scene:
                    act_image = _merge_breakdowns(act_image, grid_actual_per_scene[seg_id])
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

            episodes_result.append(
                {
                    "episode": ep_meta.get("episode"),
                    "title": ep_meta.get("title", ""),
                    "segments": segments_result,
                    "totals": {"estimate": ep_est, "actual": ep_act},
                }
            )

            for cost_type in ("image", "video", "audio"):
                proj_est[cost_type] = _merge_breakdowns(
                    proj_est.get(cost_type, {}),
                    ep_est.get(cost_type, {}),
                )
                proj_act[cost_type] = _merge_breakdowns(
                    proj_act.get(cost_type, {}),
                    ep_act.get(cost_type, {}),
                )

        # Project-level actual costs (characters/scenes/props/products 资产图 —— segment_id is null)
        async with self._session_factory() as session:
            project_image_by_type = await UsageRepository(session).get_project_image_costs_by_asset_type(project_name)
        for asset_type in ("characters", "scenes", "props", "products"):
            bucket = project_image_by_type.get(asset_type)
            if bucket:
                proj_act[asset_type] = bucket

        return {
            "project_name": project_name,
            "models": {
                "image": {"provider": image_provider, "model": image_model},
                "video": {"provider": video_provider, "model": video_model},
                "audio": {"provider": audio_provider, "model": audio_model},
            },
            "episodes": episodes_result,
            "project_totals": {"estimate": proj_est, "actual": proj_act},
        }
