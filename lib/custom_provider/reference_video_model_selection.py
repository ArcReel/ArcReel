"""参考生视频单元按可用参考图数 N 精确选端点（``docs/adr/0054`` / ``0086``）。

需求：R2V 单元执行时，按**实际喂给该单元的参考图张数 N** 在选中的 custom provider 的 R2V
家族里选 ``max_reference_images == N`` 的那个端点——3 张 → I3，7 张 → I7，严格匹配，不就近、
不裁剪、不回退。映射真相源是每个候选端点解析出的 ``max_reference_images``（节点绑定推导），
不是对 model_id 做字符串模板：I3 的 model_id 是 ``HONGYU-MinimaxH3-I3-A3-First-AutoPrompt``
（无 ``_shot`` 后缀），按名字拼 ``I{N}`` 会在 N=3 时拼错。

家族候选池 = 选中模型所在 provider 下的全部 R2V 候选端点（已启用、media_type=video、R2V
能力位满足）。i2v 的首帧模型（``max_reference_images == 0``）因 R2V 能力位不满足落选。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.resolver import (
    VideoBucketCapabilityError,
    VideoGenerationType,
    video_capability_satisfied,
)
from lib.custom_provider.capabilities import synthesize_video_capabilities
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository

logger = logging.getLogger(__name__)

_R2V: VideoGenerationType = "r2v"


def select_reference_video_model(n: int, candidates: Mapping[int, str]) -> str:
    """按可用参考图数 ``n`` 从家族候选池精确选端点 model_id。

    ``candidates`` 是 ``{max_reference_images: model_id}``（已按 tie-break 收敛）。严格匹配：

    - ``n`` 命中 → 返回 ``candidates[n]``。
    - ``n`` 不命中（含 ``n == 0``、``n > 9``、以及任何 ``1 ≤ n ≤ max(槽数)`` 但精确档位缺失 /
      被禁用的情形）→ 抛 :class:`VideoBucketCapabilityError`，点名所缺的槽数，**绝不**就近 /
      大槽回退到不匹配的端点。

    ``provider`` / ``model`` 两键由调用方在家族装配阶段补齐（无匹配时没有被选中的 model），
    本函数只负责 ``required_slots`` 与「不命中即失败」这条规则，故此处填空身份、由调用方
    就地重抛补齐身份——见 :func:`resolve_reference_video_backend_for_count` 的注释。
    """
    model_id = candidates.get(n)
    if model_id is None:
        raise VideoBucketCapabilityError(
            code="video_capability_missing_r2v",
            generation_type=_R2V,
            provider_id="",
            model_id="",
            message=f"no enabled r2v endpoint provides exactly {n} reference image slot(s)",
            extra_params={"required_slots": n},
        )
    return model_id


async def resolve_reference_video_family(session: AsyncSession, db_provider_id: int) -> dict[int, str]:
    """枚举 ``db_provider_id`` 下满足 R2V 的已启用候选端点，建 ``{max_reference_images: model_id}`` 索引。

    ``db_provider_id`` 是数字 DB id（``custom-2`` → 2 的转换由调用方完成）。每个候选的槽数经
    现有合成点求出（``resolve_endpoint_spec`` → ``synthesize_video_capabilities``），桶归属判定
    复用 :func:`video_capability_satisfied`（R2V 要求 ``max_reference_images > 0``）。某候选的
    端点定义损坏 / 下线（``resolve_endpoint_spec`` 抛 ``ValueError``）时跳过该候选、不致命；
    若家族因此缺某档位，由 :func:`select_reference_video_model` 在选择时 fail loud。

    tie-break（同一 N 多个候选，当前库里不出现）：``is_default`` 为真优先，其次 ``model_id``
    字典序升序。此规则刻意覆盖 ``list_models`` 的 id 升序，使结果不依赖查询排列。
    """
    repo = CustomProviderRepository(session)
    get_endpoint = CustomEndpointRepository(session).get

    # (max_reference_images) -> (is_default, model_id)：按 tie-break 保留胜出者。
    chosen: dict[int, tuple[bool, str]] = {}
    for model in await repo.list_models(db_provider_id):
        if not model.is_enabled:
            continue
        try:
            endpoint_spec = await resolve_endpoint_spec(model.endpoint, get_endpoint)
        except ValueError:
            logger.debug("跳过候选 %s：端点 %s 解析失败", model.model_id, model.endpoint)
            continue
        if endpoint_spec.media_type != "video":
            continue
        try:
            caps = synthesize_video_capabilities(
                endpoint=model.endpoint,
                model_id=model.model_id,
                overrides=model.capability_overrides,
                endpoint_spec=endpoint_spec,
            )
        except ValueError:
            logger.debug("跳过候选 %s：能力合成失败", model.model_id)
            continue
        if not video_capability_satisfied(
            generation_type=_R2V,
            first_frame=caps.first_frame,
            max_reference_images=caps.max_reference_images,
            text_to_video=caps.text_to_video,
        ):
            continue
        slots = caps.max_reference_images
        candidate = (bool(model.is_default), model.model_id)
        incumbent = chosen.get(slots)
        if incumbent is None or _tie_break_prefers(candidate, incumbent):
            chosen[slots] = candidate
    return {slots: model_id for slots, (_is_default, model_id) in chosen.items()}


def _tie_break_prefers(candidate: tuple[bool, str], incumbent: tuple[bool, str]) -> bool:
    """candidate 是否应取代 incumbent：``is_default`` 为真优先，其次 ``model_id`` 字典序升序。"""
    cand_default, cand_model = candidate
    inc_default, inc_model = incumbent
    if cand_default != inc_default:
        return cand_default
    return cand_model < inc_model
