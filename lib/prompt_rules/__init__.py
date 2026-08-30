"""Prompt 规则单一真相源。

收录判据：跨多条 prompt 构建路径共享、且与具体视觉风格 / backend 无关的体裁或产品约束。
`episode_pacing` 是 drama / narration 的节奏建议（开篇钩子 / 中段冲突 / 末镜定格），正文存放在
`agent_runtime_profile/.claude/references/` 下，builder 与子智能体读同一个文件；
`episode_target_duration` 是三条脚本规划共享的单集目标时长措辞。

属于"prompt 写作指导"而非可独立维护的规则文本的，写在
`lib/prompt_builders.py` / `lib/prompt_builders_script.py` 内部，不收进本包。
"""
