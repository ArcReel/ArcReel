"""启动断言测试 — 父进程 env 不得含 provider 密钥。"""

from __future__ import annotations

import platform

import pytest

from server.app import (
    assert_no_provider_secrets_in_environ,
    check_sandbox_available,
    detect_docker_environment,
)


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


def test_sandbox_available_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None)
    assert check_sandbox_available() is True


def test_sandbox_missing_macos_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="SANDBOX_UNAVAILABLE"):
        check_sandbox_available()


def test_sandbox_available_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    assert check_sandbox_available() is True


def test_sandbox_missing_linux_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="bubblewrap"):
        check_sandbox_available()


def test_sandbox_windows_warns_not_raises(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Windows 上 SDK 不支持 sandbox：返回 False + warning，不 raise。"""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    with caplog.at_level("WARNING", logger="server.app"):
        result = check_sandbox_available()
    assert result is False
    assert any("SANDBOX_UNSUPPORTED" in record.message for record in caplog.records)


def test_detect_docker_via_dockerenv(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake_dockerenv = tmp_path / ".dockerenv"
    fake_dockerenv.touch()
    monkeypatch.setattr("server.app._DOCKERENV_PATH", fake_dockerenv)
    monkeypatch.setattr("server.app._CGROUP_PATH", tmp_path / "nonexistent")
    assert detect_docker_environment() is True


def test_detect_docker_via_cgroup(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake_cgroup = tmp_path / "cgroup"
    fake_cgroup.write_text("12:cpu:/docker/abc123\n")
    monkeypatch.setattr("server.app._DOCKERENV_PATH", tmp_path / "nope")
    monkeypatch.setattr("server.app._CGROUP_PATH", fake_cgroup)
    assert detect_docker_environment() is True


def test_detect_no_docker(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("server.app._DOCKERENV_PATH", tmp_path / "nope")
    monkeypatch.setattr("server.app._CGROUP_PATH", tmp_path / "also_nope")
    assert detect_docker_environment() is False
