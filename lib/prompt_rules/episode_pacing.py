"""短剧 / 旁白解说节奏建议（drama / narration）。

短剧体裁特征：开篇 ~4 秒钩子、中段每 ~15 秒一个情绪转折、末镜停在情绪极致瞬间。
这不是 prompt engineering 启发，而是体裁约束，需要在 builder 与子智能体 .md 间
共享同一份措辞。

规则正文存放在 ``agent_runtime_profile/.claude/references/`` 下，经
:func:`lib.agent_profile.read_profile_reference` 读入：那里既是子智能体读取参考文档的
位置，也是本模块拼进 prompt 的来源，两侧读同一个文件，仓库里只有一份文本。

文风：用"宜 / 例"而非"必须 / 禁止"，给 LLM 在边界条件下的判断空间
（如视频模型最短只支持 5 秒时，"~4 秒"比"=4 秒"更可执行）。
"""

from __future__ import annotations

from lib.agent_profile import read_profile_reference

#: content_mode → ``.claude/references/`` 下的规则文件名。
PACING_RULE_FILES = {
    "drama": "episode-pacing-drama.md",
    "narration": "episode-pacing-narration.md",
}


def render_pacing_section(content_mode: str) -> str:
    try:
        file_name = PACING_RULE_FILES[content_mode]
    except KeyError:
        raise ValueError(f"unknown content_mode: {content_mode!r}") from None
    return read_profile_reference(file_name)


__all__ = [
    "PACING_RULE_FILES",
    "render_pacing_section",
]
