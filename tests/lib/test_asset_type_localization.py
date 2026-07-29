"""共享 helper ``lib.asset_types.localize_asset_type`` 的映射与降级语义单测。"""

import pytest

from lib.asset_types import ASSET_SPECS, localize_asset_type
from lib.i18n import _ as translate_message

pytestmark = pytest.mark.unit

# 独立于被测 helper 与 translate_message 内部实现的显式期望值——若某个 asset_type_*
# key 缺失，translate_message 会回退成 key 本身，用它自己再算一遍期望值会让测试对
# 漏翻译失明，因此这里手写字面量作为 oracle。
_EXPECTED_DISPLAY_NAMES = {
    "zh": {"character": "角色", "scene": "场景", "prop": "道具", "product": "产品"},
    "en": {"character": "character", "scene": "scene", "prop": "prop", "product": "product"},
    "vi": {"character": "nhân vật", "scene": "cảnh", "prop": "đạo cụ", "product": "sản phẩm"},
}


def _translator(locale: str):
    def translate(key: str, **kwargs: object) -> str:
        return translate_message(key, locale=locale, **kwargs)

    return translate


class TestLocalizeAssetType:
    @pytest.mark.parametrize("asset_type", sorted(ASSET_SPECS))
    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    def test_registered_type_renders_display_name(self, asset_type: str, locale: str):
        rendered = localize_asset_type(asset_type, _translator(locale))

        assert rendered == _EXPECTED_DISPLAY_NAMES[locale][asset_type]

    def test_unregistered_type_passes_through_unmapped(self):
        """未登记值原样透传，不做语义映射，也不回落成 ``asset_type_widget`` 这样的 key。"""
        assert localize_asset_type("widget", _translator("zh")) == "widget"
