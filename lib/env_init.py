"""
环境初始化模块

加载 .env 文件。

provider 密钥的真相源是 DB。如果 .env 残留 provider key 写入 os.environ，
父进程 fork 出的 Bash 沙箱子进程会继承到，违反安全红线 — 由
`server.app.assert_no_provider_secrets_in_environ()` 在 lifespan 启动期 fail-fast。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def init_environment():
    """初始化项目环境：定位项目根 + load .env。"""
    lib_dir = Path(__file__).parent
    project_root = lib_dir.parent

    try:
        from dotenv import load_dotenv

        env_path = project_root / ".env"
        if env_path.exists():
            load_dotenv(env_path)
        else:
            load_dotenv()
    except ImportError:
        pass

    return project_root


PROJECT_ROOT = init_environment()
