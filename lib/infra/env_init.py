"""Initialize the environment and load .env.

The database owns provider secrets. Stale .env provider keys in os.environ would
be inherited by forked Bash sandbox subprocesses, violating the security boundary.
``server.app.assert_no_provider_secrets_in_environ()`` fails fast during lifespan startup.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import find_dotenv
    from dotenv.main import DotEnv
except ImportError:  # pragma: no cover - python-dotenv is a runtime dependency; skip .env loading when absent
    DotEnv = None
    find_dotenv = None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _EnvFileLoad:
    """Result of the last .env read, retained for startup warnings because imports happen before logging is configured."""

    path: Path | None
    diverged_keys: tuple[str, ...]


_last_load = _EnvFileLoad(path=None, diverged_keys=())


def load_env_file(env_path: Path) -> tuple[str, ...]:
    """Load ``env_path`` into the process environment and return keys whose file values differ.

    Precedence:
    - Missing process keys take file values, including empty values.
    - Empty process strings count as unset, allowing nonempty file values. Docker
      Compose ``env_file`` injects template ``AUTH_PASSWORD=`` as an empty string; otherwise
      a value later added to .env would never take effect.
    - Explicit nonempty process values take precedence; record keys differing from
      their file values in the return value.

    Return an empty tuple if the file cannot be read (sandbox denial or absence),
    or when PYTHON_DOTENV_DISABLED is enabled.
    """
    global _last_load
    if DotEnv is None:
        return ()
    if os.environ.get("PYTHON_DOTENV_DISABLED", "").casefold() in {"1", "true", "t", "yes", "y"}:
        return ()
    try:
        # Keep load_dotenv process-environment precedence so file interpolation cannot change explicit configuration.
        file_values = DotEnv(env_path, encoding="utf-8", override=False).dict()
    except OSError:
        return ()
    diverged: list[str] = []
    for key, value in file_values.items():
        if value is None:
            continue
        current = os.environ.get(key)
        if current is None or (current == "" and value):
            os.environ[key] = value
        elif current and current != value:
            diverged.append(key)
    _last_load = _EnvFileLoad(path=Path(env_path), diverged_keys=tuple(diverged))
    return _last_load.diverged_keys


def reset_env_file_load_for_tests() -> None:
    """Clear the last .env read record."""
    global _last_load
    _last_load = _EnvFileLoad(path=None, diverged_keys=())


def warn_if_env_diverges_from_file() -> None:
    """Warn about differing process/.env values, listing keys only and never values."""
    if not _last_load.diverged_keys:
        return
    logger.warning(
        "进程环境变量与 %s 中的取值不一致，按进程环境变量生效：%s。"
        "Docker 部署修改 .env 后需在 Compose 目录执行 docker compose up -d 重建容器，"
        "docker compose restart 不会重新载入 .env",
        _last_load.path,
        ", ".join(_last_load.diverged_keys),
    )


def init_environment():
    """Locate the project root and load ``.env``.

    The Agent Bash sandbox denies .env reads (macOS sandbox-exec / Linux bwrap deny
    list); ``env_path.exists()`` can raise PermissionError. These subprocesses do not
    need .env because SessionManager explicitly injects provider/Anthropic values
    through ``options.env``. Swallow OSError so sandbox restrictions do not break ``import lib``.
    """
    lib_dir = Path(__file__).parent.parent
    project_root = lib_dir.parent

    if find_dotenv is None:
        return project_root

    env_path = project_root / ".env"
    # In the sandbox, stat and open are separate syscalls: exists() may succeed while
    # denyRead still blocks reading. Catch OSError around the whole sequence;
    # any file-access failure means .env is unavailable; continue importing without it.
    try:
        if not env_path.exists():
            # Without a root .env, retain python-dotenv's default upward search.
            found = find_dotenv()
            env_path = Path(found) if found else None
        if env_path is not None:
            load_env_file(env_path)
    except OSError:
        pass

    return project_root


PROJECT_ROOT = init_environment()
