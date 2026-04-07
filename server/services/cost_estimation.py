"""费用估算服务 — 计算预估 + 汇总实际费用。"""

from __future__ import annotations

import logging
from typing import Any

from lib.config.resolver import ConfigResolver
from lib.cost_calculator import cost_calculator
from lib.storyboard_sequence import get_storyboard_items
from lib.usage_tracker import UsageTracker

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
        project_video_provider = project_data.get("video_provider")
        if project_video_provider:
            video_provider = project_video_provider
            # 项目级可能有自己的模型设置
            project_video_settings = project_data.get("video_provider_settings", {}).get(project_video_provider, {})
            if project_video_settings.get("model"):
                video_model = project_video_settings["model"]

        # 项目级图片配置覆盖
        project_image_provider = project_data.get("image_provider")
        if project_image_provider:
            image_provider = project_image_provider

        from server.services.generation_tasks import DEFAULT_VIDEO_RESOLUTION

        video_resolution = DEFAULT_VIDEO_RESOLUTION.get(video_provider, "1080p")

        # Get actual costs
        actual_by_segment = await self._tracker.get_actual_costs_by_segment(project_name)

        # 预计算图片单价（所有 segment 相同）
        image_unit_cost: tuple[float, str] | None = None
        try:
            image_unit_cost = cost_calculator.calculate_cost(
                provider=image_provider,
                call_type="image",
                model=image_model,
                resolution="1K",
            )
        except Exception:
            logger.debug("无法计算 image 预估单价", exc_info=True)

        episodes_result = []
        proj_est: dict[str, CostBreakdown] = {}
        proj_act: dict[str, CostBreakdown] = {}

        for ep_meta in episodes_meta:
            script_file = ep_meta.get("script_file", "")
            script = scripts.get(script_file)
            if not script:
                continue

            raw_segments, id_key, _, _ = get_storyboard_items(script)

            segments_result = []
            ep_est: dict[str, CostBreakdown] = {}
            ep_act: dict[str, CostBreakdown] = {}

            for seg in raw_segments:
                seg_id = seg.get(id_key, "")
                duration = seg.get("duration_seconds", 8)

                est_image: CostBreakdown = {}
                est_video: CostBreakdown = {}

                if image_unit_cost:
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

        # Project-level actual costs (character/clue images — segment_id is null)
        project_level = actual_by_segment.get("__project__", {})
        if "image" in project_level:
            proj_act["character_and_clue"] = project_level["image"]

        return {
            "project_name": project_name,
            "models": {
                "image": {"provider": image_provider, "model": image_model},
                "video": {"provider": video_provider, "model": video_model},
            },
            "episodes": episodes_result,
            "project_totals": {"estimate": proj_est, "actual": proj_act},
        }
