# AI Anime Generator Library
# 共享 Python 库，用于 Gemini API 封装和项目管理

# 首先初始化环境（激活 .venv，加载 .env）
from .data_validator import DataValidator, ValidationResult, validate_episode, validate_project
from .env_init import PROJECT_ROOT
from .project_manager import ProjectManager

__all__ = [
    "ProjectManager",
    "PROJECT_ROOT",
    "DataValidator",
    "validate_project",
    "validate_episode",
    "ValidationResult",
]
