"""assets namespace 注册到 MESSAGES。"""

from __future__ import annotations

from lib.i18n import MESSAGES, _


def test_asset_not_found_key_present_both_locales():
    zh = _("asset_not_found", locale="zh", name="X")
    assert "资产" in zh and "X" in zh
    en = _("asset_not_found", locale="en", name="X")
    assert "X" in en


def test_all_asset_keys_registered_in_both_locales():
    expected = {
        "asset_not_found",
        "asset_already_exists",
        "asset_invalid_type",
        "asset_upload_too_large",
        "asset_unsupported_format",
        "asset_source_resource_not_found",
        "asset_target_project_not_found",
    }
    for key in expected:
        assert key in MESSAGES["zh"], f"missing zh key: {key}"
        assert key in MESSAGES["en"], f"missing en key: {key}"
