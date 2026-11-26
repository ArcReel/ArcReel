from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.local_instance import (
    SENTINEL_NAME,
    build_backend_env,
    build_parser,
    cleanup_data_dir,
    ensure_frontend_dist,
    main,
    prepare_data_dir,
    wait_until_healthy,
)


class FakeResponse:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeProcess:
    def __init__(self, pid: int = 43210, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


def test_prepare_and_cleanup_data_dir_enforce_sentinel(tmp_path: Path) -> None:
    non_empty = tmp_path / "existing"
    non_empty.mkdir()
    (non_empty / "user.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(RuntimeError, match=SENTINEL_NAME):
        prepare_data_dir(non_empty)

    with pytest.raises(RuntimeError, match=SENTINEL_NAME):
        cleanup_data_dir(non_empty)
    assert (non_empty / "user.txt").is_file()

    managed = prepare_data_dir(tmp_path / "managed")
    assert (managed / SENTINEL_NAME).is_file()
    cleanup_data_dir(managed)
    assert not managed.exists()


def test_build_backend_env_strips_provider_secrets_and_isolates_storage(tmp_path: Path) -> None:
    base_env = {
        "PATH": "/usr/bin",
        "DATABASE_URL": "postgresql://prod",
        "AI_ANIME_PROJECTS": "/prod/projects",
        "GEMINI_API_KEY": "secret-gemini",
        "OPENAI_API_KEY": "secret-openai",
        "AUTH_TOKEN_SECRET": "parent-only",
        "CLAUDE_CODE_OAUTH_TOKEN": "parent-only",
        "AUTH_USERNAME": "leaked-user",
        "AUTH_PASSWORD": "leaked-pass",
    }

    no_auth = build_backend_env(tmp_path, auth_enabled=False, base_env=base_env)
    assert {"AUTH_TOKEN_SECRET", "CLAUDE_CODE_OAUTH_TOKEN"}.isdisjoint(no_auth)
    assert no_auth["ARCREEL_DATA_DIR"] == str(tmp_path / "data")
    assert no_auth["PYTHON_DOTENV_DISABLED"] == "1"
    assert no_auth["ARCREEL_SKIP_CATALOG_SYNC"] == "1"
    assert no_auth["AUTH_ENABLED"] == "false"
    for removed in ("DATABASE_URL", "AI_ANIME_PROJECTS", "GEMINI_API_KEY", "OPENAI_API_KEY", "AUTH_PASSWORD"):
        assert removed not in no_auth

    with_auth = build_backend_env(
        tmp_path,
        auth_enabled=True,
        username="qa-admin",
        password="qa-password",
        base_env=base_env,
    )
    assert with_auth["AUTH_ENABLED"] == "true"
    assert with_auth["AUTH_USERNAME"] == "qa-admin"
    assert with_auth["AUTH_PASSWORD"] == "qa-password"


def test_ensure_frontend_dist_builds_or_verifies_artifact(tmp_path: Path) -> None:
    dist_index = tmp_path / "frontend" / "dist" / "index.html"
    with pytest.raises(RuntimeError, match="--skip-build"):
        ensure_frontend_dist(tmp_path, skip_build=True)

    commands: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(tuple(argv))
        dist_index.parent.mkdir(parents=True, exist_ok=True)
        dist_index.write_text("<!doctype html>", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="built", stderr="")

    assert ensure_frontend_dist(tmp_path, runner=runner) == dist_index
    assert len(commands) == 1
    assert commands[0][-3:] == ("exec", "vite", "build")


def test_wait_until_healthy_detects_ready_early_exit_and_timeout() -> None:
    attempts = 0

    def opener(url: str, timeout: float = 2.0) -> FakeResponse:
        del timeout
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise OSError(f"refused: {url}")
        return FakeResponse({"status": "ok", "commit": None})

    payload = wait_until_healthy("http://127.0.0.1:18159", opener=opener, sleeper=lambda _: None)
    assert payload["status"] == "ok"
    assert attempts == 2

    exited = FakeProcess(returncode=1)
    with pytest.raises(RuntimeError, match="提前退出"):
        wait_until_healthy(
            "http://127.0.0.1:18159",
            process=exited,
            opener=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("down")),
            sleeper=lambda _: None,
        )

    ticks = iter([0.0, 0.5, 1.5])
    with pytest.raises(TimeoutError, match="超时"):
        wait_until_healthy(
            "http://127.0.0.1:18159",
            timeout=1.0,
            clock=lambda: next(ticks),
            sleeper=lambda _: None,
            opener=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("down")),
        )


def test_cli_help_exposes_subcommands_and_flags(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    help_text = parser.format_help()
    for token in ("start", "status", "stop"):
        assert token in help_text

    with pytest.raises(SystemExit) as exc_info:
        main(["start", "--help"])
    assert exc_info.value.code == 0
    start_help = capsys.readouterr().out
    for option in ("--port", "--data-dir", "--state-file", "--auth", "--skip-build", "--wait-timeout"):
        assert option in start_help
