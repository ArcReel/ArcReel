"""费用估算服务 — 计算预估 + 汇总实际费用。"""

from __future__ import annotations

import logging
import math
from typing import Any

from lib.config.resolver import ConfigResolver
from lib.cost_calculator import cost_calculator
from lib.grid.layout import calculate_grid_layout
from lib.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from lib.usage_tracker import UsageTracker
from server.services.resolution_resolver import get_provider_fallback, resolve_resolution

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
    def __init__(self, resolver: ConfigResolver, tracker: UsageTracker) -> None:
        self._resolver = resolver
        self._tracker = tracker

    async def compute(
        self,
        project_data: dict[str, Any],
        scripts: dict[str, dict[str, Any]],
        *,
        project_name: str,
    ) -> dict[str, Any]:
        episodes_meta = project_data.get("episodes", [])

        # Resolve current model config（共享单一 session）
        async with self._resolver.session() as r:
            try:
                image_provider, image_model = await r.default_image_backend()
            except Exception:
                image_provider, image_model = "unknown", "unknown"

            try:
                video_provider, video_model = await r.default_video_backend()
            except Exception:
                video_provider, video_model = "unknown", "unknown"

            try:
                generate_audio = await r.video_generate_audio(project_name)
            except Exception:
                generate_audio = False

        # 项目级视频配置覆盖
        # 优先读新格式 video_backend（"provider_id/model_id"），兼容旧 video_provider 字段
        project_video_backend = project_data.get("video_backend") or ""
        registry_video_provider_id: str | None = None
        if project_video_backend and "/" in project_video_backend:
            _vb_provider, _vb_model = project_video_backend.split("/", 1)
            registry_video_provider_id = _vb_provider
            video_provider = _vb_provider
            video_model = _vb_model
        else:
            project_video_provider = project_data.get("video_provider")
            if project_video_provider:
                video_provider = project_video_provider
                registry_video_provider_id = project_video_provider
                # 项目级可能有自己的模型设置
                project_video_settings = project_data.get("video_provider_settings", {}).get(project_video_provider, {})
                if project_video_settings.get("model"):
                    video_model = project_video_settings["model"]

        # 项目级图片配置覆盖
        project_image_provider = project_data.get("image_provider")
        if project_image_provider:
            image_provider = project_image_provider

        _resolve_pid = registry_video_provider_id or video_provider
        _resolved_resolution = await resolve_resolution(project_data, _resolve_pid, video_model or "")
        video_resolution = _resolved_resolution or get_provider_fallback(video_provider)

        # Get actual costs
        actual_by_segment = await self._tracker.get_actual_costs_by_segment(project_name)

        generation_mode = project_data.get("generation_mode", "single")
        # 规范化 aspect_ratio：可能是 str 或 dict，复用生成任务的解析逻辑
        raw_ar = project_data.get("aspect_ratio")
        if isinstance(raw_ar, str):
            aspect_ratio = raw_ar
        elif isinstance(raw_ar, dict):
            aspect_ratio = raw_ar.get("storyboards", "9:16")
        else:
            aspect_ratio = "9:16" if project_data.get("content_mode", "narration") == "narration" else "16:9"

        # 预计算图片单价
        image_unit_cost: tuple[float, str] | None = None
        grid_image_unit_cost: tuple[float, str] | None = None
        try:
            image_unit_cost = cost_calculator.calculate_cost(
                provider=image_provider,
                call_type="image",
                model=image_model,
                resolution="1K",
            )
        except Exception:
            logger.debug("无法计算 image 预估单价", exc_info=True)

        if generation_mode == "grid":
            try:
                grid_image_unit_cost = cost_calculator.calculate_cost(
                    provider=image_provider,
                    call_type="image",
                    model=image_model,
                    resolution="2K",
                )
            except Exception:
                grid_image_unit_cost = image_unit_cost

        episodes_result = []
        proj_est: dict[str, CostBreakdown] = {}
        proj_act: dict[str, CostBreakdown] = {}

        for ep_meta in episodes_meta:
            script_file = ep_meta.get("script_file", "")
            script = scripts.get(script_file)
            if not script:
                continue

            raw_segments, id_key, _, _, _ = get_storyboard_items(script)

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

                if generation_mode == "grid" and seg_id in grid_cost_per_segment:
                    cost_amount, cost_currency = grid_cost_per_segment[seg_id]
                    _add_cost(est_image, cost_amount, cost_currency)
                elif image_unit_cost:
                    _add_cost(est_image, image_unit_cost[0], image_unit_cost[1])

                try:
                    vid_amount, vid_currency = cost_calculator.calculate_cost(
                        provider=video_provider,
                        call_type="video",
                        model=video_model,
                        resolution=video_resolution,
                        duration_seconds=duration,
                        generate_audio=generate_audio,
                    )
                    _add_cost(est_video, vid_amount, vid_currency)
                except Exception:
                    logger.debug("无法计算 video 预估 for %s", seg_id, exc_info=True)

                seg_actual = actual_by_segment.get(seg_id, {})
                act_image: CostBreakdown = seg_actual.get("image", {})
                if seg_id in grid_actual_per_scene:
                    act_image = _merge_breakdowns(act_image, grid_actual_per_scene[seg_id])
                act_video: CostBreakdown = seg_actual.get("video", {})

                segments_result.append(
                    {
                        "segment_id": seg_id,
                        "duration_seconds": duration,
                        "estimate": {"image": est_image, "video": est_video},
                        "actual": {"image": act_image, "video": act_video},
                    }
                )

                for cost_type in ("image", "video"):
                    ep_est[cost_type] = _merge_breakdowns(
                        ep_est.get(cost_type, {}),
                        {"image": est_image, "video": est_video}[cost_type],
                    )
                    ep_act[cost_type] = _merge_breakdowns(
                        ep_act.get(cost_type, {}),
                        {"image": act_image, "video": act_video}[cost_type],
                    )

            episodes_result.append(
                {
                    "episode": ep_meta.get("episode"),
                    "title": ep_meta.get("title", ""),
                    "segments": segments_result,
                    "totals": {"estimate": ep_est, "actual": ep_act},
                }
            )

            for cost_type in ("image", "video"):
                proj_est[cost_type] = _merge_breakdowns(
                    proj_est.get(cost_type, {}),
                    ep_est.get(cost_type, {}),
                )
                proj_act[cost_type] = _merge_breakdowns(
                    proj_act.get(cost_type, {}),
                    ep_act.get(cost_type, {}),
                )

        # Project-level actual costs (characters/scenes/props 三类资产图 —— segment_id is null)
        project_image_by_type = await self._tracker.get_project_image_costs_by_asset_type(project_name)
        for asset_type in ("characters", "scenes", "props"):
            bucket = project_image_by_type.get(asset_type)
            if bucket:
                proj_act[asset_type] = bucket

        return {
            "project_name": project_name,
            "models": {
                "image": {"provider": image_provider, "model": image_model},
                "video": {"provider": video_provider, "model": video_model},
            },
            "episodes": episodes_result,
            "project_totals": {"estimate": proj_est, "actual": proj_act},
        }
