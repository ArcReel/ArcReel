"""
环境初始化模块

加载 .env 文件。

provider 密钥的真相源是 DB。如果 .env 残留 provider key 写入 os.environ，
父进程 fork 出的 Bash 沙箱子进程会继承到，违反安全红线 — 由
`server.app.assert_no_provider_secrets_in_environ()` 在 lifespan 启动期 fail-fast。
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import find_dotenv
    from dotenv.main import DotEnv
except ImportError:  # pragma: no cover - python-dotenv 是运行依赖，缺失时不加载 .env
    DotEnv = None
    find_dotenv = None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _EnvFileLoad:
    """最近一次读取 .env 的结果，供启动后补记告警（读取发生在 import 期，日志尚未配置）。"""

    path: Path | None
    diverged_keys: tuple[str, ...]


_last_load = _EnvFileLoad(path=None, diverged_keys=())


def load_env_file(env_path: Path) -> tuple[str, ...]:
    """把 ``env_path`` 中的变量载入进程环境，返回与进程环境取值不一致的键名。

    覆盖规则：
    - 进程环境里不存在的键，取 .env 中的值（含空值）；
    - 进程环境里为空串的键视为未设置，.env 中的非空值生效。Docker Compose 的 ``env_file``
      会把模板里的 ``AUTH_PASSWORD=`` 注入为空串，不这样处理时 .env 里后来写入的值永远进不来；
    - 进程环境里显式设置的非空值优先，不被 .env 覆盖。与文件值不同的键名记入返回值。

    文件读不了（沙箱拒读、不存在）或 PYTHON_DOTENV_DISABLED 开启时，返回空元组。
    """
    global _last_load
    if DotEnv is None:
        return ()
    if os.environ.get("PYTHON_DOTENV_DISABLED", "").casefold() in {"1", "true", "t", "yes", "y"}:
        return ()
    try:
        # 插值沿用 load_dotenv 的进程环境优先级，避免文件中的引用改变显式配置。
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
    """清掉最近一次读取 .env 的记录。"""
    global _last_load
    _last_load = _EnvFileLoad(path=None, diverged_keys=())


def warn_if_env_diverges_from_file() -> None:
    """进程环境与 .env 取值不一致时记一条警告；只列键名，不输出取值。"""
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
    """初始化项目环境：定位项目根 + load .env。

    在 Agent Bash 沙箱子进程里，``.env`` 会被沙箱拒读（macOS sandbox-exec /
    Linux bwrap deny list），``env_path.exists()`` 会抛 PermissionError。
    沙箱子进程不需要 .env —— provider/Anthropic env 已由 SessionManager
    通过 ``options.env`` 显式注入。这里吞掉 OSError 让 ``import lib`` 链
    不被沙箱拦截。
    """
    lib_dir = Path(__file__).parent.parent
    project_root = lib_dir.parent

    if find_dotenv is None:
        return project_root

    env_path = project_root / ".env"
    # 沙箱里 stat 与 open 是两次独立 syscall：exists() 可能放行而
    # 读取仍被 denyRead 拦截。整段统一用 OSError 兜底，
    # 任何文件访问失败都视为 ".env 不可用"，降级继续 import。
    try:
        if not env_path.exists():
            # 项目根没有 .env 时沿用 python-dotenv 默认的向上回溯查找
            found = find_dotenv()
            env_path = Path(found) if found else None
        if env_path is not None:
            load_env_file(env_path)
    except OSError:
        pass

    return project_root


PROJECT_ROOT = init_environment()
