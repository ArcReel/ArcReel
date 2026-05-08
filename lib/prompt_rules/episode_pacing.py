"""短剧 / 说书节奏建议（drama / narration）。

短剧体裁特征：开篇 ~4 秒钩子、中段每 ~15 秒一个情绪转折、末镜停在情绪极致瞬间。
这不是 prompt engineering 启发，而是体裁约束，需要在 builder 与 subagent .md 间
共享同一份措辞，避免漂移。

注意：本模块的 DRAMA_PACING_RULES / NARRATION_PACING_RULES 文本会被
agent_runtime_profile/.claude/agents/normalize-drama-script.md 与
split-narration-segments.md 逐字镜像；漂移由 test_subagent_md_sync.py 防御。

文风：用"宜 / 例"而非"必须 / 禁止"，给 LLM 在边界条件下的判断空间
（如视频模型最短只支持 5 秒时，"~4 秒"比"=4 秒"更可执行）。
"""

DRAMA_PACING_RULES = """分集节奏（短剧体裁建议）：
- 开篇 ~4 秒承担钩子职能：用强冲击 / 悬念 / 危机切入，避免介绍性远景。
- 中段每 ~15 秒宜安排一次转折点（动作转折 / 情绪反差 / 关系撕裂 / 异常事件），
  通过画面权重和景别变化呈现，避免长段平铺。
- 末镜停在情绪极致瞬间，shot_type 倾向 Close-up / Extreme Close-up，
  给观众留下回看的钩子。""".strip()


NARRATION_PACING_RULES = """说书节奏建议：
- 首段画面（朗读前 ~4 秒）服务于钩子：用强冲击 / 悬念 / 危机匹配钩子台词，
  避免平铺式开场。
- 末段画面服务于卡点留悬（特写人物 / 关键物件 / 极端表情），
  shot_type 倾向 Close-up / Extreme Close-up。""".strip()


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
