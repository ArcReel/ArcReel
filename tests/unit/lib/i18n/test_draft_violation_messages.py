"""Keep every draft diagnostic template aligned with its structured producer params."""

import ast
from pathlib import Path
from string import Formatter

import pytest

from lib.i18n import MESSAGES, SUPPORTED_LOCALES
from lib.script.draft_violation import SCHEMA_ERROR_PARAM, DraftViolation, draft_violation_message_key, violation_items
from lib.script.plan_new_assets import new_asset_violations
from lib.script.reference_video.draft_validation import assert_dialogue_preserved, validate_unit_text

ROOT = Path(__file__).resolve().parents[4]
PRODUCERS = (
    "lib/script/draft_violation.py",
    "lib/script/reference_video/draft_validation.py",
    "lib/script/script_generator.py",
    "server/text_generation.py",
    "server/draft_workflow.py",
    "lib/script/plan_new_assets.py",
)


def _fields(template: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(template) if field is not None}


def _param_key(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id == "SCHEMA_ERROR_PARAM":
        return SCHEMA_ERROR_PARAM
    return None


def _schema_error_types():
    """Producer-side shape codes written as literal ``schema_error`` dicts."""
    for filename in PRODUCERS:
        for node in ast.walk(ast.parse((ROOT / filename).read_text())):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if _param_key(key) == SCHEMA_ERROR_PARAM and isinstance(value, ast.Dict):
                        kinds = {_param_key(k): v for k, v in zip(value.keys, value.values, strict=True)}
                        kind = kinds.get("type")
                        if isinstance(kind, ast.Constant):
                            yield kind.value


def _producer_contracts():
    for filename in PRODUCERS:
        for node in ast.walk(ast.parse((ROOT / filename).read_text())):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "DraftViolation":
                kwargs = {arg.arg: arg.value for arg in node.keywords}
                code = kwargs.get("code")
                if not isinstance(code, ast.Constant) or code.value == "quarantined":
                    # Aggregated reports are control-flow exceptions, not stored entries.
                    continue
                params = kwargs.get("params")
                assert isinstance(params, ast.Dict), (filename, node.lineno, code.value)
                yield code.value, {key for key in map(_param_key, params.keys) if key is not None}
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "_problem":
                code = node.args[0]
                assert isinstance(code, ast.Constant)
                yield code.value, {"label", *(kw.arg for kw in node.keywords)}
            elif isinstance(node.func, ast.Name) and node.func.id == "NewAssetProblem" and node.args:
                code = node.args[0]
                if isinstance(code, ast.Constant):
                    params = node.args[3]
                    assert isinstance(params, ast.Dict)
                    yield code.value, {key.value for key in params.keys if isinstance(key, ast.Constant)}


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_every_draft_producer_has_a_template_with_exact_param_keys(locale):
    contracts = list(_producer_contracts())
    assert len(contracts) >= 40
    for code, fields in contracts:
        key = draft_violation_message_key(code)
        assert key in MESSAGES[locale], (locale, code)
        if code == "schema_invalid":
            # The structured schema reason is rendered into {detail}; the template never names it.
            assert SCHEMA_ERROR_PARAM in fields, (locale, code)
            fields = fields - {SCHEMA_ERROR_PARAM}
        assert _fields(MESSAGES[locale][key]) == fields, (locale, code)


def test_schema_reason_templates_share_placeholders_across_locales():
    keys = {key for key in MESSAGES["zh"] if key.startswith("draft_schema_")}
    assert {"draft_schema_with_value", "draft_schema_choice_separator"} <= keys
    for kind in _schema_error_types():
        assert f"draft_schema_{kind}" in keys, kind
    for key in keys:
        for locale in SUPPORTED_LOCALES:
            assert _fields(MESSAGES[locale][key]) == _fields(MESSAGES["zh"][key]), (locale, key)


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
@pytest.mark.parametrize("code", ["mixed_speech", "needs_replan", "parse_failed", "empty_speaker"])
def test_speech_diagnostic_templates_use_location_and_label(locale, code):
    assert _fields(MESSAGES[locale][draft_violation_message_key(code)]) == {"label", "location"}


def test_dynamic_asset_problem_params_reach_draft_entries():
    violations = new_asset_violations(
        {},
        [
            {
                "type": "scene",
                "name": "Hall",
                "description": "A hall",
                "reason": "First appearance",
                "decision": "derivative",
                "target": "Hero",
            },
            {
                "type": "character",
                "name": "Hero",
                "description": "A hero",
                "reason": "First appearance",
                "decision": "merge",
                "target": "Missing",
            },
        ],
    )
    assert {item.code for item in violations} == {"new_asset_derivative_not_character", "new_asset_target_unresolved"}
    for item in violations:
        assert item.params is not None
        for locale in SUPPORTED_LOCALES:
            assert _fields(MESSAGES[locale][draft_violation_message_key(item.code)]) == set(item.params)


def test_speech_diagnostic_params_are_produced_at_the_validation_boundary():
    with pytest.raises(DraftViolation) as caught:
        validate_unit_text(
            "unit E1U01", "A street.\n@[Hero]{Hello}\n{A voiceover}", {"characters": {"Hero": {}}}, max_refs=None
        )
    items = violation_items(caught.value)
    assert items[0].code == "mixed_speech"
    assert items[0].params == {"label": "unit E1U01", "location": "text:2, text:3"}
    assert "定位：text line 1, text line 2" in str(items[0])


def test_rewritten_voiceover_keeps_script_syntax_in_web_params():
    with pytest.raises(DraftViolation) as caught:
        assert_dialogue_preserved("unit E1U01", "门开了\n{旧旁白}", "门开了\n@[张三]{新台词}")
    assert caught.value.params == {"label": "unit E1U01", "index": 1, "before": "{旧旁白}", "after": "@[张三]{新台词}"}
    assert "原：画外音「旧旁白」" in str(caught.value)
