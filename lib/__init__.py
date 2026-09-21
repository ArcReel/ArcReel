# AI Anime Generator Library
# 共享 Python 库，用于 Gemini API 封装和项目管理

# 首先初始化环境（激活 .venv，加载 .env）
from lib.infra.env_init import PROJECT_ROOT
from lib.infra.validation_messages import ValidationResult
from lib.project.data_validator import DataValidator, validate_episode, validate_project
from lib.project.project_manager import ProjectManager

__all__ = [
    "PROJECT_ROOT",
    "DataValidator",
    "ProjectManager",
    "ValidationResult",
    "validate_episode",
    "validate_project",
]
