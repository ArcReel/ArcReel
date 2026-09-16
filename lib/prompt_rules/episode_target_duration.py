"""读取内置单集目标时长共享片段的源文，供字符串拼接的脚本规划 builder 使用。"""

from lib.prompt_templates.builtin import BUILTIN_DIRECTORY

EPISODE_TARGET_DURATION_RULE_TEMPLATE = (
    (BUILTIN_DIRECTORY / "partials/shared/episode_target_duration_rule.md")
    .read_text(encoding="utf-8")
    .replace("{{ episode_target_duration }}", "{seconds}")
)


def render_episode_target_duration_rule(target_seconds: int | None) -> str:
    """未设目标时不注入；返回值不带结尾句号。"""
    if target_seconds is None:
        return ""
    return EPISODE_TARGET_DURATION_RULE_TEMPLATE.format(seconds=target_seconds)
