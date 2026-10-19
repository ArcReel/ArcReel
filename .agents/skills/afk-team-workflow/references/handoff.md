# Handoff

每个 issue 一份 `.afk/<batch-id>/handoff-<N>.md`；stage 集成审查与 review loop 共用 `.afk/<batch-id>/handoff-stage-<K>.md`。handoff 是交接便签，不是工作日志。

各角色追加自己的「实现」「集成审查」「Base sync」或「审查循环」段。保留下一位执行者需要、但 diff、issue、PR 无法重推的判断：关键取舍、环境限制、未决风险，以及 pushback、故障或冲突处置的依据。已有记录可引用。

Base sync 段留下 rebase 前后的远程 HEAD；审查硬停时留下远程 HEAD，并说明进展、未决事项及建议的下一步。其余交付信息按各角色契约回报。

follow-up 记录尚未解决的问题或改进机会及依据，可以涉及工程、产品设计或执行方式；区分事实与猜测，价值评估留给复盘。批次内清尾与立 issue 都归 team-lead，按范围与 [escalation.md](../../../../docs/agents/escalation.md) 分流。

收尾自查发现的教训候选按 [lessons.md](../../../../docs/agents/lessons.md) 的条目格式单独写一段，由 team-lead 发到 stage PR。

其他复盘线索随相关判断顺手留下；handoff 可以没有复盘候选，知识分类留给复盘。
