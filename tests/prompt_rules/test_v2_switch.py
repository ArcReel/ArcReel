import pytest

from lib.prompt_rules import is_v2_enabled


def test_default_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARCREEL_PROMPT_RULES_V2", raising=False)
    assert is_v2_enabled() is True


def test_explicit_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    assert is_v2_enabled() is False


def test_off_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "OFF")
    assert is_v2_enabled() is False


def test_other_value_treated_as_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "true")
    assert is_v2_enabled() is True


def test_off_with_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "  off  ")
    assert is_v2_enabled() is False
