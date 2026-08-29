"""Verify that i18n translation dictionaries are consistent across locales."""

import re
from pathlib import Path

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
from lib.style_templates import STYLE_TEMPLATES


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
    """STYLE_TEMPLATES 的每个 id 都必须在 zh/en/vi templates 里有 name 与 tagline key。"""
    required_name_keys = {f"template_name_{tid}" for tid in STYLE_TEMPLATES}
    required_tagline_keys = {f"template_tagline_{tid}" for tid in STYLE_TEMPLATES}
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
        # 用 `str.format` 自己的解析器：转义花括号 `{{…}}`（如语法示例 `@[角色]：{台词}`）
        # 被识别为字面文本而非占位符，带格式说明的 `{delta:.0%}` 也能正确取到字段名。
        # 连同 format_spec 一起比较：某语言漏写 `.0%` / `.1f` 会让该语言渲染出原始数值。
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

    from lib.generation_result import GenerationProblemCode
    from lib.reference_video.request_projection import _PROBLEM_PRESENTATION
    from lib.speech_composition import SpeechProblemCode

    execution_only = {
        GenerationProblemCode.ENQUEUE_FAILED,
        GenerationProblemCode.TASK_FAILED,
        GenerationProblemCode.TASK_CANCELLED,
        GenerationProblemCode.TASK_INTERRUPTED,
        GenerationProblemCode.POST_PROCESSING_FAILED,
    }
    codes = (
        set(_PROBLEM_PRESENTATION)
        | {code.value for code in SpeechProblemCode}
        | {code.value for code in GenerationProblemCode if code not in execution_only}
    )
    for code in sorted(codes):
        for locale in SUPPORTED_LOCALES:
            assert code in MESSAGES[locale], f"problem code '{code}' has no {locale} message"


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
    """事件载荷可能携带的 label_key 全部有翻译，且没有无人使用的残留 key。"""
    from lib.script_skeleton import SKELETON_ITEM_LABEL_KEYS
    from server.services.generation_tasks import _SKELETON_TASK_LABEL_KEYS, _TASK_CHANGE_SPECS

    emitted = {spec[2] for spec in _TASK_CHANGE_SPECS.values()}
    emitted |= set(_SKELETON_TASK_LABEL_KEYS.values())
    emitted |= set(SKELETON_ITEM_LABEL_KEYS.values())
    # 快照差分与路由直接发布的固定 key（无表可枚举，在此登记）。
    emitted |= {
        "named_entity_character",
        "named_entity_scene",
        "named_entity_prop",
        "character_reference_audio",
        "project_settings",
        "overview",
        "episode",
        "draft_normalized_script",
        "draft_segment_splitting",
    }
    assert _event_label_keys(zh_events.MESSAGES) == emitted


def test_frontend_event_label_keys_match_backend():
    """界面按同一组 label_key 渲染文案，两侧 key 集合不得漂移。"""
    source = (Path(__file__).resolve().parents[4] / "frontend" / "src" / "i18n" / "en" / "events.ts").read_text(
        encoding="utf-8"
    )
    frontend_keys = set(re.findall(r"""["']label\.([a-z0-9_]+)["']""", source))
    assert frontend_keys == _event_label_keys(en_events.MESSAGES)


#: 目录名里出现即需要译名的书写系统区段：拉丁字母以外的写法在 en/vi 界面上无法直接阅读。
_NON_LATIN_RANGES = (
    ("\u0400", "\u04ff"),  # 西里尔字母
    ("\u3040", "\u30ff"),  # 平假名 / 片假名
    ("\u3400", "\u4dbf"),  # CJK 扩展 A
    ("\u4e00", "\u9fff"),  # CJK 统一表意文字
    ("\uac00", "\ud7af"),  # 谚文音节
    ("\uf900", "\ufaff"),  # CJK 兼容表意文字
)


def test_non_latin_registry_display_names_are_translated():
    """registry 里用非拉丁文字写的名字必须三语都有译名，否则 en/vi 目录会漏出原文。

    纯品牌名（"Gemini 3 Pro"）不入译名表：``translate_or`` 回退到 registry 的
    display_name，三份相同的条目只是重复。
    """

    def is_non_latin(text: str) -> bool:
        return any(low <= ch <= high for ch in text for low, high in _NON_LATIN_RANGES)

    required: set[str] = set()
    for provider_id, meta in PROVIDER_REGISTRY.items():
        if is_non_latin(meta.display_name):
            required.add(f"provider_name_{provider_id}")
        for model_id, model in meta.models.items():
            if is_non_latin(model.display_name):
                required.add(f"model_name_{provider_id}_{model_id}")

    for locale in SUPPORTED_LOCALES:
        missing = required - set(MESSAGES[locale])
        assert not missing, f"{locale} 缺少目录译名: {sorted(missing)}"


def test_no_orphan_model_name_keys():
    """译名表里不留 registry 已删除的模型条目。"""
    known = {
        f"model_name_{provider_id}_{model_id}"
        for provider_id, meta in PROVIDER_REGISTRY.items()
        for model_id in meta.models
    }
    for locale in SUPPORTED_LOCALES:
        orphans = {key for key in MESSAGES[locale] if key.startswith("model_name_")} - known
        assert not orphans, f"{locale} 有 registry 里不存在的模型译名: {sorted(orphans)}"
