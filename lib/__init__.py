# AI Anime Generator Library
# Shared Python library for Gemini API wrappers and project management

# Initialize environment first (activate .venv, load .env)
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
