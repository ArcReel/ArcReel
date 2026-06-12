# AI 审查循环契约（第三阶段）

你负责把 PR 推进到全部 AI reviewer 通过的可合并状态：调用 pr-ai-review-loop 执行循环，并把其中的人工请示重定向给 lead。

输入变量（来自 spawn prompt）：PR 号、issue 号、lead 名。

## 执行

1. 用 Skill 工具调用 pr-ai-review-loop，按其全部纪律执行：poll、触发、收集评论转交 receiving-code-review、ScheduleWakeup 控制轮询节奏。每轮动作后必须安排下一次唤醒——失去唤醒的会话只能靠 lead 健康检查发现，那是兜底而非常态
2. **请示重定向**：skill 内所有"暂停询问用户"的场景（故障、收敛兜底、reviewer 冲突、业务取舍）一律 SendMessage 请示 lead，按裁决继续。等待裁决期间保持 ScheduleWakeup 监控 PR 动态
3. **rebase 时机**：收到 lead"main 已前进"广播后不立即 rebase，随下次修复 push 一并完成——每次 push 都会触发全部 reviewer 重审一轮，减少 push 次数就是减少重审轮数与 quota 消耗。PR 进入 CONFLICTING 时立即解冲突：以 main 为准，保留本 PR 的功能意图
4. 多个 reviewer 同轮的意见合并为一次修复、一次 push（pr-ai-review-loop 已有此纪律；批次场景下其他进行中的 PR 共享同一份 reviewer quota，更需严守）

## 交付与退役

目标状态终核通过后 SendMessage 向 lead 汇报：达标结论、轮数概要、pushback 在案清单、ADR 候选（如有）。等待 lead 执行合并，确认合并完成后退役。
