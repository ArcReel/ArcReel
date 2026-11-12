"""Keep every draft diagnostic template aligned with its structured producer params."""

import ast
from pathlib import Path
from string import Formatter

import pytest

from lib.i18n import MESSAGES, SUPPORTED_LOCALES
from lib.script.draft_violation import DraftViolation, violation_items
from lib.script.plan_new_assets import new_asset_violations
from lib.script.reference_video.draft_validation import validate_unit_text

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
                yield code.value, {key.value for key in params.keys if isinstance(key, ast.Constant)}
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
        assert code in MESSAGES[locale], (locale, code)
        assert _fields(MESSAGES[locale][code]) == fields, (locale, code)


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
@pytest.mark.parametrize("code", ["mixed_speech", "needs_replan", "parse_failed", "empty_speaker"])
def test_speech_diagnostic_templates_use_location_and_label(locale, code):
    assert _fields(MESSAGES[locale][code]) == {"label", "location"}


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
            assert _fields(MESSAGES[locale][item.code]) == set(item.params)


def test_speech_diagnostic_params_are_produced_at_the_validation_boundary():
    with pytest.raises(DraftViolation) as caught:
        validate_unit_text(
            "unit E1U01", "A street.\n@[Hero]{Hello}\n{A voiceover}", {"characters": {"Hero": {}}}, max_refs=None
        )
    items = violation_items(caught.value)
    assert items[0].code == "mixed_speech"
    assert set(items[0].params or {}) == {"label", "location"}
