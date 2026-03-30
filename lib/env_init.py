"""
环境初始化模块

加载 .env 文件。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def init_environment():
    """
    初始化项目环境

    1. 定位项目根目录
    2. 加载 .env 文件
    """
    # 获取项目根目录（lib 的父目录）
    lib_dir = Path(__file__).parent
    project_root = lib_dir.parent

    # 加载 .env 文件
    try:
        from dotenv import load_dotenv

        env_path = project_root / ".env"
        if env_path.exists():
            load_dotenv(env_path)
        else:
            load_dotenv()
    except ImportError:
        pass  # python-dotenv 未安装时跳过

    return project_root


# 模块导入时自动初始化
PROJECT_ROOT = init_environment()
