"""共享 helper ``lib.asset_types.localize_asset_type`` 的映射与降级语义单测。"""

import pytest

from lib.asset_types import ASSET_SPECS, localize_asset_type
from lib.i18n import _ as translate_message

pytestmark = pytest.mark.unit


def _translator(locale: str):
    def translate(key: str, **kwargs: object) -> str:
        return translate_message(key, locale=locale, **kwargs)

    return translate


class TestLocalizeAssetType:
    @pytest.mark.parametrize("asset_type", sorted(ASSET_SPECS))
    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    def test_registered_type_renders_display_name(self, asset_type: str, locale: str):
        rendered = localize_asset_type(asset_type, _translator(locale))

        assert rendered == translate_message(f"asset_type_{asset_type}", locale=locale)

    def test_unregistered_type_passes_through_unmapped(self):
        """未登记值原样透传，不做语义映射，也不回落成 ``asset_type_widget`` 这样的 key。"""
        assert localize_asset_type("widget", _translator("zh")) == "widget"
