"""lib.style_templates 的测试。"""

import pytest

from lib.style_templates import (
    LEGACY_STYLE_MAP,
    STYLE_TEMPLATES,
    list_templates_by_category,
    resolve_template_prompt,
)


def test_templates_count_and_categories():
    assert len(STYLE_TEMPLATES) == 36
    lives = [t for t in STYLE_TEMPLATES.values() if t["category"] == "live"]
    anims = [t for t in STYLE_TEMPLATES.values() if t["category"] == "anim"]
    assert len(lives) == 18
    assert len(anims) == 18


def test_template_ids_unique_and_slug_shaped():
    for tpl_id, data in STYLE_TEMPLATES.items():
        assert tpl_id.startswith(("live_", "anim_")), tpl_id
        assert "prompt" in data and data["prompt"].strip()
        assert data["category"] in ("live", "anim")


def test_legacy_map_targets_exist():
    for legacy, tpl_id in LEGACY_STYLE_MAP.items():
        assert tpl_id in STYLE_TEMPLATES, f"{legacy} -> {tpl_id} 不在 registry"
    assert LEGACY_STYLE_MAP["Photographic"] == "live_premium_drama"
    assert LEGACY_STYLE_MAP["Anime"] == "anim_kyoto"
    assert LEGACY_STYLE_MAP["3D Animation"] == "anim_3d_cg"


def test_resolve_template_prompt_ok():
    prompt = resolve_template_prompt("live_premium_drama")
    assert "精品短剧" in prompt or "真人电视剧" in prompt


def test_resolve_template_prompt_unknown_raises():
    with pytest.raises(KeyError):
        resolve_template_prompt("no_such_id")


def test_list_templates_by_category():
    grouped = list_templates_by_category()
    assert set(grouped.keys()) == {"live", "anim"}
    assert len(grouped["live"]) == 18
    assert len(grouped["anim"]) == 18
    assert grouped["live"][0]["id"].startswith("live_")
