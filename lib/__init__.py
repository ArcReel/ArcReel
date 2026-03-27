# AI Anime Generator Library
# 共享 Python 库，用于 Gemini API 封装和项目管理

# 首先初始化环境（激活 .venv，加载 .env）
from .env_init import PROJECT_ROOT

from .project_manager import ProjectManager
from .data_validator import DataValidator, validate_project, validate_episode, ValidationResult

__all__ = ['ProjectManager', 'PROJECT_ROOT', 'DataValidator', 'validate_project', 'validate_episode', 'ValidationResult']
