"""资产布局模板——按 asset_type 套三视图 / 主+细节 / 多视角。"""

CHARACTER_LAYOUT = "三个等比例全身像水平排列在纯净浅灰背景上：左侧正面、中间四分之三侧面、右侧纯侧面。柔和均匀的摄影棚照明，无强烈阴影。"
SCENE_LAYOUT = "主画面占据四分之三区域展示环境整体外观与氛围，右下角小图为关键细节特写。柔和自然光线。"
PROP_LAYOUT = "三个视图水平排列在纯净浅灰背景上：正面全视图、45 度侧视图展示立体感、关键细节特写。柔和均匀的摄影棚照明，色彩准确。"

_LAYOUT_MAP = {
    "character": CHARACTER_LAYOUT,
    "scene": SCENE_LAYOUT,
    "prop": PROP_LAYOUT,
}


def layout_for(asset_type: str) -> str:
    if asset_type not in _LAYOUT_MAP:
        raise ValueError(f"unknown asset_type: {asset_type!r}")
    return _LAYOUT_MAP[asset_type]


__all__ = [
    "CHARACTER_LAYOUT",
    "SCENE_LAYOUT",
    "PROP_LAYOUT",
    "layout_for",
]
