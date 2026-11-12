"""草稿违约的 Web 本地化：按 code 与 params 渲染，Agent 读到的原文不变。"""

from __future__ import annotations

from functools import partial

import pytest
from pydantic import ValidationError

from lib.i18n import _ as i18n_message
from lib.i18n.display_names import DisplayNames
from lib.script.draft_quarantine import violation_entries
from lib.script.draft_violation import DraftViolation, schema_violations
from lib.script.plan_new_assets import new_asset_violations
from lib.script.reference_video.draft_validation import validate_source_text_anchor
from lib.script.script_models import NarrationScriptPlanDraft, build_drama_normalized_script_model
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


def _web_messages(entries: list[dict], locale: str) -> dict[str, str]:
    result = router_mod.localize_draft_view(
        {"violations": entries}, partial(i18n_message, locale=locale), DisplayNames({}, {}, "Deleted provider")
    )
    assert result is not None
    return {item["params"]["path"]: item["message"] for item in result["violations"]}


def _schema_entries(model, content: dict, root: str, id_field: str | None = None) -> tuple[list[dict], list[str]]:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(content)
    violations = schema_violations(caught.value, content, root, id_field)
    return violation_entries(violations), [str(item) for item in violations]


_SCHEMA_SEGMENT = {
    "segment_id": "E1S01",
    "novel_text": "原文",
    "duration_seconds": 99,
    "characters_in_segment": [],
    "scenes": [],
    "props": [],
}


def test_schema_violations_show_bounds_and_allowed_values_in_each_locale():
    """数值越界写出上限，取值越界写出允许的取值；Agent 读到的 pydantic 原文不变。"""
    narration, narration_agent = _schema_entries(
        NarrationScriptPlanDraft, {"segments": [_SCHEMA_SEGMENT]}, "segments", "segment_id"
    )
    drama, drama_agent = _schema_entries(
        build_drama_normalized_script_model([4, 6, 8]),
        {
            "title": "第一集",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 5,
                    "characters_in_scene": [],
                    "scene_description": "村口",
                    "utterances": [{"kind": "monologue", "text": "你好"}],
                }
            ],
        },
        "scenes",
        "scene_id",
    )
    assert narration_agent == [
        "草稿的 content.segments.0.duration_seconds 不符合产出结构：Input should be less than or equal to 60（当前值：99）"
    ]
    assert drama_agent == [
        "草稿的 content.scenes.0.duration_seconds 不符合产出结构：Input should be 4, 6 or 8（当前值：5）",
        "草稿的 content.scenes.0.utterances.0.kind 不符合产出结构：Input should be 'dialogue' or 'voiceover'（当前值：\"monologue\"）",
    ]
    entries = narration + drama
    assert _web_messages(entries, "zh") == {
        "content.segments.0.duration_seconds": "草稿的 content.segments.0.duration_seconds 不符合产出结构：不能大于 60（当前值：99）。检查必填字段、类型和取值",
        "content.scenes.0.duration_seconds": "草稿的 content.scenes.0.duration_seconds 不符合产出结构：须为以下取值之一：4、6、8（当前值：5）。检查必填字段、类型和取值",
        "content.scenes.0.utterances.0.kind": '草稿的 content.scenes.0.utterances.0.kind 不符合产出结构：须为以下取值之一："dialogue"、"voiceover"（当前值："monologue"）。检查必填字段、类型和取值',
    }
    assert _web_messages(entries, "en") == {
        "content.segments.0.duration_seconds": "Draft content.segments.0.duration_seconds does not match the output structure: must be at most 60 (current value: 99). Check required fields, types, and values.",
        "content.scenes.0.duration_seconds": "Draft content.scenes.0.duration_seconds does not match the output structure: must be one of: 4, 6, 8 (current value: 5). Check required fields, types, and values.",
        "content.scenes.0.utterances.0.kind": 'Draft content.scenes.0.utterances.0.kind does not match the output structure: must be one of: "dialogue", "voiceover" (current value: "monologue"). Check required fields, types, and values.',
    }
    for locale in ("vi", "ru"):
        messages = _web_messages(entries, locale)
        assert "60" in messages["content.segments.0.duration_seconds"]
        assert '"dialogue"' in messages["content.scenes.0.utterances.0.kind"]
        assert not any(code in text for text in messages.values() for code in ("less_than_equal", "literal_error"))


def test_length_limits_read_naturally_for_a_single_character():
    """草稿 schema 的长度下限都是 1，计数文案不能出现「1 characters」这类写法。"""
    content = {
        "title": "第一集",
        "scenes": [],
        "new_assets": [{"type": "character", "name": "", "decision": "register", "reason": "首次出场"}],
    }
    entries, _agent = _schema_entries(build_drama_normalized_script_model([8]), content, "scenes")
    assert _web_messages(entries, "en") == {
        "content.new_assets.0.name": 'Draft content.new_assets.0.name does not match the output structure: minimum length is 1 (current value: ""). Check required fields, types, and values.'
    }
    assert _web_messages(entries, "zh") == {
        "content.new_assets.0.name": '草稿的 content.new_assets.0.name 不符合产出结构：至少需要 1 个字符（当前值：""）。检查必填字段、类型和取值'
    }


def test_schema_types_without_dedicated_copy_show_the_pydantic_reason():
    content = {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "characters_in_scene": [],
                "scene_description": "村口",
                "utterances": [{"kind": "dialogue", "text": "你好"}],
            }
        ],
    }
    entries, _agent = _schema_entries(build_drama_normalized_script_model([8]), content, "scenes", "scene_id")
    assert _web_messages(entries, "en") == {
        "content.scenes.0.utterances.0": "Draft content.scenes.0.utterances.0 does not match the output structure: Value error, dialogue utterance 必须带非空 speaker. Check required fields, types, and values."
    }


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (
            {"path": "content.units.0.duration_seconds", "detail": "less_than_equal (30)"},
            "草稿的 content.units.0.duration_seconds 不符合产出结构：less_than_equal (30)。检查必填字段、类型和取值",
        ),
        (
            {
                "path": "content.units.0.duration_seconds",
                "detail": "less_than_equal (30)",
                "schema_error": {
                    "type": "less_than_equal",
                    "msg": "Input should be less than or equal to 15",
                    "input": 30,
                },
            },
            "草稿的 content.units.0.duration_seconds 不符合产出结构：Input should be less than or equal to 15（当前值：30）。检查必填字段、类型和取值",
        ),
        (
            {
                "path": "content.units.0.kind",
                "detail": "literal_error",
                "schema_error": {
                    "type": "literal_error",
                    "msg": "Input should be 'a' or 'b'",
                    "ctx": {"expected": "a or"},
                },
            },
            "草稿的 content.units.0.kind 不符合产出结构：须为以下取值之一：a or。检查必填字段、类型和取值",
        ),
    ],
    ids=["legacy_entry", "context_missing", "unparsed_choices"],
)
def test_schema_entries_fall_back_without_exposing_placeholders(params, expected):
    """旧条目沿用落盘的 detail；约束参数不全时退回 pydantic 原文；取值列表解析不了时原样显示。"""
    entry = {"code": "schema_invalid", "label": "", "message": "Agent report", "params": params}
    assert _web_messages([entry], "zh") == {params["path"]: expected}
