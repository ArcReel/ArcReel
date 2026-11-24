"""百炼视频模型名称的家族、模态与别名归一化规则。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

WAN3_MODEL_KEY = "wan3.0-video"
WAN27_PROFILE_KEYS = ("wan2.7-t2v", "wan2.7-i2v", "wan2.7-r2v")

WAN3_PATTERN = re.compile(r"(?<![a-z0-9])wan[-_]?3(?![a-z0-9])", re.I)

WAN2_PATTERN = re.compile(r"(?<![a-z0-9])wan[-_]?2\.7(?![a-z0-9])", re.I)

WAN_DOT_FORM_PATTERN = re.compile(r"(?<![a-z0-9])wan2\.\d+(?![a-z0-9])", re.I)

HAPPYHORSE_PATTERN = re.compile(r"(?<![a-z0-9])happyhorse(?![a-z0-9])", re.I)

WAN_IMAGE_TO_VIDEO_PATTERN = re.compile(r"(?<![a-z0-9])image[-_]?(?:to|2)[-_]?video(?![a-z0-9])", re.I)

WAN_VIDEOEDIT_PATTERN = re.compile(r"(?<![a-z0-9])video[-_]?edit(?![a-z0-9])", re.I)

WAN_S2V_V2V_PATTERN = re.compile(r"(?<![a-z0-9])[sv]2v(?![a-z0-9])", re.I)

WanFamily = Literal["happyhorse", "wan2.7", "wan3", "wan2x_dot"]


@dataclass(frozen=True)
class WanClassification:
    """model_id 在万相/happyhorse 家族判定链上的结构化结论。

    唯一判定入口——家族归属、分隔符归一化、标识符边界、image-to-video 续接语法、未实现模态排除
    均只在 `classify_wan_model` 里实现一次。端点预填、
    百炼视频能力档、时长档（duration_presets.py）三处判定点都只消费这份
    结论，不得再各自对 model_id 做正则匹配，避免同一家族的边界规则在多处漂移出互斥组合（例如
    命中原生路由却拿到与该路由不兼容的能力档）。
    """

    family: WanFamily | None
    is_image_to_video: bool
    is_videoedit: bool
    profile_key: str | None
    has_known_modality: bool
    is_known_video_modality: bool


def classify_wan_model(model_id: str | None) -> WanClassification:
    """对 model_id 做一次判定，供路由/能力档/时长档复用同一结论。"""
    normalized = (model_id or "").strip().lower()
    if HAPPYHORSE_PATTERN.search(normalized):
        return WanClassification(
            family="happyhorse",
            is_image_to_video=False,
            is_videoedit=False,
            profile_key=None,
            has_known_modality=True,
            is_known_video_modality=False,
        )
    wan3_match = WAN3_PATTERN.search(normalized)
    wan2_match = None if wan3_match else WAN2_PATTERN.search(normalized)
    wan_dot_match = None if (wan3_match or wan2_match) else WAN_DOT_FORM_PATTERN.search(normalized)
    family_match = wan3_match or wan2_match or wan_dot_match
    if wan3_match:
        family: WanFamily = "wan3"
    elif wan2_match:
        family = "wan2.7"
    elif wan_dot_match:
        family = "wan2x_dot"
    else:
        wan_locator = normalized.find("wan")
        fallback_scope = normalized[wan_locator:] if wan_locator != -1 else normalized
        return WanClassification(
            family=None,
            is_image_to_video=bool(WAN_IMAGE_TO_VIDEO_PATTERN.search(fallback_scope)),
            is_videoedit=False,
            profile_key=None,
            has_known_modality=True,
            is_known_video_modality=False,
        )

    assert family_match is not None
    family_suffix = normalized[family_match.start() :]
    is_image_to_video = bool(WAN_IMAGE_TO_VIDEO_PATTERN.search(family_suffix))
    is_videoedit = False
    profile_key: str | None = None
    has_known_modality = True
    is_known_video_modality = False
    if family == "wan3":
        profile_key = WAN3_MODEL_KEY
    elif family == "wan2.7":
        wan27_suffix = _normalize_wan27_alias(family_suffix)
        profile_key = normalized[: family_match.start()] + wan27_suffix
        is_videoedit = bool(WAN_VIDEOEDIT_PATTERN.search(wan27_suffix))
        is_s2v_or_v2v = bool(WAN_S2V_V2V_PATTERN.search(wan27_suffix))
        has_known_modality = (
            not is_videoedit
            and not is_s2v_or_v2v
            and find_known_profile_key(wan27_suffix, WAN27_PROFILE_KEYS) is not None
        )
        is_known_video_modality = has_known_modality or is_videoedit or is_s2v_or_v2v
    elif family == "wan2x_dot" and is_image_to_video:
        has_known_modality = False
    return WanClassification(
        family=family,
        is_image_to_video=is_image_to_video,
        is_videoedit=is_videoedit,
        profile_key=profile_key,
        has_known_modality=has_known_modality,
        is_known_video_modality=is_known_video_modality,
    )


def _normalize_wan27_alias(family_suffix: str) -> str:
    """把 WAN2_PATTERN 命中的 wan2.7 别名折成 _MODEL_PROFILES key 固定使用的形态：
    "wan[-_]?2.7[-_]<modality>"，无论 wan/版本号/模态三段各自用哪种分隔符，统一成
    "wan2.7-<modality>"（modality 含 image-to-video 续接语法时进一步折成 "i2v"）。

    分两步是因为两段分隔符独立可变（"wan_2.7-r2v" 只有前段是下划线、"wan-2.7_r2v" 只有后段
    是下划线、"wan_2.7_r2v" 两段都是），任一段漏归一化都会导致结果不与 _MODEL_PROFILES 的 key
    构成子串关系，静默退回通道声明。调用方须传入从 wan2.7 标记本身开始的子串（不含标记前
    的装饰前缀），且须先用 WAN2_PATTERN.search 确认命中。
    """
    family_suffix = WAN2_PATTERN.sub("wan2.7", family_suffix)
    family_suffix = re.sub(r"wan2\.7_", "wan2.7-", family_suffix)
    return WAN_IMAGE_TO_VIDEO_PATTERN.sub("i2v", family_suffix)


def find_known_profile_key(normalized: str, keys: Iterable[str]) -> str | None:
    """在 normalized 里按标识符边界查找 keys 中出现的第一个已知 key，均未命中则 None。

    两侧边界要求非字母数字：左侧避免 "swan2.7-r2v"（"s" 紧贴 "wan2.7-r2v"）、
    "myhappyhorse-1.0-r2v" 这类第三方型号名被字面子串误吞；右侧避免 "wan2.7-i2vfoo"、
    "happyhorse-1.0-r2vfoo" 这类未知变体后缀被截断误判成已知 key。代理中转的装饰前缀/后缀
    （"proxy/xxx"、"xxx-0715"）靠非字母数字分隔符天然满足两侧边界，不受影响。
    """
    for key in keys:
        if re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", normalized):
            return key
    return None
