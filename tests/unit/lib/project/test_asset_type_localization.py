"""Mapping/fallback unit tests for shared helper ``lib.project.asset_types.localize_asset_type``."""

import pytest

from lib.i18n import _ as translate_message
from lib.project.asset_types import ASSET_SPECS, localize_asset_type

# Explicit expected values independent of the helper and translate_message implementation: missing asset_type_*
# keys make translate_message fall back to the key. Recomputing expectations through that same function would hide
# missing translations, so use handwritten literals as the oracle.
_EXPECTED_DISPLAY_NAMES = {
    "zh": {"character": "角色", "scene": "场景", "prop": "道具", "product": "商品"},
    "en": {"character": "character", "scene": "scene", "prop": "prop", "product": "merchandise"},
    "vi": {"character": "nhân vật", "scene": "cảnh", "prop": "đạo cụ", "product": "hàng hóa"},
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
        """Pass unknown values unchanged without semantic mapping or fallback to keys such as ``asset_type_widget``."""
        assert localize_asset_type("widget", _translator("zh")) == "widget"
