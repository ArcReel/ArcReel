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
from lib.reference_video.ad_units import derive_ad_reference_units, resolve_ad_unit_shots
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


def _add_cost(target: CostBreakdown, amount: float, currency: str) -> None:
    if amount <= 0:
        return
    target[currency] = round(target.get(currency, 0) + amount, 6)


def _merge_breakdowns(a: CostBreakdown, b: CostBreakdown) -> CostBreakdown:
    merged = dict(a)
    for cur, amt in b.items():
        merged[cur] = round(merged.get(cur, 0) + amt, 6)
    return merged


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
    video_provider: str,
    video_model: str | None,
    video_resolution: str | None,
    generate_audio: bool,
    video_price: Any,
) -> CostBreakdown:
    """一个参考视频 unit 取档后秒数的视频估值。计价失败返回空 breakdown（该 unit 不计费）。

    两条参考视频估算路径（ad 摊回镜头、narration/drama 按 unit 展示）的展示颗粒度不同，
    但「按取档后秒数向 provider 询价」这一步与颗粒度无关，共用同一实现避免两处漂移。
    """
    est_video: CostBreakdown = {}
    try:
        amount, currency = cost_calculator.calculate_cost(
            video_provider,
            PricingParams(
                call_type="video",
                model=video_model,
                resolution=video_resolution,
                duration_seconds=duration_seconds,
                generate_audio=generate_audio,
            ),
            custom_price_input=video_price.price_input,
            custom_price_output=video_price.price_output,
            custom_currency=video_price.currency,
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

            try:
                resolved_video = await r.resolve_video_backend(project_data, None)
                video_provider, video_model = resolved_video.provider_id, resolved_video.model_id
            except Exception:
                video_provider, video_model = "unknown", "unknown"

            try:
                generate_audio = await r.video_generate_audio(project_name)
            except Exception:
                generate_audio = False
            # AI Studio 的 Veo 请求不下发 generate_audio 参数（该开关仅存在于 Vertex 定价页，
            # AI Studio 定价页无 audio-off 档），供应商恒按含音价出账；真实生成时
            # GeminiVideoBackend 已按此结算实际用量（backend_type != "vertex" 强制 True，见
            # lib/video_backends/gemini.py），此处对齐纯预估路径，避免二者口径分叉。
            if video_provider == "gemini-aistudio":
                generate_audio = True

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
        # 惰性解析：只有项目里真出现按 unit 计费的参考视频集时才触发这次额外 IO
        # （见 :func:`server.services.reference_video_tasks.resolve_project_duration_context`）。
        duration_ctx: ProjectDurationContext | None = None

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

        for ep_meta in episodes_meta:
            script_file = ep_meta.get("script_file", "")
            script = scripts.get(script_file)
            if not script:
                continue

            # 参考生视频路径跳过分镜步骤，分镜图维度按路径显式跳过；视频估值按实际计费颗粒度
            # （reference_unit，而非镜头）计算。两条子路径的展示颗粒度不同：ad 的 unit 只是
            # 镜头分组索引，费用要摊回自带 ``shot_id`` 的成员镜头（``_estimate_ad_reference_video_episode``）；
            # narration/drama 的 unit 自带 ``unit_id`` 且成员 shot 无独立 ID，unit 本身即展示
            # 颗粒度（``_estimate_unit_reference_video_episode``）。
            #
            # ad 骨架唯一、shots 不打 generation_mode 戳，生成路径以项目级/集级戳为真相源，故
            # 只按 ``effective_mode`` 判定；narration/drama 的生成侧按剧本自身的 generation_mode
            # 戳判定（``server/agent_runtime/sdk_tools/enqueue_videos.py::_is_reference_script``），
            # 项目已切到 reference_video 但该集剧本仍是分镜骨架（戳缺失/非 reference_video）时
            # 实际入队的还是分镜任务，估算须按同一戳回落分镜路径——不能只看 video_units 是否
            # 非空：剧本可能残留切换前/迁移中间态的 video_units，戳仍是分镜骨架时那份数据不是
            # 真相源，若照旧估值会与实际入队的分镜任务对不上。
            is_reference_video = effective_mode(project=project_data, episode=ep_meta) == "reference_video"
            raw_units = script.get("video_units")
            video_units: list[Any] = raw_units if isinstance(raw_units, list) else []
            if content_mode == "ad":
                estimate_by_unit = is_reference_video
            else:
                estimate_by_unit = is_reference_video and script.get("generation_mode") == "reference_video"

            if estimate_by_unit:
                if duration_ctx is None:
                    duration_ctx = await resolve_project_duration_context(project_data)
                if content_mode == "ad":
                    segments_result, ep_est, ep_act = self._estimate_ad_reference_video_episode(
                        script=script,
                        episode=ep_meta.get("episode"),
                        duration_ctx=duration_ctx,
                        video_provider=video_provider,
                        video_model=video_model,
                        video_resolution=video_resolution,
                        generate_audio=generate_audio,
                        video_price=video_price,
                        actual_by_segment=actual_by_segment,
                    )
                else:
                    segments_result, ep_est, ep_act = self._estimate_unit_reference_video_episode(
                        units=video_units,
                        duration_ctx=duration_ctx,
                        video_provider=video_provider,
                        video_model=video_model,
                        video_resolution=video_resolution,
                        generate_audio=generate_audio,
                        video_price=video_price,
                        actual_by_segment=actual_by_segment,
                    )
                _accumulate_episode(ep_meta, segments_result, ep_est, ep_act)
                continue

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
                gid = get_generated_assets(seg).get("grid_id")
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

            _accumulate_episode(ep_meta, segments_result, ep_est, ep_act)

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

    def _estimate_ad_reference_video_episode(
        self,
        *,
        script: dict[str, Any],
        episode: int | None,
        duration_ctx: ProjectDurationContext,
        video_provider: str,
        video_model: str | None,
        video_resolution: str | None,
        generate_audio: bool,
        video_price: Any,
        actual_by_segment: dict[str, dict[str, CostBreakdown]],
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

        分组优先读剧本已持久化的 ``reference_units``（与执行时同一份索引，见
        ``lib.reference_video.ad_units.sync_ad_reference_units``）；用户尚未打开过参考
        视频面板/触发过入队、索引还未派生时，按纯函数现推一份仅用于估算、不写回剧本——
        时长上限取自 ``duration_ctx``（与执行侧派生同一份能力解析，不额外触发 IO），
        分组结果因此与真实派生同约束，真实分组仍以执行时派生结果为准。

        无图片/音频估值维度：ad 参考视频不产生分镜图（跳过分镜步骤）、镜头口播文案不产生
        旁白配音估值（同 storyboard 模式口径，见 ``test_ad_voiceover_does_not_produce_audio_estimate``）。
        实付维度不受此限——见上段。
        """
        units = script.get("reference_units")
        if not isinstance(units, list) or not units:
            units = derive_ad_reference_units(
                script.get("shots"), episode=episode or 0, max_unit_duration=duration_ctx.max_duration
            )

        segments_result: list[dict[str, Any]] = []
        ep_est: dict[str, CostBreakdown] = {}
        ep_act: dict[str, CostBreakdown] = {}

        for unit in units:
            if not isinstance(unit, dict):
                continue
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

            slot = precheck_unit(duration_ctx, unit, ad_shots)

            est_video = _estimate_unit_video_cost(
                unit_id=unit_id,
                duration_seconds=slot.seconds,
                video_provider=video_provider,
                video_model=video_model,
                video_resolution=video_resolution,
                generate_audio=generate_audio,
                video_price=video_price,
            )

            act_video: CostBreakdown = actual_by_segment.get(unit_id, {}).get("video", {})
            est_by_shot = _split_cost_across(est_video, len(ad_shots))
            act_by_shot = _split_cost_across(act_video, len(ad_shots))

            for idx, shot in enumerate(ad_shots):
                shot_est = est_by_shot[idx]
                shot_id = shot.get("shot_id", "")
                # 模式切换前（storyboard 阶段）按 shot_id 记的镜头图/视频/音频实付仍要展示，
                # 不因当前是 reference_video 模式而清空——那是用户已经花掉的真实费用。video
                # 维度与 unit 分摊额合并而非互相替换：同一镜头可能既有切换前的历史视频调用
                # （shot_id 记账），也有切换后的 unit 调用（unit_id 分摊），两者都是真实支出。
                shot_actual = actual_by_segment.get(shot_id, {})
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

    def _estimate_unit_reference_video_episode(
        self,
        *,
        units: list[Any],
        duration_ctx: ProjectDurationContext,
        video_provider: str,
        video_model: str | None,
        video_resolution: str | None,
        generate_audio: bool,
        video_price: Any,
        actual_by_segment: dict[str, dict[str, CostBreakdown]],
    ) -> tuple[list[dict[str, Any]], dict[str, CostBreakdown], dict[str, CostBreakdown]]:
        """narration/drama + reference_video 集的估值：unit 本身就是展示颗粒度，无需分摊。

        与 ad 路径不同——``AdShot`` 自带 ``shot_id``，``reference_units`` 只是把镜头分组的
        轻量索引，unit 费用要摊回成员镜头才能被前端按镜头 ID 查到；narration/drama 的
        ``video_units[*].shots``（``Shot``）没有独立 ID，unit 本身就是最小可寻址单位，
        前端画布与费用面板均按 ``unit_id`` 索引（见 ``ReferenceVideoCanvas`` 读
        ``cost-store`` 的 ``_segmentIndex.get(unit.unit_id)``），故此处不需要
        ``_split_cost_across`` 这一步。

        取档口径与 ad 路径同构：按 ``duration_ctx`` 解析的项目视频能力对 unit 剧本时长
        （``unit.duration_seconds``，各 shot 之和）取档后计费，而非原始剧本时长——
        与 ``execute_reference_video_task`` 实际申请的秒数对齐。

        无图片/音频估值维度：该模式跳过分镜步骤（无分镜图），``Shot`` 没有独立的旁白/口播
        文案字段可供计价，同 ad 参考视频口径（见 ``_estimate_ad_reference_video_episode``
        docstring）。实付按 ``actual_by_segment[unit_id]`` 三个维度原样透传——``lib/media_generator.py``
        对 ``resource_type == "reference_videos"`` 的记账以 unit_id 写入 usage 的 segment_id，
        与本函数的输出 identity 一致。切换模式前按分镜 ID（``E1S1`` 等）记的历史支出不在此
        呈现：unit 与分镜之间没有映射关系，无处归属。
        """
        segments_result: list[dict[str, Any]] = []
        ep_est: dict[str, CostBreakdown] = {}
        ep_act: dict[str, CostBreakdown] = {}

        for unit in units:
            if not isinstance(unit, dict):
                continue
            unit_id = str(unit.get("unit_id") or "")
            if not unit_id:
                continue

            slot = precheck_unit(duration_ctx, unit, None)

            est_video = _estimate_unit_video_cost(
                unit_id=unit_id,
                duration_seconds=slot.seconds,
                video_provider=video_provider,
                video_model=video_model,
                video_resolution=video_resolution,
                generate_audio=generate_audio,
                video_price=video_price,
            )

            unit_actual = actual_by_segment.get(unit_id, {})
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
