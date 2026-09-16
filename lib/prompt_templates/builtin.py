"""随核心库分发的生产模版实例。"""

from pathlib import Path

from .engine import PromptTemplates

BUILTIN_DIRECTORY = Path(__file__).parent / "templates"
builtin_templates = PromptTemplates(BUILTIN_DIRECTORY)
