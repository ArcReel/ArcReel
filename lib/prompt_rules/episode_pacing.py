"""按 content_mode 读取内置节奏共享片段的源文，供脚本规划 builder 拼接。"""

from lib.prompt_templates.builtin import BUILTIN_DIRECTORY


def render_pacing_section(content_mode: str) -> str:
    if content_mode not in {"drama", "narration"}:
        raise ValueError(f"unknown content_mode: {content_mode!r}")
    return (BUILTIN_DIRECTORY / "partials/shared/pacing" / f"{content_mode}.md").read_text(encoding="utf-8")
