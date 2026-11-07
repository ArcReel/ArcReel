"""lib.infra.env_init .env override rules and inconsistency warnings."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from lib.infra import env_init


@pytest.fixture(autouse=True)
def _dotenv_enabled(monkeypatch: pytest.MonkeyPatch):
    # Root conftest disables .env reading process-wide; this tests those exact read rules.
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)


@pytest.fixture
def env_file(tmp_path: Path):
    # load_env_file stores read results for startup warnings; clear after tests to avoid later lifespan warnings.
    yield tmp_path / ".env"
    env_init.reset_env_file_load_for_tests()


def test_empty_process_env_is_filled_from_env_file(env_file: Path, monkeypatch: pytest.MonkeyPatch):
    """compose env_file injects template `AUTH_PASSWORD=` as empty; later .env-written values must apply."""
    env_file.write_text("AUTH_PASSWORD=my-new-pass\n", encoding="utf-8")
    monkeypatch.setenv("AUTH_PASSWORD", "")

    env_init.load_env_file(env_file)

    assert os.environ["AUTH_PASSWORD"] == "my-new-pass"


def test_explicit_non_empty_env_wins_over_env_file(env_file: Path, monkeypatch: pytest.MonkeyPatch):
    env_file.write_text("AUTH_PASSWORD=from-file\n", encoding="utf-8")
    monkeypatch.setenv("AUTH_PASSWORD", "from-env")

    env_init.load_env_file(env_file)

    assert os.environ["AUTH_PASSWORD"] == "from-env"


def test_interpolation_preserves_explicit_environment_auth_setting(env_file: Path, monkeypatch: pytest.MonkeyPatch):
    from server.auth import is_auth_enabled

    env_file.write_text("ARCREEL_TEST_FLAG=false\nAUTH_ENABLED=${ARCREEL_TEST_FLAG}\n", encoding="utf-8")
    monkeypatch.setenv("ARCREEL_TEST_FLAG", "true")
    monkeypatch.delenv("AUTH_ENABLED", raising=False)

    env_init.load_env_file(env_file)

    assert is_auth_enabled()


@pytest.mark.parametrize("value", ["true", "false"])
def test_empty_auth_enabled_uses_env_file(env_file: Path, monkeypatch: pytest.MonkeyPatch, value: str):
    from server.auth import is_auth_enabled

    env_file.write_text(f"AUTH_ENABLED={value}\n", encoding="utf-8")
    monkeypatch.setenv("AUTH_ENABLED", "")

    assert env_init.load_env_file(env_file) == ()
    assert is_auth_enabled() is (value == "true")


def test_disabled_dotenv_does_not_load_auth_configuration(env_file: Path, monkeypatch: pytest.MonkeyPatch):
    env_file.write_text("AUTH_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("AUTH_ENABLED", raising=False)

    assert env_init.load_env_file(env_file) == ()
    assert "AUTH_ENABLED" not in os.environ


def test_cleared_file_value_warns_while_explicit_environment_still_wins(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
):
    env_file.write_text("AUTH_ENABLED=\n", encoding="utf-8")
    monkeypatch.setenv("AUTH_ENABLED", "false")

    assert env_init.load_env_file(env_file) == ("AUTH_ENABLED",)
    assert os.environ["AUTH_ENABLED"] == "false"


def test_unset_keys_are_loaded_including_empty_values(env_file: Path, monkeypatch: pytest.MonkeyPatch):
    env_file.write_text("ARCREEL_TEST_FILLED=v\nARCREEL_TEST_BLANK=\n", encoding="utf-8")
    monkeypatch.delenv("ARCREEL_TEST_FILLED", raising=False)
    monkeypatch.delenv("ARCREEL_TEST_BLANK", raising=False)

    env_init.load_env_file(env_file)

    assert os.environ["ARCREEL_TEST_FILLED"] == "v"
    assert os.environ["ARCREEL_TEST_BLANK"] == ""


def test_returns_keys_whose_values_diverge(env_file: Path, monkeypatch: pytest.MonkeyPatch):
    """Only retained process values differing from files are inconsistent; .env-filled empty values are not."""
    env_file.write_text("AUTH_PASSWORD=new\nAUTH_USERNAME=admin\nAUTH_TOKEN_SECRET=s\n", encoding="utf-8")
    monkeypatch.setenv("AUTH_PASSWORD", "old")
    monkeypatch.setenv("AUTH_USERNAME", "admin")
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "")

    diverged = env_init.load_env_file(env_file)

    assert diverged == ("AUTH_PASSWORD",)


def test_divergence_warning_names_keys_without_values(
    env_file: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    env_file.write_text("AUTH_PASSWORD=file-secret-value\n", encoding="utf-8")
    monkeypatch.setenv("AUTH_PASSWORD", "env-secret-value")
    env_init.load_env_file(env_file)

    with caplog.at_level(logging.WARNING, logger=env_init.__name__):
        env_init.warn_if_env_diverges_from_file()

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "AUTH_PASSWORD" in message
    assert "docker compose up -d" in message
    assert "file-secret-value" not in message
    assert "env-secret-value" not in message


def test_no_warning_when_env_matches_file(
    env_file: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    env_file.write_text("AUTH_PASSWORD=same\n", encoding="utf-8")
    monkeypatch.setenv("AUTH_PASSWORD", "same")
    env_init.load_env_file(env_file)

    with caplog.at_level(logging.WARNING, logger=env_init.__name__):
        env_init.warn_if_env_diverges_from_file()

    assert caplog.records == []


def test_unreadable_env_file_is_ignored(env_file: Path, monkeypatch: pytest.MonkeyPatch):
    """Sandbox .env denial must not fail importing lib."""
    env_file.write_text("ARCREEL_TEST_DENIED=v\n", encoding="utf-8")
    monkeypatch.delenv("ARCREEL_TEST_DENIED", raising=False)

    def deny(*_args, **_kwargs):
        raise PermissionError("denied by sandbox")

    monkeypatch.setattr(env_init, "DotEnv", deny)

    assert env_init.load_env_file(env_file) == ()
    assert "ARCREEL_TEST_DENIED" not in os.environ
