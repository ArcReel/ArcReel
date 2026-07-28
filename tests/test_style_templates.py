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


def test_no_preset_starts_with_huafeng_prefix():
    # 预设值不再以「画风：」开头（避免叠加英文 Style: 标签渲染成 "Style: 画风："）。
    # anim_arcane 是唯一例外：其「画风」是复合词「油画三渲二画风」的一部分，非可删前缀。
    for tpl_id, data in STYLE_TEMPLATES.items():
        if tpl_id == "anim_arcane":
            assert data["prompt"].startswith("油画三渲二画风：")
            continue
        # 全角/半角冒号都要排除，与 normalize_style 的清理口径（画风： / 画风:）一致
        assert not data["prompt"].startswith(("画风：", "画风:")), tpl_id


def test_resolve_template_prompt_ok():
    prompt = resolve_template_prompt("live_premium_drama")
    assert "精品短剧" in prompt or "真人电视剧" in prompt
    assert not prompt.startswith("画风：")


def test_resolve_template_prompt_unknown_raises():
    with pytest.raises(KeyError):
        resolve_template_prompt("no_such_id")


def test_list_templates_by_category():
    grouped = list_templates_by_category()
    assert set(grouped.keys()) == {"live", "anim"}
    assert len(grouped["live"]) == 18
    assert len(grouped["anim"]) == 18
    assert grouped["live"][0]["id"].startswith("live_")
