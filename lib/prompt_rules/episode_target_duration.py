"""单集目标时长软约束的提示词措辞（drama / narration / 参考生视频三条脚本规划共享）。

三条拆分路径决定「本集拆多少个单元」的口径必须一致，否则同一个项目设置在不同创作类型下
被读成不同强度。措辞集中在这里，由三条路径的 builder 注入；子智能体 .md 不复述这段措辞，
只写死 ``EPISODE_TARGET_DURATION_FIELD`` 这个机器契约字段名。

文风：软目标而非硬上限——写明两个方向的越界都允许（内容不足宁少凑，内容需要可超出），
避免 LLM 把「目标」读成「必须凑满 / 不得超过」而注水或删情节。
"""

from __future__ import annotations

EPISODE_TARGET_DURATION_RULE_TEMPLATE = (
    "本集成片目标时长约 {seconds} 秒：据此决定本集的单元数与拆分粒度，"
    "让各单元时长合计向该目标靠拢。这是软目标、不是硬上限——"
    "内容不足以支撑目标时宁可少拆几个，不要靠注水 / 切碎凑满；"
    "内容确实需要更多篇幅时可以超出目标，不要为压进目标删减必要情节"
)


def render_episode_target_duration_rule(target_seconds: int | None) -> str:
    """渲染单集目标时长软约束句；未设目标（``None``）时返回空串，调用方据此不注入该段。

    返回值不带结尾句号，供调用方按各自的规则句式拼接（三条 prompt 的时长段句读格式不同）。
    """
    if target_seconds is None:
        return ""
    return EPISODE_TARGET_DURATION_RULE_TEMPLATE.format(seconds=target_seconds)
