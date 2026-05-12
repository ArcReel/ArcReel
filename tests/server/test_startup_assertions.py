"""启动断言测试 — 父进程 env 不得含 provider 密钥。"""

from __future__ import annotations

import pytest

from server.app import assert_no_provider_secrets_in_environ


def _clear_secret_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "ANTHROPIC_API_KEY",
        "ARK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "VIDU_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(k, raising=False)


def test_clean_environ_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_secret_envs(monkeypatch)
    assert_no_provider_secrets_in_environ()  # no raise


@pytest.mark.parametrize(
    "leaked_key",
    [
        "ANTHROPIC_API_KEY",
        "ARK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "VIDU_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ],
)
def test_any_single_secret_triggers_raise(monkeypatch: pytest.MonkeyPatch, leaked_key: str) -> None:
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv(leaked_key, "leaked-value")
    with pytest.raises(RuntimeError, match="SECURITY"):
        assert_no_provider_secrets_in_environ()


def test_empty_string_value_not_treated_as_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """空字符串不算泄漏（os.environ.pop 后 SDK 子进程会跳过空值）。"""
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert_no_provider_secrets_in_environ()  # 空值不 raise
