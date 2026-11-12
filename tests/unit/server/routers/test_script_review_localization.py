"""草稿违约的 Web 本地化：按 code 与 params 渲染，Agent 读到的原文不变。"""

from __future__ import annotations

from functools import partial

import pytest

from lib.i18n import _ as i18n_message
from lib.i18n.display_names import DisplayNames
from lib.script.draft_quarantine import violation_entries
from lib.script.draft_violation import DraftViolation
from lib.script.plan_new_assets import new_asset_violations
from lib.script.reference_video.draft_validation import validate_source_text_anchor
from server.routers import script_review as router_mod
from server.text_generation import collect_drama_violations, collect_narration_violations


def test_registered_draft_violation_is_localized_without_changing_agent_message():
    """Web renders structured diagnostics; the Agent exception retains its exact report."""
    with pytest.raises(DraftViolation) as caught:
        validate_source_text_anchor("unit E1U01", "", "Original text")
    original = "unit E1U01 的 source_text 为空：每个 unit 必须摘录其所依据的原文片段作为追溯锚"
    assert str(caught.value) == original
    view = {"violations": violation_entries([caught.value])}
    localized = router_mod.localize_draft_view(
        view, partial(i18n_message, locale="en"), DisplayNames({}, {}, "Deleted provider")
    )
    assert localized is not None
    assert (
        localized["violations"][0]["message"]
        == "unit E1U01: source_text is empty. Copy the original passage used by this video unit."
    )
    assert str(caught.value) == original


def test_unregistered_draft_codes_keep_the_original_message_even_with_params():
    original = "Original diagnostic from a future producer"
    view = {"violations": [{"code": "future_draft_problem", "params": {"value": 1}, "message": original}]}
    result = router_mod.localize_draft_view(
        view, partial(i18n_message, locale="en"), DisplayNames({}, {}, "Deleted provider")
    )
    assert result is not None
    assert result["violations"][0]["message"] == original


@pytest.mark.parametrize("route", ["drama", "narration"])
def test_storyboard_diagnostics_localize_without_changing_agent_reports(route):
    if route == "drama":
        items = collect_drama_violations(
            [{"scene_id": "E1S01", "characters_in_scene": ["Missing"]}], project={}, new_assets=[]
        )
        expected = "scene E1S01 的 characters_in_scene 引用了未登记的资产名: ['Missing']；资产名必须逐字取自已登记资产，或把它列进 new_assets 并给出处理决定"
    else:
        items = collect_narration_violations(
            [{"segment_id": "E1S01", "novel_text": " ", "duration_seconds": 8}],
            episode=1,
            supported_durations=[8],
            project={},
            new_assets=[],
            novel_text="Original",
            source_scope="源文件 source.txt",
            source_path="source.txt",
        )
        expected = "segment E1S01 的 novel_text 为空白；每个分镜必须携带逐字取自原文的旁白正文"
    assert str(items[0]) == expected
    result = router_mod.localize_draft_view(
        {"violations": violation_entries(items)},
        partial(i18n_message, locale="en"),
        DisplayNames({}, {}, "Deleted provider"),
    )
    assert result is not None
    assert all(not any("\u4e00" <= ch <= "\u9fff" for ch in item["message"]) for item in result["violations"])
    assert str(items[0]) == expected


def test_new_asset_type_renders_as_the_locale_display_name():
    items = new_asset_violations(
        {},
        [
            {
                "type": "character",
                "name": "Hero",
                "description": "A hero",
                "reason": "First",
                "decision": "merge",
                "target": "Missing",
            }
        ],
    )
    result = router_mod.localize_draft_view(
        {"violations": violation_entries(items)},
        partial(i18n_message, locale="zh"),
        DisplayNames({}, {}, "Deleted provider"),
    )
    assert result is not None
    message = result["violations"][0]["message"]
    assert "（角色）" in message
    assert "character" not in message


def test_cross_type_name_collision_names_the_colliding_asset_type():
    items = new_asset_violations(
        {"scenes": {"Hall": {"description": "大厅"}}},
        [{"type": "character", "name": "Hall", "description": "A hero", "reason": "First", "decision": "register"}],
    )
    assert (
        str(items[0]) == "new_assets[0]：「Hall」已是场景的名字；请改正该项的 decision / target，或改用 register / skip"
    )
    result = router_mod.localize_draft_view(
        {"violations": violation_entries(items)},
        partial(i18n_message, locale="zh"),
        DisplayNames({}, {}, "Deleted provider"),
    )
    assert result is not None
    assert result["violations"][0]["message"] == "new_assets[0] 的名称「Hall」与场景「Hall」重名。改用其他名称"
