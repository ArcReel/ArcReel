"""Verify that i18n translation dictionaries are consistent across locales."""

from lib.i18n import MESSAGES, SUPPORTED_LOCALES
from lib.i18n.en import emails as en_emails
from lib.i18n.en import errors as en_errors
from lib.i18n.en import system as en_system
from lib.i18n.zh import emails as zh_emails
from lib.i18n.zh import errors as zh_errors
from lib.i18n.zh import system as zh_system


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


def test_supported_locales_all_present():
    """SUPPORTED_LOCALES must match the locales in MESSAGES."""
    assert set(SUPPORTED_LOCALES) == set(MESSAGES.keys())


def test_format_placeholders_consistent():
    """Both locales must use the same format placeholders for each key."""
    import re

    placeholder_re = re.compile(r"\{(\w+)\}")
    base_locale = SUPPORTED_LOCALES[0]

    for key in MESSAGES[base_locale]:
        base_placeholders = set(placeholder_re.findall(MESSAGES[base_locale][key]))
        for locale in SUPPORTED_LOCALES[1:]:
            if key not in MESSAGES[locale]:
                continue
            locale_placeholders = set(placeholder_re.findall(MESSAGES[locale][key]))
            assert base_placeholders == locale_placeholders, (
                f"Key '{key}': {base_locale} uses {base_placeholders} but {locale} uses {locale_placeholders}"
            )
