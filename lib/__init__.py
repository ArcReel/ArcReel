# AI Anime Generator Library
# Shared Python library for Gemini API wrappers and project management

# Initialize the environment first (activate .venv and load .env)
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
