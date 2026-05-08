"""分集节奏铁则（首镜 4 秒钩子 / 15 秒冲突节点 / 末镜定格卡点）。

注意：本模块的 DRAMA_PACING_RULES / NARRATION_PACING_RULES 文本会被
agent_runtime_profile/.claude/agents/normalize-drama-script.md 与
split-narration-segments.md 逐字镜像；漂移由 test_subagent_md_sync.py 防御。
"""

DRAMA_PACING_RULES = """
分集节奏铁则（请把以下要求体现到首镜与末镜的视觉描述上）：
- 开篇钩子：第 1 个分镜的 duration_seconds 设为 4 秒；该镜头画面必须以强视觉冲击/悬念/危机/极致反差作为焦点，杜绝静止介绍性远景。
- 中段冲突密度：每 15 秒至少出现 1 个冲突节点（动作转折 / 情绪反差 / 关系撕裂 / 异常事件），通过分镜的画面权重和镜头景别变化体现。
- 末镜定格卡点：本集最后一个分镜画面停在悬念升级或情绪极致瞬间，shot_type 推荐 Close-up 或 Extreme Close-up，禁止平稳收尾。
""".strip()


NARRATION_PACING_RULES = """
说书节奏要求：
- 首段画面对应朗读前 4 秒，必须用强视觉冲击 / 悬念 / 危机匹配钩子台词，杜绝平铺叙述。
- 末段画面服务于卡点留悬（特写人物 / 关键物件 / 极端表情），shot_type 推荐 Close-up 或 Extreme Close-up。
""".strip()


def render_pacing_section(content_mode: str) -> str:
    if content_mode == "drama":
        return DRAMA_PACING_RULES
    if content_mode == "narration":
        return NARRATION_PACING_RULES
    raise ValueError(f"unknown content_mode: {content_mode!r}")


__all__ = [
    "DRAMA_PACING_RULES",
    "NARRATION_PACING_RULES",
    "render_pacing_section",
]
