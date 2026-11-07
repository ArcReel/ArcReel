"""Production template instance distributed with the core library."""

from pathlib import Path

from .engine import PromptTemplates

BUILTIN_DIRECTORY = Path(__file__).parent / "templates"
builtin_templates = PromptTemplates(BUILTIN_DIRECTORY)
