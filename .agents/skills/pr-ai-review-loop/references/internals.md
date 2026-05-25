# pr-ai-review-loop 设计内幕

本文承接 SKILL.md 中外移的设计依据。SKILL.md 描述"做什么",本文说明"为什么这样做"。仅在需要理解机制或调整规则时阅读;日常调度按 SKILL.md 执行即可。

## 节奏控制(self-pace)

### 为什么不用 `/loop` 包装

`/loop` 是定时器驱动的固定节拍工具,适合"每隔 N 分钟重复一次"的轮询。但 AI reviewer 的响应时机是事件驱动的——CodeRabbit 跟新 push 一般 60-90 秒、Gemini 5 分钟内、Codex 取决于仓库配置——固定间隔要么过密(浪费 API 调用与 quota),要么过疏(错过响应窗口)。

本 skill 改用 ScheduleWakeup 自带的动态节奏:每轮 poll 完成后,根据当前状态(刚 push / 刚触发 / 常规等待)选择下一次唤醒延迟。harness 到时会重新加载本 skill 的上下文继续执行。

### 唤醒延迟的选择

| 场景 | 延迟 | 依据 |
|---|---|---|
| 新 HEAD 后首轮 | 180 秒 | reviewer cold-start 上限;CR 自动跟需 60-90 秒,Gemini PR opened 自动 review 在 5 分钟内出结果(取上限的 60%) |
| 手动触发后 | 120 秒 | Gemini 响应通常 90-120 秒,60 秒易错过 |
| 常规等待 | 60 秒 | 处于 prompt cache 5 分钟窗口内,不丢缓存 |

所有延迟均落在 5 分钟的 prompt cache 窗口内(60s / 120s / 180s),保证下一轮上下文从缓存读取,不付额外 token 成本。只有"超过 15 分钟无响应"的暂停场景会跨窗口,此时本就需要暂停询问用户,缓存失效在所难免。

### Wakeup prompt 字段

`ScheduleWakeup` 的 `prompt` 字段需要 harness 能重新拉起本 skill。一般写明 PR 号与当前轮次摘要即可,例如 `/pr-ai-review-loop 645 — 继续轮询,round=2, ...`。harness 通过 `/pr-ai-review-loop` 前缀识别 skill。

## 状态字段

本 skill 维护三个状态字段,均存于对话上下文,不落盘。三者的更新触发条件不同,反映各自的语义:

| 字段 | 更新时机 | 跨 HEAD 行为 | 服务于 |
|---|---|---|---|
| `round_count` | HEAD SHA 或 `last_push_at` 与上一轮记录不同时 +1 | 累加,不重置 | 收敛兜底 #1(≥ 8 轮停问) |
| `topic_history` | 每次 poll 拉到 reviewer 新意见时追加一条 | 累积,不清空 | 主题指纹比对(收敛兜底 #3 与「运行模式」B 节) |
| `last_commit_shapes` | HEAD SHA 或 `last_push_at` 变化时追加一条形状标签 | 长度 ≤ 3 的滑窗 | 收敛兜底 #2(连续 2 轮 nit 停问) |

### round_count 不计 poll 次数

"轮"对齐"修复 → push → reviewer 响应"周期,而非"poll 调用次数"。HEAD 未变时多次 wakeup 回来等待 reviewer 出意见,不应累加轮数,否则收敛兜底 #1 会在 reviewer cold-start 期间误触发。

### topic_history 跨 HEAD 累积

收敛兜底 #3 的语义是"同一主题被 reviewer 连提 ≥ 3 轮",衡量的是"reviewer 在多个 HEAD 上反复提同一意见",必须跨 HEAD 累积才有意义。清空会破坏判定。

同一 HEAD 内多次 poll 拉到同一条 reviewer comment 时只入库一次(以 comment id 去重),避免噪声。

主题指纹采用语义相似度(由 Claude 在上下文中按摘要文本比对),不脚本化:Markdown 全文比对开销大、关键词匹配又过粗,Claude 的语义判断在小规模(< 30 条)历史下足够准确。

### last_commit_shapes 形状滑窗

形状由 `classify_commits.sh` 输出 + Claude 概括,标签如 `all_nit/format`、`contains_functional`、`refactor_only`。滑窗长度 3 足以判定"连续 2 轮均为 nit"(收敛兜底 #2),更长无收益。

## quota 经济学

CodeRabbit 自动跟 push,不消耗 quota 配额;Gemini 与 Codex 每次手动触发均会重新扫描整个 PR diff,API 调用与 token 开销均不可忽视。因此本 skill 在以下场景**跳过手动触发**(由 `classify_commits.sh` 配合 Claude 判定):

- 当轮 push 的所有 commit 均属于 fix-up 性质(nit / format / typo / 单字段调整 / 小 bug 修复)
- CodeRabbit 自动跟即可,不再额外触发 Gemini / Codex

跳过触发并不代表跳过 review:CodeRabbit 仍会自动跟新 commit;若 CR 给出新意见,正常进入步骤 3 处理。

## 暂停场景分类

本 skill 区分两类暂停:故障类与调度类。

**故障类**(SKILL.md 「运行模式」A 节):bot 报错、reviewer 长时间无响应、`gh` 认证失败、脚本异常、review 语义模糊到无法转交 receiving-code-review。这些是循环本身无法自愈的状态,必须暂停询问用户。

**调度类**(SKILL.md 「运行模式」B 节):reviewer 之间冲突、同一主题反复争议、修复方案涉及业务取舍。这些不是"循环故障",而是"超出 skill 调度范围的根本性争议",需用户决策(是否升级 ADR、采纳哪一方意见、是否影响业务意图)。

"无人值守"指循环内可自动决定的动作(poll / 触发 / 收集意见 / 调用 receiving-code-review)继续自动决定,**不**包括上述根本性争议。

## 与其他 skill 的边界

本 skill 仅负责调度:何时 poll、何时触发、何时转交、何时结束。具体动作分工:

- **回应 / 实施 / 反驳 review 意见**:转交 `receiving-code-review`
- **验证修复是否真的解决问题**:`verify`(可由 receiving-code-review 自行调用)
- **创建 PR**:`commit-commands:commit-push-pr`

转交 `receiving-code-review` 时,所有 reviewer 的本轮新意见**合并为一次调用**,而非每家单独调用。合并时必须将 `gemini.reviews[*].body`(summary)整段贴入上下文——Gemini 的某些建议仅出现在 summary 中、inline 为空,只贴 inline 会丢失意见。
