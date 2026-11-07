"""Capability failures persist code+params and localize at read time.

Protect:
1. `CAPABILITY_FAILURE_CODES` covers every capability-error raise site through AST drift guards.
2. One persisted error_message renders in each zh/en/vi language."""

from __future__ import annotations

import ast
import json
import string
from collections import Counter
from pathlib import Path

import httpx
import pytest

from arcreel_market_core.video_backend_contract import VideoCapabilityError
from lib.backends.http_status_errors import ArtifactDownloadError, ProviderRejectedError
from lib.backends.image_backends.base import ImageCapabilityError
from lib.config.resolver import ImageBucketCapabilityError, VideoBucketCapabilityError, VideoGenerationType
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.db.repositories.task_repo import _encode_bounded_cascade_failure
from lib.generation import task_failure
from lib.generation.task_failure import (
    CAPABILITY_FAILURE_CODES,
    FAILURE_CODE_KEYS,
    REFERENCE_PROJECTION_FAILURE_CODES,
    VIDEO_REQUEST_FACTS_FAILURE_CODES,
    bound_reason,
    collapse_cascade_reason,
    encode_failure,
    render_failure,
)
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.generation.video_request_facts import VideoRequestFactsError, VideoRequestFactsFailure
from lib.i18n import MESSAGES
from lib.i18n import _ as i18n_translate
from lib.references.reference_compression import ReferencePayloadFloorError
from lib.script.reference_video.request_projection import ProjectionProblem, ReferenceProjectionBlockedError
from tests.factories import make_display_names

# AST guards scan real source trees and rendering assertions use real i18n catalogs; no tested entry points are mocked.

_CAPABILITY_EXCEPTIONS = {"ImageCapabilityError", "VideoCapabilityError", "ReferencePayloadFloorError"}
_SCANNED_ROOTS = ("lib", "server")
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Collect capability-exception import aliases in this module, mapping original class names.

    After ``from ... import VideoCapabilityError as VCE``, calls use only ``VCE``. Ignoring aliases misses both guards; ``_try_encode_failure()`` then falls back to ``str(exc)`` and leaks raw machine codes into UI."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom | ast.Import):
            continue
        for alias in node.names:
            original = alias.name.rsplit(".", 1)[-1]
            if original in _CAPABILITY_EXCEPTIONS and alias.asname:
                aliases[alias.asname] = original
    return aliases


def _exception_name(func: ast.expr, aliases: dict[str, str]) -> str | None:
    """Get constructor class names. Treat `VideoCapabilityError(...)`, `base.VideoCapabilityError(...)` and imported `VCE(...)` alike, resolving aliases through ``aliases``."""
    if isinstance(func, ast.Name):
        if func.id in _CAPABILITY_EXCEPTIONS:
            return func.id
        return aliases.get(func.id)
    if isinstance(func, ast.Attribute):
        if func.attr in _CAPABILITY_EXCEPTIONS:
            return func.attr
        return aliases.get(func.attr)
    return None


def _enclosing_function(tree: ast.Module, lineno: int) -> str:
    """Get the innermost function enclosing ``lineno``, or ``<module>`` outside functions."""
    innermost = "<module>"
    best_start = -1
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        end = node.end_lineno if node.end_lineno is not None else node.lineno
        if node.lineno <= lineno <= end and node.lineno > best_start:
            innermost = node.name
            best_start = node.lineno
    return innermost


def _static_str_choices(node: ast.expr) -> list[str] | None:
    """Enumerate possible code literals, returning ``None`` if incomplete.

    Accept only string constants and conditionals (``"a" if cond else "b"``) with both branches literal. Merely finding any ``ast.walk`` string would fail open on ``"lit" if cond else dynamic_code``: neither guard catches unregistered dynamic values, leaking raw machine codes."""
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else None
    if isinstance(node, ast.IfExp):
        body = _static_str_choices(node.body)
        orelse = _static_str_choices(node.orelse)
        if body is None or orelse is None:
            return None
        return body + orelse
    return None


def _keyword_value(node: ast.Call, name: str) -> ast.expr | None:
    """Get the expression supplied for ``name=`` or return ``None``."""
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _scan_tree(tree: ast.Module, rel: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Scan one AST for capability-error constructors.

    Return code → first location and unscannable-code sites. Cover direct positional/``code=`` literals, including conditional ``"a" if cond else "b"``, and agnes-style ``error_code=`` forwarding to internal helpers whose constructor uses variables but callers provide literals."""
    codes: dict[str, str] = {}
    dynamic_sites: list[tuple[str, str]] = []
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    aliases = _import_aliases(tree)
    constructions = [(node, name) for node in calls if (name := _exception_name(node.func, aliases)) is not None]
    if not constructions:
        return codes, dynamic_sites

    for node, name in constructions:
        where = f"{rel}:{node.lineno}"
        # All three capability exceptions name their first argument ``code`` and allow positional/keyword forms. Positional-only scanning would
        # register ``ReferencePayloadFloorError(code="new")`` as default/no-arguments,
        # neither discovering the real new code nor marking a dynamic site, defeating both guards.
        code_expr = node.args[0] if node.args else _keyword_value(node, "code")
        if code_expr is None:
            # ``**kwargs`` may hide code from static scans. Never treat it as absent:
            # ``ReferencePayloadFloorError(**{"code": "new"})`` would otherwise register the default while
            # both guards miss the new code. With no positional/explicit ``code=``, accept defaults only when
            # unpacking is definitely absent.
            if any(keyword.arg is None for keyword in node.keywords):
                dynamic_sites.append((where, f"{rel}::{_enclosing_function(tree, node.lineno)}"))
            elif name == "ReferencePayloadFloorError":
                # Only capability exception with a default code, frequently omitted at construction.
                codes.setdefault(ReferencePayloadFloorError().code, where)
            else:
                dynamic_sites.append((where, f"{rel}::{_enclosing_function(tree, node.lineno)}"))
            continue
        literals = _static_str_choices(code_expr)
        if literals is not None:
            for literal in literals:
                codes.setdefault(literal, where)
        else:
            # Variable/module-constant codes or conditional expressions with dynamic branches evade static enumeration;
            # record and assert them separately to prevent registration drift escaping.
            dynamic_sites.append((where, f"{rel}::{_enclosing_function(tree, node.lineno)}"))

    # Collect ``error_code=`` only from files with actual capability constructors, avoiding unrelated same-name kwargs
    # and false unregistered-code positives. Mark nonliteral error_code as unscannable too:
    # helper constructors are scope-exempt, so silently admitting callers would defeat both guards
    # and let unregistered raw machine codes reach users.
    for node in calls:
        error_code = _keyword_value(node, "error_code")
        if error_code is None:
            continue
        literals = _static_str_choices(error_code)
        if literals is not None:
            for literal in literals:
                codes.setdefault(literal, f"{rel}:{node.lineno}")
        else:
            dynamic_sites.append((f"{rel}:{node.lineno}", f"{rel}::{_enclosing_function(tree, node.lineno)}"))
    return codes, dynamic_sites


def _scan_raised_codes() -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Scan all lib/ + server/ capability-error construction sites."""
    codes: dict[str, str] = {}
    dynamic_sites: list[tuple[str, str]] = []
    for root in _SCANNED_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found, dynamic = _scan_tree(tree, str(path.relative_to(_REPO_ROOT)))
            for code, where in found.items():
                codes.setdefault(code, where)
            dynamic_sites.extend(dynamic)
    return codes, dynamic_sites


# Exempt dynamic variable/parameter constructor sites by file::enclosing-function, not line number
# (unrelated edits move lines) or whole file (new dynamic sites would inherit blanket exemption).
# Allow only same-file helpers fed by ``error_code=`` literals: helper itself is unscannable,
# but every caller is covered above. Values are permitted dynamic-site counts per scope:
# quotas, never blank checks; extra sites must justify themselves rather than slipping through.
_ALLOWED_DYNAMIC_SCOPES = {"lib/backends/video_backends/agnes.py::_encode_image": 2}


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ('"video_duration_invalid"', ["video_duration_invalid"]),
        ('"a" if cond else "b"', ["a", "b"]),
        ('"a" if cond else dynamic_code', None),
        ('dynamic_code if cond else "b"', None),
        ("dynamic_code", None),
        ('CODES["a"]', None),
        ('f"video_{suffix}"', None),
    ],
)
def test_static_code_enumeration_rejects_partially_dynamic_expressions(expr, expected):
    """Classify literal only if the entire expression is enumerable; any dynamic conditional branch is unscannable, or its unregistered values would bypass both drift guards."""
    assert _static_str_choices(ast.parse(expr, mode="eval").body) == expected


@pytest.mark.parametrize(
    ("source", "expected_codes", "expected_dynamic"),
    [
        ('VideoCapabilityError("video_duration_not_supported")', ["video_duration_not_supported"], 0),
        ('VideoCapabilityError(code="video_duration_not_supported")', ["video_duration_not_supported"], 0),
        ("VideoCapabilityError(code=chosen)", [], 1),
        # ``**`` may hide code: do not register a default as though code were absent.
        ('ReferencePayloadFloorError(**{"code": "new_code"})', [], 1),
        ("VideoCapabilityError(**overrides)", [], 1),
        # Defaults apply only when code truly is absent; supplied keywords must be scanned.
        ("ReferencePayloadFloorError()", ["ref_payload_floor_exceeded"], 0),
        ('ReferencePayloadFloorError(code="new_code")', ["new_code"], 0),
        ("ReferencePayloadFloorError(code=chosen)", [], 1),
        ('_encode_image(error_code="image_capability_missing_i2i")', ["image_capability_missing_i2i"], 0),
    ],
)
def test_scanner_reads_code_from_keyword_form(source, expected_codes, expected_dynamic):
    """Treat keyword ``code=`` like positional arguments and fail closed on nonliterals.

    Positional-only scans would register ``ReferencePayloadFloorError(code="new_code")`` as default, neither discovering the new code nor marking it dynamic, defeating both guards."""
    # Collect ``error_code=`` only in files with capability constructors; add one background constructor to synthetic source.
    # Background codes differ from all cases; assert complete equality, never set subtraction,
    # or matching tested/background codes would disappear together and empty collections could pass.
    prelude = 'VideoCapabilityError("video_duration_invalid")\n'
    codes, dynamic = _scan_tree(ast.parse(prelude + source), "synthetic.py")

    assert sorted(codes) == sorted({"video_duration_invalid", *expected_codes})
    assert len(dynamic) == expected_dynamic


@pytest.mark.parametrize(
    ("import_line", "call"),
    [
        ("from arcreel_market_core.video_backend_contract import VideoCapabilityError as VCE", 'VCE("new_code")'),
        ("import arcreel_market_core.video_backend_contract as base", 'base.VideoCapabilityError("new_code")'),
        (
            "from lib.references.reference_compression import ReferencePayloadFloorError as RPFE",
            'RPFE(code="new_code")',
        ),
    ],
)
def test_scanner_resolves_import_aliases(import_line, call):
    """Scan constructors imported with ``as`` aliases too, or both guards miss them."""
    codes, dynamic = _scan_tree(ast.parse(f"{import_line}\n{call}\n"), "synthetic.py")

    assert sorted(codes) == ["new_code"]
    assert dynamic == []


def test_capability_codes_registered_no_drift():
    """New unregistered capability codes fail CI rather than degrading through KeyError only during production persistence."""
    raised, _ = _scan_raised_codes()
    unregistered = {code: where for code, where in raised.items() if code not in CAPABILITY_FAILURE_CODES}
    assert not unregistered, f"以下 capability code 未登记进 CAPABILITY_FAILURE_CODES: {unregistered}"

    stale = CAPABILITY_FAILURE_CODES - raised.keys()
    assert not stale, f"以下已登记 code 在代码里已无 raise 点，应清理: {sorted(stale)}"


def test_no_unscannable_capability_construction_sites():
    """Constructor codes must be statically scannable or drift guards fail open. New dynamic forms become literals or explicit allowlist entries proving registered codes."""
    _, dynamic_sites = _scan_raised_codes()
    unexpected = sorted({where for where, scope in dynamic_sites if scope not in _ALLOWED_DYNAMIC_SCOPES})
    assert not unexpected, f"以下构造点的 code 非字面量，漂移守卫扫不到: {unexpected}"

    counted = Counter(scope for _, scope in dynamic_sites)
    assert counted == Counter(_ALLOWED_DYNAMIC_SCOPES), (
        f"白名单 scope 的动态构造点数量与定额不符，新增的那个未自证 code 已登记: {counted}"
    )


def _template_placeholders(template: str) -> set[str]:
    """Extract named template placeholders."""
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def _scan_param_contracts() -> list[tuple[str, str, set[str]]]:
    """Collect statically visible constructors as (location, code, explicit parameter names).

    Only direct constructors with visible code/kwargs qualify. ``**`` hides arguments and would cause false positives; helper-forwarded ``error_code=`` sites assemble params inside helpers and are excluded too."""
    contracts: list[tuple[str, str, set[str]]] = []
    for root in _SCANNED_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            rel = str(path.relative_to(_REPO_ROOT))
            tree = ast.parse(path.read_text(encoding="utf-8"))
            aliases = _import_aliases(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _exception_name(node.func, aliases) is None:
                    continue
                if any(keyword.arg is None for keyword in node.keywords):
                    continue
                code_expr = node.args[0] if node.args else _keyword_value(node, "code")
                literals = _static_str_choices(code_expr) if code_expr is not None else None
                if code_expr is None:
                    literals = [ReferencePayloadFloorError().code]
                if literals is None:
                    continue
                provided = {keyword.arg for keyword in node.keywords if keyword.arg and keyword.arg != "code"}
                contracts.extend((f"{rel}:{node.lineno}", literal, provided) for literal in literals)
    return contracts


def test_capability_construction_sites_supply_every_template_param():
    """Constructors supply every placeholder required by all three locales, preventing visible raw ``{name}``.

    ``lib/i18n/__init__.py::_`` swallows ``format`` failures and returns unformatted templates, so missing parameters fail silently. Registered-code and three-locale-template guards check key existence only and cannot catch omissions."""
    missing: list[str] = []
    for where, code, provided in _scan_param_contracts():
        for locale in ("zh", "en", "vi"):
            template = MESSAGES[locale].get(code)
            if template is None:
                continue  # test_capability_code_has_all_three_locales covers missing templates.
            absent = _template_placeholders(template) - provided
            if absent:
                missing.append(f"{where} ({code}, {locale}) 缺参数 {sorted(absent)}")
    assert not missing, "以下构造点漏传模板参数，读侧会渲染出裸占位符:\n" + "\n".join(missing)


@pytest.mark.parametrize("code", sorted(CAPABILITY_FAILURE_CODES))
def test_capability_code_has_all_three_locales(code):
    """Each code is an errors-catalog key (identity mapping), requiring templates in all three locales."""
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


def _translator(locale: str):
    """Mirror ``lib.i18n.get_translator`` by binding locale, as read paths do."""

    def translate(key: str, **params) -> str:
        return i18n_translate(key, locale=locale, **params)

    return translate


def test_stored_reason_renders_per_locale():
    """One persisted error_message renders into three localized messages."""
    stored = encode_task_failure_message(
        VideoCapabilityError("video_duration_not_supported", duration=7, supported="5, 10")
    )
    assert stored == '[video_duration_not_supported] {"duration": 7, "supported": "5, 10"}'

    for locale in ("zh", "en", "vi"):
        # Derive expected text independently from each locale catalog, never compare rendered outputs only:
        # three different strings could still pass swapped zh/en/vi templates or locale mappings.
        expected = MESSAGES[locale]["video_duration_not_supported"].format(duration=7, supported="5, 10")
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == expected


def test_artifact_download_failure_is_eligible_for_retry_download():
    stored = encode_task_failure_message(ArtifactDownloadError(detail="cdn unavailable"))

    assert stored == '[artifact_download_failed] {"detail": "cdn unavailable"}'
    for locale in ("zh", "en", "vi"):
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == MESSAGES[locale][
            "task_fail_artifact_download_failed"
        ].format(detail="cdn unavailable")


def test_an_endpoint_whose_media_runtime_is_missing_fails_in_the_reader_s_language():
    """Invalid ComfyUI media types outside its two schema values (manual DB edits only) fail backend construction. Persist structured failures rather than Chinese raw text visible to non-Chinese users."""
    stored = encode_task_failure_message(
        ComfyuiError("provider_unsupported_media", provider_id="custom-1", media_type="audio")
    )

    assert stored.startswith("[provider_unsupported_media]")
    for locale in ("zh", "en", "vi"):
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == MESSAGES[locale][
            "task_fail_provider_unsupported_media"
        ].format(**make_display_names(locale).apply({"provider_id": "custom-1", "media_type": "audio"}))


def test_encode_covers_every_capability_exception_type():
    """All three capability exceptions use structured encoding, never baked-in wording."""
    cases = [
        ImageCapabilityError("image_endpoint_mismatch_no_t2i", model="gpt-image-1"),
        VideoCapabilityError("video_start_image_unreadable", model="kling-v1", name="a.png"),
        ReferencePayloadFloorError(),
    ]
    for exc in cases:
        stored = encode_task_failure_message(exc)
        assert stored.startswith(f"[{exc.code}]")
        assert render_failure(stored, _translator("en"), make_display_names("en")) != stored


@pytest.mark.parametrize("code", sorted(REFERENCE_PROJECTION_FAILURE_CODES))
def test_projection_failure_codes_are_machine_encodable_in_all_locales(code: str):
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


@pytest.mark.parametrize(
    ("problem", "expected_params"),
    [
        (
            ProjectionProblem(
                code="reference_supported_durations_invalid",
                blocking=True,
                params=(("provider", "fake"), ("model", "bad-model")),
            ),
            {"provider": "fake", "model": "bad-model"},
        ),
        (
            ProjectionProblem(
                code="reference_asset_missing",
                blocking=True,
                params=(
                    ("missing", (("character", "张三"),)),
                    ("missing_text", "character: 张三"),
                ),
            ),
            {
                "missing": (("character", "张三"),),
                "missing_text": "character: 张三",
            },
        ),
    ],
)
def test_projection_failure_preserves_canonical_code_and_params_for_localized_tasks(
    problem: ProjectionProblem, expected_params: dict[str, object]
):
    stored = encode_task_failure_message(ReferenceProjectionBlockedError(problem))
    assert json.loads(stored.split("] ", 1)[1]) == json.loads(json.dumps(expected_params, ensure_ascii=False))
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale][problem.code].format(**make_display_names(locale).apply(expected_params))
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == expected


@pytest.mark.parametrize("code", sorted(VIDEO_REQUEST_FACTS_FAILURE_CODES))
def test_video_request_facts_failure_codes_are_machine_encodable_in_all_locales(code: str) -> None:
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


def test_video_request_facts_rejection_keeps_code_and_params_for_localized_tasks() -> None:
    params = {"provider": "gemini-aistudio", "model": "veo", "resolution": "1080p", "capability": "i2v"}
    failure = VideoRequestFactsFailure("video_supported_durations_incompatible", tuple(params.items()))

    stored = encode_task_failure_message(VideoRequestFactsError(failure))

    assert stored.startswith("[video_supported_durations_incompatible]")
    assert json.loads(stored.split("] ", 1)[1]) == params
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale]["video_supported_durations_incompatible"].format(
            **make_display_names(locale).apply(params)
        )
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == expected


#: Failure codes/retry flags removed when video stopped reading narration delivery; preserve historical task text verbatim.
_RETIRED_NARRATED_VIDEO_CODES = (
    "tts_duration_endpoint_fixed",
    "video_shorter_than_tts",
    "video_duration_unavailable",
    "video_request_cost_unavailable",
    "tts_conflicts_with_active_narrated_video",
    "tts_missing",
    "tts_generating",
    "tts_stale",
    "tts_state_unavailable",
    "tts_duration_unavailable",
    "tts_not_configured",
)


@pytest.mark.parametrize("code", _RETIRED_NARRATED_VIDEO_CODES)
def test_retired_narrated_video_codes_are_gone_and_historic_rows_pass_through(code: str) -> None:
    assert code not in FAILURE_CODE_KEYS
    for locale in ("zh", "en", "vi"):
        assert code not in MESSAGES[locale]
    stored = f'[{code}] {{"resource_id": "E1U01"}}'
    assert render_failure(stored, _translator("zh"), make_display_names("zh")) == stored


@pytest.mark.parametrize(
    ("code", "generation_type"),
    [
        ("video_capability_missing_i2v", "i2v"),
        ("video_capability_missing_r2v", "r2v"),
        ("video_capability_reference_unavailable", "r2v"),
    ],
)
def test_encode_video_bucket_capability_error_renders_per_locale(code: str, generation_type: VideoGenerationType):
    """VideoBucketCapabilityError resolution gates also encode structurally and render in three languages. Assert all three codes separately; testing one misses malformed placeholders/parameters in others. ``test_video_bucket_codes_registered_with_all_three_locales`` separately checks key coverage."""
    exc = VideoBucketCapabilityError(
        code=code,
        generation_type=generation_type,
        provider_id="minimax",
        model_id="MiniMax-Hailuo-2.3",
        message=f"lacks {generation_type}",
    )
    stored = encode_task_failure_message(exc)
    assert stored.startswith(f"[{code}]")
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale][code].format(
            **make_display_names(locale).apply({"provider": "minimax", "model": "MiniMax-Hailuo-2.3"})
        )
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == expected


@pytest.mark.parametrize("generation_type", ["t2i", "i2i"])
def test_encode_image_bucket_capability_error_renders_per_locale(generation_type: str):
    """ImageBucketCapabilityError resolution gates share execution ImageCapabilityError codes and structured encoding."""
    exc = ImageBucketCapabilityError(generation_type=generation_type, provider_id="custom-3", model_id="relay-img")
    stored = encode_task_failure_message(exc)
    code = f"image_capability_missing_{generation_type}"
    assert stored.startswith(f"[{code}]")
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale][code].format(
            **make_display_names(locale).apply({"provider": "custom-3", "model": "relay-img"})
        )
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == expected


@pytest.mark.parametrize(
    "code",
    [
        "video_capability_missing_i2v",
        "video_capability_missing_r2v",
        "video_capability_reference_unavailable",
    ],
)
def test_video_bucket_codes_registered_with_all_three_locales(code):
    """Resolution-gate codes map identically to errors keys, with all three locale templates."""
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


def test_encode_stringifies_non_json_params():
    """Convert non-JSON-native params to strings without raising during failure encoding. ``video_duration_invalid`` echoes rejected caller values of uncontrolled types."""
    stored = encode_task_failure_message(VideoCapabilityError("video_duration_invalid", duration=Path("weird")))
    assert json.loads(stored.split("] ", 1)[1]) == {"duration": "weird"}


def test_bound_reason_preserves_scalar_param_types():
    """Truncation must preserve types of other parameters in the same failure.

    Truncation normalizes the complete params object once any value is oversized. Stringifying native scalars reencodes with quotes (``42`` → ``"42"``, ``True`` → ``"true"``, ``None`` → ``"null"``), changing persisted envelope types. Typed placeholders such as ``{n:d}`` then make ``str.format`` raise; ``lib/i18n/__init__.py::_`` swallows it, exposing raw placeholders."""
    stored = encode_failure("resume_expired_detail", detail="x" * 3000, retries=3, ratio=1.5, expired=True, note=None)

    bounded = bound_reason(stored, 2000)

    assert len(bounded) <= 2000
    params = json.loads(bounded.split("] ", 1)[1])
    assert params["retries"] == 3
    assert isinstance(params["retries"], int)
    assert params["ratio"] == 1.5
    assert isinstance(params["ratio"], float)
    assert params["expired"] is True
    assert params["note"] is None
    # Still narrow the oversized parameter.
    assert len(params["detail"]) < 3000


def test_unregistered_capability_code_degrades_to_passthrough_text():
    """Unregistered codes fall back to unstructured text forwarded unchanged; retain reasons without leaving tasks running."""
    exc = VideoCapabilityError("video_totally_new_code", model="x")
    stored = encode_task_failure_message(exc)
    assert stored == "video_totally_new_code"
    assert render_failure(stored, _translator("zh"), make_display_names("zh")) == stored


def test_non_capability_exception_still_passes_through():
    stored = encode_task_failure_message(RuntimeError("provider socket closed"))
    assert stored == "provider socket closed"
    assert render_failure(stored, _translator("en"), make_display_names("en")) == stored


def test_provider_rejection_stores_status_and_reason_as_separate_params():
    """Deterministic upstream 4xx puts status codes into translations while retaining original reason summaries in separate envelope parameters."""
    request = httpx.Request("POST", "https://x/v2?api_key=SECRETKEY")
    exc = ProviderRejectedError(
        "400 response for https://x/v2",
        request=request,
        response=httpx.Response(400, request=request),
        provider_reason="InvalidParameter: prompt violates the content policy",
    )

    stored = encode_task_failure_message(exc)

    assert json.loads(stored.split("] ", 1)[1]) == {
        "provider_reason": "InvalidParameter: prompt violates the content policy",
        "status": 400,
    }
    assert "SECRETKEY" not in stored
    for locale in ("zh", "en", "vi"):
        rendered = render_failure(stored, _translator(locale), make_display_names(locale))
        assert rendered == MESSAGES[locale]["task_fail_provider_rejected"].format(status=400)
        # Upstream original summaries stay outside translations and display separately on reads.
        assert "InvalidParameter" not in rendered


def test_provider_rejection_without_a_reason_still_stores_the_status():
    """Absent summaries (authentication reasons are not forwarded, or empty bodies) still use structured envelopes and localized reads."""
    request = httpx.Request("POST", "https://x/v2?api_key=SECRETKEY")
    exc = ProviderRejectedError(
        "401 response for https://x/v2",
        request=request,
        response=httpx.Response(401, request=request),
    )

    stored = encode_task_failure_message(exc)

    assert json.loads(stored.split("] ", 1)[1]) == {"status": 401}
    assert "SECRETKEY" not in stored
    for locale in ("zh", "en", "vi"):
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == MESSAGES[locale][
            "task_fail_provider_rejected"
        ].format(status=401)


@pytest.mark.parametrize(
    "legacy",
    [
        None,
        "",
        "<AioRpcError of RPC that terminated with:\n\tstatus = StatusCode.OUT_OF_RANGE>",
        "不支持的资源类型: reference_videos",
        "[video_duration_not_supported] not-json",
        "[some_retired_code] {}",
    ],
)
def test_legacy_rows_render_without_error(legacy):
    """Legacy empty/plain/invalid pseudo-structured rows never break reads and return unchanged."""
    assert render_failure(legacy, _translator("en"), make_display_names("en")) == legacy


def _encode_cascade(dependency_task_id: str, reason: str) -> str:
    return encode_failure("cascade_blocked_dependency", dependency_task_id=dependency_task_id, reason=reason)


def test_cascade_reason_renders_upstream_reason_per_locale():
    """Upstream-dependent tasks never degrade to raw [code]; localize cascade wrappers and their inner causes in read language."""
    upstream = encode_task_failure_message(
        VideoCapabilityError("video_duration_not_supported", duration=7, supported="5, 10")
    )
    stored = _encode_cascade("5a38f38e", upstream)

    rendered = {
        locale: render_failure(stored, _translator(locale), make_display_names(locale)) for locale in ("zh", "en", "vi")
    }
    assert len({*rendered.values()}) == 3, rendered
    for locale, text in rendered.items():
        assert "video_duration_not_supported" not in text
        assert "5a38f38e" in text
        inner = MESSAGES[locale]["video_duration_not_supported"].format(duration=7, supported="5, 10")
        assert inner in text


def test_deep_cascade_chain_keeps_root_cause_renderable():
    """Every layer of long dependency chains renders locally rather than embedding truncated inner envelopes.

    Repeatedly reencoding complete prior envelopes grows near-exponentially; escaping doubles length until roughly seven layers exceed persistence budgets. Tail clipping cuts inner JSON: outer JSON parses but reads expose broken inner content as plain text. Folding intermediate layers before encoding makes length independent of chain depth."""
    upstream = encode_task_failure_message(
        VideoCapabilityError("video_duration_not_supported", duration=7, supported="5, 10")
    )
    inner = MESSAGES["zh"]["video_duration_not_supported"].format(duration=7, supported="5, 10")

    stored = upstream
    lengths = set()
    for depth in range(1, 21):
        stored = _encode_bounded_cascade_failure(dependency_task_id=f"{depth:032d}", reason=stored)
        lengths.add(len(stored))
        rendered = render_failure(stored, _translator("zh"), make_display_names("zh"))
        assert rendered is not None
        # Both immediate dependency and root cause remain, without leaked machine-code fragments.
        assert f"{depth:032d}" in rendered
        assert inner in rendered
        assert "[cascade_blocked_dependency]" not in rendered
        assert "video_duration_not_supported" not in rendered

    assert len(lengths) == 1, f"折叠后串长应与链深无关，实际出现多种长度: {sorted(lengths)}"


def test_collapse_cascade_reason_leaves_non_cascade_reason_untouched():
    """Never alter noncascade reasons, including original provider text, through folding."""
    upstream = encode_task_failure_message(VideoCapabilityError("video_duration_invalid", duration="x"))
    assert collapse_cascade_reason(upstream) == upstream
    assert collapse_cascade_reason("provider rejected") == "provider rejected"


def test_cascade_reason_with_unstructured_upstream_still_renders():
    """When upstream causes are raw provider exceptions, localize wrappers but forward inner text unchanged."""
    stored = _encode_cascade("5a38f38e", "provider rejected")
    rendered = render_failure(stored, _translator("zh"), make_display_names("zh"))
    assert rendered != stored
    assert "provider rejected" in (rendered or "")


def test_render_degrades_when_json_hits_recursion_limit(monkeypatch):
    """Short deeply nested params can exceed ``json.loads`` recursion limits during HTTP rendering. Treat like malformed JSON and forward unchanged, preserving reasons rather than returning 500."""
    stored = '[video_reference_images_unreadable] {"names": [[[1]]]}'

    def _boom(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(task_failure.json, "loads", _boom)

    assert render_failure(stored, _translator("en"), make_display_names("en")) == stored


def test_bound_reason_shrinks_oversized_non_string_params():
    """Oversized nonstring params must still leave parseable envelopes.

    ``video_duration_invalid`` echoes original rejected configuration; imports/manual project.json edits may make huge arrays. Native JSON types bypass ``default=str``; without string params to narrow, old raw slicing cut JSON mid-envelope and exposed machine-code strings on read failure."""
    stored = encode_task_failure_message(VideoCapabilityError("video_duration_invalid", duration=list(range(2000))))
    assert len(stored) > 2000

    bounded = bound_reason(stored, 2000)

    assert len(bounded) <= 2000
    # Still a valid envelope: read paths parse and localize rather than exposing raw machine codes.
    rendered = render_failure(bounded, _translator("en"), make_display_names("en"))
    assert rendered is not None
    assert rendered != bounded
    assert "video_duration_invalid" not in rendered


def test_bound_reason_shrinks_escape_heavy_params():
    """Escaping-doubled parameters still narrow into valid envelopes rather than falsely falling back to raw slices.

    Excess uses encoded characters, but ``"`` / ``\\`` occupy two after JSON escaping. Comparing excess directly with raw lengths misclassifies parameters whose raw length is below excess but which would fit after narrowing."""
    names = "\\" * 900
    stored = encode_failure("image_reference_images_unreadable", names=names)
    # Escaping roughly doubles encoded length, the premise of the false classification.
    assert len(stored) > 2 * len(names)

    bounded = bound_reason(stored, 900)

    assert len(bounded) <= 900
    rendered = render_failure(bounded, _translator("en"), make_display_names("en"))
    assert rendered is not None
    assert "image_reference_images_unreadable" not in rendered
    # When budget fits substantial parameter content, do not empty it: diagnostics are this reason's value.
    assert len(json.loads(bounded.split(" ", 1)[1])["names"]) > 100


def _oversized_envelope() -> str:
    """An envelope exceeding 2000 characters but still valid ``[code] {params}``.

    Place excess inside JSON: trailing text after ``}`` fails ``_STRUCTURED_RE`` and returns before ``json``, never testing this degradation path."""
    stored = encode_failure("video_duration_invalid", duration="x" * 3000)
    assert len(stored) > 2000
    return stored


def test_as_shrinkable_degrades_when_param_json_hits_recursion_limit(monkeypatch):
    """If deep nesting prevents measuring parameters, degrade to ellipses without raising in persistence.

    Assert ``_as_shrinkable`` alone: afterward ``params`` are flat strings, so ``encode_failure`` ``dumps`` cannot hit recursion. Forcing ``bound_reason`` would require global mocks to create unreachable scenarios."""

    def _boom(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(task_failure.json, "dumps", _boom)

    assert task_failure._as_shrinkable([[[1]]]) == "…"


def test_bound_reason_degrades_when_params_json_load_hits_recursion_limit(monkeypatch):
    """If params exceed ``json.loads`` recursion limits, fall back to character truncation without raising during persistence."""
    stored = _oversized_envelope()

    def _boom(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(task_failure.json, "loads", _boom)

    assert bound_reason(stored, 2000) == stored[:2000]
