"""资产防崩规则——正向（append 到 description 末尾）+ 负向（payload.negative_prompt）。"""

CHARACTER_POSITIVE = "人物五官对称、身体结构正常、手指完整为五指、肢体比例协调、面部特征清晰、服装造型完整无穿帮。"
SCENE_POSITIVE = "场景结构完整、空间透视正常、陈设固定、光影统一、无元素错位。"
PROP_POSITIVE = "道具结构完整、外观特征清晰、无变形扭曲、焦点明确。"

NEGATIVE_BASE = "畸形, 多肢体, 多指, 断指, 五官扭曲, 面部崩坏, 乱码文字, 水印, 模糊, 低分辨率, 穿帮元素, 严重色差"

_POSITIVE_MAP = {
    "character": CHARACTER_POSITIVE,
    "scene": SCENE_POSITIVE,
    "prop": PROP_POSITIVE,
}


def positive_for(asset_type: str) -> str:
    if asset_type not in _POSITIVE_MAP:
        raise ValueError(f"unknown asset_type: {asset_type!r}")
    return _POSITIVE_MAP[asset_type]


def negative_for(asset_type: str) -> str:
    if asset_type not in _POSITIVE_MAP:
        raise ValueError(f"unknown asset_type: {asset_type!r}")
    return NEGATIVE_BASE


__all__ = [
    "CHARACTER_POSITIVE",
    "SCENE_POSITIVE",
    "PROP_POSITIVE",
    "NEGATIVE_BASE",
    "positive_for",
    "negative_for",
]
