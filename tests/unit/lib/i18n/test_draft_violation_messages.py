"""Keep every draft diagnostic template aligned with its structured producer params."""

import ast
from pathlib import Path
from string import Formatter

import pytest

from lib.i18n import MESSAGES, SUPPORTED_LOCALES
from lib.script.draft_violation import SCHEMA_ERROR_PARAM, DraftViolation, draft_violation_message_key, violation_items
from lib.script.plan_new_assets import new_asset_violations
from lib.script.reference_video.draft_validation import assert_dialogue_preserved, validate_unit_text
from scripts.audit_tests import dotted

ROOT = Path(__file__).resolve().parents[4]
PRODUCER_ROOTS = ("lib", "server")
PRODUCER_TOKENS = ("DraftViolation", "NewAssetProblem", "SCHEMA_ERROR_PARAM")


def _iter_producer_trees(root: Path = ROOT) -> list[tuple[str, ast.AST]]:
    trees: list[tuple[str, ast.AST]] = []
    for base in PRODUCER_ROOTS:
        base_dir = root / base
        if not base_dir.is_dir():
            continue
        for path in sorted(base_dir.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if not any(token in text for token in PRODUCER_TOKENS):
                continue
            trees.append((path.relative_to(root).as_posix(), ast.parse(text)))
    return trees


def _fields(template: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(template) if field is not None}


def _param_key(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id == "SCHEMA_ERROR_PARAM":
        return SCHEMA_ERROR_PARAM
    return None


def _schema_error_types(root: Path = ROOT):
    """Producer-side shape codes written as literal ``schema_error`` dicts."""
    for _, tree in _iter_producer_trees(root):
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if _param_key(key) == SCHEMA_ERROR_PARAM and isinstance(value, ast.Dict):
                        kinds = {_param_key(k): v for k, v in zip(value.keys, value.values, strict=True)}
                        kind = kinds.get("type")
                        if isinstance(kind, ast.Constant):
                            yield kind.value


def _constructor_aliases(tree: ast.AST) -> dict[str, str]:
    return {
        alias.asname: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname and alias.name in {"DraftViolation", "NewAssetProblem"}
    }


def _producer_contracts(root: Path = ROOT):
    for filename, tree in _iter_producer_trees(root):
        aliases = _constructor_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted(node.func)
            if not name:
                continue
            constructor = aliases.get(name, name.rsplit(".", 1)[-1])
            if constructor == "DraftViolation":
                kwargs = {arg.arg: arg.value for arg in node.keywords}
                code = kwargs.get("code")
                if not isinstance(code, ast.Constant) or code.value == "quarantined":
                    continue
                params = kwargs.get("params")
                assert isinstance(params, ast.Dict), (filename, node.lineno, code.value)
                yield code.value, {key for key in map(_param_key, params.keys) if key is not None}
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "_problem":
                code = node.args[0]
                assert isinstance(code, ast.Constant)
                yield code.value, {"label", *(kw.arg for kw in node.keywords)}
            elif constructor == "NewAssetProblem" and node.args:
                code = node.args[0]
                if isinstance(code, ast.Constant):
                    params = node.args[3]
                    assert isinstance(params, ast.Dict)
                    yield code.value, {key.value for key in params.keys if isinstance(key, ast.Constant)}


@pytest.mark.parametrize(
    ("base", "import_statement", "constructor"),
    [
        ("lib", "from lib.script.draft_violation import DraftViolation", "DraftViolation"),
        ("server", "from lib.script.draft_violation import DraftViolation as DV", "DV"),
        ("lib", "from lib.script import draft_violation", "draft_violation.DraftViolation"),
        ("server", "import lib.script.draft_violation as diagnostics", "diagnostics.DraftViolation"),
    ],
)
def test_unregistered_producer_reports_missing_template(tmp_path: Path, base, import_statement, constructor):
    module = tmp_path / base / "unregistered_validator.py"
    module.parent.mkdir()
    module.write_text(
        f"{import_statement}\n"
        "def check():\n"
        f'    return {constructor}("failure", code="unregistered_violation", params={{"field": "x"}})\n',
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="unregistered_violation"):
        _assert_producer_templates("zh", list(_producer_contracts(tmp_path)))


def _assert_producer_templates(locale, contracts):
    for code, fields in contracts:
        key = draft_violation_message_key(code)
        assert key in MESSAGES[locale], (locale, code)
        if code == "schema_invalid":
            assert SCHEMA_ERROR_PARAM in fields, (locale, code)
            fields = fields - {SCHEMA_ERROR_PARAM}
        assert _fields(MESSAGES[locale][key]) == fields, (locale, code)


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_every_draft_producer_has_a_template_with_exact_param_keys(locale):
    contracts = list(_producer_contracts())
    assert len(contracts) >= 40
    _assert_producer_templates(locale, contracts)


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
