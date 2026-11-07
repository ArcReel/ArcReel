"""Verify that i18n translation dictionaries are consistent across locales."""

import re
from pathlib import Path
from typing import get_args

from lib.config.registry import PROVIDER_REGISTRY
from lib.i18n import MESSAGES, SUPPORTED_LOCALES
from lib.i18n.en import emails as en_emails
from lib.i18n.en import errors as en_errors
from lib.i18n.en import events as en_events
from lib.i18n.en import system as en_system
from lib.i18n.en import templates as en_templates
from lib.i18n.vi import emails as vi_emails
from lib.i18n.vi import errors as vi_errors
from lib.i18n.vi import events as vi_events
from lib.i18n.vi import system as vi_system
from lib.i18n.vi import templates as vi_templates
from lib.i18n.zh import emails as zh_emails
from lib.i18n.zh import errors as zh_errors
from lib.i18n.zh import events as zh_events
from lib.i18n.zh import system as zh_system
from lib.i18n.zh import templates as zh_templates
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.prompts.prompt_templates.engine import TemplateAxis
from lib.prompts.style_templates import list_template_ids


def test_all_locales_have_same_keys():
    """Every locale must define the exact same set of merged keys."""
    key_sets = {locale: set(msgs.keys()) for locale, msgs in MESSAGES.items()}
    locales = list(key_sets.keys())
    for i in range(1, len(locales)):
        missing = key_sets[locales[0]] - key_sets[locales[i]]
        extra = key_sets[locales[i]] - key_sets[locales[0]]
        assert not missing, f"{locales[i]} is missing keys present in {locales[0]}: {missing}"
        assert not extra, f"{locales[i]} has extra keys not in {locales[0]}: {extra}"


def test_errors_module_keys_match():
    """en/errors.py and zh/errors.py must have identical key sets."""
    en_keys = set(en_errors.MESSAGES.keys())
    zh_keys = set(zh_errors.MESSAGES.keys())
    assert en_keys == zh_keys, (
        f"en-zh errors key mismatch: missing_in_zh={en_keys - zh_keys}, missing_in_en={zh_keys - en_keys}"
    )


def test_system_module_keys_match():
    en_keys = set(en_system.MESSAGES.keys())
    zh_keys = set(zh_system.MESSAGES.keys())
    assert en_keys == zh_keys, (
        f"en-zh system key mismatch: missing_in_zh={en_keys - zh_keys}, missing_in_en={zh_keys - en_keys}"
    )


def test_emails_module_keys_match():
    en_keys = set(en_emails.MESSAGES.keys())
    zh_keys = set(zh_emails.MESSAGES.keys())
    assert en_keys == zh_keys, (
        f"en-zh emails key mismatch: missing_in_zh={en_keys - zh_keys}, missing_in_en={zh_keys - en_keys}"
    )


def test_templates_module_keys_match():
    en_keys = set(en_templates.MESSAGES.keys())
    zh_keys = set(zh_templates.MESSAGES.keys())
    assert en_keys == zh_keys, (
        f"en-zh templates key mismatch: missing_in_zh={en_keys - zh_keys}, missing_in_en={zh_keys - en_keys}"
    )


def test_templates_cover_all_style_template_ids():
    """Every style-template ID needs name/tagline keys in zh/en/vi templates."""
    required_name_keys = {f"template_name_{tid}" for tid in list_template_ids()}
    required_tagline_keys = {f"template_tagline_{tid}" for tid in list_template_ids()}
    for module_name, msgs in (
        ("zh", zh_templates.MESSAGES),
        ("en", en_templates.MESSAGES),
        ("vi", vi_templates.MESSAGES),
    ):
        missing_names = required_name_keys - set(msgs.keys())
        missing_taglines = required_tagline_keys - set(msgs.keys())
        assert not missing_names, f"{module_name} templates missing name keys: {missing_names}"
        assert not missing_taglines, f"{module_name} templates missing tagline keys: {missing_taglines}"


def test_vi_errors_module_keys_match():
    """vi/errors.py and en/errors.py must have identical key sets."""
    en_keys = set(en_errors.MESSAGES.keys())
    vi_keys = set(vi_errors.MESSAGES.keys())
    assert en_keys == vi_keys, (
        f"en-vi errors key mismatch: missing_in_vi={en_keys - vi_keys}, missing_in_en={vi_keys - en_keys}"
    )


def test_vi_system_module_keys_match():
    en_keys = set(en_system.MESSAGES.keys())
    vi_keys = set(vi_system.MESSAGES.keys())
    assert en_keys == vi_keys, (
        f"en-vi system key mismatch: missing_in_vi={en_keys - vi_keys}, missing_in_en={vi_keys - en_keys}"
    )


def test_vi_emails_module_keys_match():
    en_keys = set(en_emails.MESSAGES.keys())
    vi_keys = set(vi_emails.MESSAGES.keys())
    assert en_keys == vi_keys, (
        f"en-vi emails key mismatch: missing_in_vi={en_keys - vi_keys}, missing_in_en={vi_keys - en_keys}"
    )


def test_vi_templates_module_keys_match():
    en_keys = set(en_templates.MESSAGES.keys())
    vi_keys = set(vi_templates.MESSAGES.keys())
    assert en_keys == vi_keys, (
        f"en-vi templates key mismatch: missing_in_vi={en_keys - vi_keys}, missing_in_en={vi_keys - en_keys}"
    )


def test_supported_locales_all_present():
    """SUPPORTED_LOCALES must match the locales in MESSAGES."""
    assert set(SUPPORTED_LOCALES) == set(MESSAGES.keys())


def test_format_placeholders_consistent():
    """Both locales must use the same format placeholders for each key."""
    import string

    def placeholders(msg: str) -> set[tuple[str, str]]:
        # Use `str.format` parsing: escaped `{{…}}` braces (e.g. `@[角色]：{台词}` syntax)
        # are literal text, not placeholders; formatted `{delta:.0%}` still yields the correct field name.
        # Compare format_spec too: missing `.0%` / `.1f` would render raw numeric values in that language.
        return {(name, spec or "") for _, name, spec, _ in string.Formatter().parse(msg) if name}

    base_locale = SUPPORTED_LOCALES[0]

    for key in MESSAGES[base_locale]:
        base_placeholders = placeholders(MESSAGES[base_locale][key])
        for locale in SUPPORTED_LOCALES[1:]:
            if key not in MESSAGES[locale]:
                continue
            locale_placeholders = placeholders(MESSAGES[locale][key])
            assert base_placeholders == locale_placeholders, (
                f"Key '{key}': {base_locale} uses {base_placeholders} but {locale} uses {locale_placeholders}"
            )


def test_batch_admission_problem_codes_are_translated():
    """Every problem code a batch admission can surface must read as prose.

    The admission envelope localizes each problem by looking its code up as a
    message key, and an unresolved key falls back to the key itself — so a
    missing entry reaches the user as a bare identifier instead of a reason.
    Execution-time codes are excluded: they are reported from the persisted task
    failure envelope, not from admission.
    """

    from lib.generation.generation_result import GenerationProblemCode
    from lib.script.reference_video.request_projection import _PROBLEM_PRESENTATION
    from lib.speech.speech_composition import SpeechProblemCode

    execution_only = {
        GenerationProblemCode.ENQUEUE_FAILED,
        GenerationProblemCode.TASK_FAILED,
        GenerationProblemCode.TASK_CANCELLED,
        GenerationProblemCode.TASK_INTERRUPTED,
        GenerationProblemCode.POST_PROCESSING_FAILED,
        GenerationProblemCode.DEPENDENCY_FAILED,
    }
    codes = (
        set(_PROBLEM_PRESENTATION)
        | {code.value for code in SpeechProblemCode}
        | {code.value for code in GenerationProblemCode if code not in execution_only}
    )
    for code in sorted(codes):
        for locale in SUPPORTED_LOCALES:
            assert code in MESSAGES[locale], f"problem code '{code}' has no {locale} message"


def test_market_core_message_keys_are_translated():
    """Every message key arcreel-market-core can emit must read as prose.

    The subpackage ships no translation catalog: its diagnostics carry only a key
    and params, and the application renders them. A key missing here reaches the
    user as a bare identifier.
    """
    from arcreel_market_core.message_keys import MESSAGE_KEYS

    for key in sorted(MESSAGE_KEYS):
        for locale in SUPPORTED_LOCALES:
            assert key in MESSAGES[locale], f"market core message key '{key}' has no {locale} message"


def _event_label_keys(messages: dict[str, str]) -> set[str]:
    prefix = "event_label_"
    return {key.removeprefix(prefix) for key in messages if key.startswith(prefix)}


def test_events_module_keys_match():
    en_keys = set(en_events.MESSAGES.keys())
    zh_keys = set(zh_events.MESSAGES.keys())
    vi_keys = set(vi_events.MESSAGES.keys())
    assert en_keys == zh_keys == vi_keys, (
        f"events key mismatch: missing_in_zh={en_keys - zh_keys}, "
        f"missing_in_vi={en_keys - vi_keys}, missing_in_en={(zh_keys | vi_keys) - en_keys}"
    )


def test_every_event_label_key_is_translated():
    """Translate all potential event label_key values, without unused residual keys."""
    from lib.project.asset_types import ASSET_SPECS
    from lib.script.script_skeleton import SKELETON_ITEM_LABEL_KEYS
    from server.services.tasks.generation_tasks import _SKELETON_TASK_LABEL_KEYS, _TASK_CHANGE_SPECS

    emitted = {spec[2] for spec in _TASK_CHANGE_SPECS.values()}
    emitted |= set(_SKELETON_TASK_LABEL_KEYS.values())
    emitted |= set(SKELETON_ITEM_LABEL_KEYS.values())
    # Snapshot-diff asset/derivative keys derive from the asset-type table.
    emitted |= {f"named_entity_{asset_type}" for asset_type in ASSET_SPECS}
    emitted |= {f"named_entity_{t}_derivative" for t, spec in ASSET_SPECS.items() if spec.supports_derivatives}
    # Fixed keys published directly by snapshots/routes, explicitly registered because no table enumerates them.
    emitted |= {
        "character_reference_audio",
        "project_settings",
        "overview",
        "episode",
        "draft_normalized_script",
        "draft_segment_splitting",
        "draft_script_plan",
        "draft_prompt_authoring",
    }
    assert _event_label_keys(zh_events.MESSAGES) == emitted


def test_frontend_event_label_keys_match_backend():
    """Both UI sides render the same label_key set; key inventories must not drift."""
    source = (Path(__file__).resolve().parents[4] / "frontend" / "src" / "i18n" / "en" / "events.ts").read_text(
        encoding="utf-8"
    )
    frontend_keys = set(re.findall(r"""["']label\.([a-z0-9_]+)["']""", source))
    assert frontend_keys == _event_label_keys(en_events.MESSAGES)


def test_frontend_dashboard_covers_prompt_template_axes_and_categories():
    """Settings template axes/category IDs need all three locales; missing keys expose raw English identifiers."""
    axes = set(get_args(TemplateAxis))
    categories = {meta.category for meta in builtin_templates.list_templates()}
    required = {f"prompt_templates_axis_{axis}" for axis in axes} | {
        f"prompt_templates_category_{category}" for category in categories
    }
    i18n_dir = Path(__file__).resolve().parents[4] / "frontend" / "src" / "i18n"
    for locale in ("zh", "en", "vi"):
        source = (i18n_dir / locale / "dashboard.ts").read_text(encoding="utf-8")
        defined = set(re.findall(r"""['"]([a-z0-9_]+)['"]\s*:""", source))
        missing = required - defined
        assert not missing, f"{locale} dashboard missing prompt template keys: {sorted(missing)}"


#: Writing-system ranges requiring translated catalog names; non-Latin names are unreadable directly in en/vi interfaces.
_NON_LATIN_RANGES = (
    ("\u0400", "\u04ff"),  # Cyrillic.
    ("\u3040", "\u30ff"),  # Hiragana / Katakana.
    ("\u3400", "\u4dbf"),  # CJK Extension A.
    ("\u4e00", "\u9fff"),  # CJK Unified Ideographs.
    ("\uac00", "\ud7af"),  # Hangul Syllables.
    ("\uf900", "\ufaff"),  # CJK Compatibility Ideographs.
)


def test_non_latin_provider_display_names_are_translated():
    """Non-Latin registry provider names require three-language translations, preventing source text in en/vi catalogs.

    Pure brands ("OpenAI") stay outside translation tables: ``translate_or`` falls back to registry
    display_name, and three identical translations would merely duplicate it.
    """

    def is_non_latin(text: str) -> bool:
        return any(low <= ch <= high for ch in text for low, high in _NON_LATIN_RANGES)

    required = {
        f"provider_name_{provider_id}"
        for provider_id, meta in PROVIDER_REGISTRY.items()
        if is_non_latin(meta.display_name)
    }
    for locale in SUPPORTED_LOCALES:
        missing = required - set(MESSAGES[locale])
        assert not missing, f"{locale} 缺少目录译名: {sorted(missing)}"


def test_model_display_names_have_no_translation_keys():
    """Model display names come only from catalog entries, independent of locale; translation tables exclude model names."""
    for locale in SUPPORTED_LOCALES:
        model_keys = sorted(key for key in MESSAGES[locale] if key.startswith("model_name_"))
        assert not model_keys, f"{locale} 仍有模型译名: {model_keys}"
