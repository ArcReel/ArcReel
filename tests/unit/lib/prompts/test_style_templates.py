"""Tests for lib.prompts.style_templates."""

import pytest

from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.prompts.style_templates import (
    is_known_template,
    list_template_ids,
    list_templates_by_category,
    resolve_template_prompt,
)


def test_templates_count_and_definition_order():
    ids = list_template_ids()
    assert len(ids) == 36
    assert len(set(ids)) == 36
    assert ids[0] == "live_cinematic_ancient"
    assert ids[17] == "live_cyberpunk"
    assert ids[18] == "anim_3d_cg"
    assert ids[-1] == "anim_90s_retro"
    assert all(tpl_id.startswith("live_") for tpl_id in ids[:18])
    assert all(tpl_id.startswith("anim_") for tpl_id in ids[18:])


def test_style_templates_are_slotless_and_grouped_under_style_category():
    entries = [entry for entry in builtin_templates.list_templates() if entry.category == "style"]
    assert [entry.id for entry in entries] == [f"style/{tpl_id}" for tpl_id in list_template_ids()]
    for entry in entries:
        assert entry.slots == {}
        assert entry.applies_to == {}
        assert entry.title.strip()
        assert entry.description.strip()


def test_no_preset_starts_with_huafeng_prefix():
    # Presets never start with 画风：, avoiding stacked English Style: labels rendered as "Style: 画风：".
    # anim_arcane alone is exempt: 画风 belongs to compound 油画三渲二画风, not a removable prefix.
    for tpl_id in list_template_ids():
        prompt = resolve_template_prompt(tpl_id)
        assert prompt.strip()
        assert prompt == prompt.strip()
        if tpl_id == "anim_arcane":
            assert prompt.startswith("Oil-painted cel-shaded 3D look")
            continue
        # Exclude full-/half-width colons alike, matching v13→v14 removal (画风： / 画风:).
        assert not prompt.startswith(("画风：", "画风:")), tpl_id


def test_resolve_template_prompt_ok():
    assert resolve_template_prompt("live_premium_drama") == "真人电视剧风格，精品短剧画风，大师级构图"


@pytest.mark.parametrize("template_id", ["no_such_id", "../asset/sheet", "asset/sheet", ""])
def test_resolve_template_prompt_unknown_raises(template_id):
    assert not is_known_template(template_id)
    with pytest.raises(KeyError):
        resolve_template_prompt(template_id)


def test_list_templates_by_category():
    grouped = list_templates_by_category()
    assert set(grouped.keys()) == {"live", "anim"}
    assert len(grouped["live"]) == 18
    assert len(grouped["anim"]) == 18
    assert grouped["live"][0] == {
        "id": "live_cinematic_ancient",
        "prompt": "Premium live-action period short-drama style, professional lighting, high-quality television-drama texture",
    }
    assert [item["id"] for item in grouped["live"] + grouped["anim"]] == list_template_ids()
