---
status: accepted
---

# 消息改写由应用层前缀分叉实现，不依赖 SDK 原生 fork

消息改写要求「回到某条历史用户消息发出前，用改写后的内容重新发出」，即从会话中间点分叉并丢弃其后内容。Claude Agent SDK 不提供这个能力：`fork_session` 只能从会话末尾复制**整史**分叉，`rewind_files` 只回退文件、明确不回退对话（官方文档原话 "It does not rewind the conversation itself"）；「从指定消息处 resume/fork」是上游已知但未实现的 feature request。Claude Code CLI 的 `/rewind` 能回退对话，但那是 CLI 在应用层截断重放自己 transcript 的内部功能，未经 SDK 下放。

决定在 ArcReel 应用层自实现**前缀分叉**：把改写点之前的 transcript 前缀（含 subagent 子路径）从 DB 镜像（`agent_session_entries`）复制到新 session_id 下，以 `resume=新id` 启动新 SessionActor，改写后的消息作为新会话首个输入。这与 CLI 内部实现 `/rewind` 的思路同构——在自己持有的 transcript 存储上操作，区别只是 CLI 截 jsonl、我们复制 DB 行。

关键约束与风险收敛：

- **分叉点固定在用户消息边界**。前一轮次必然完整收尾，tool_use/tool_result 配对与 sidechain 完整性由此保证；会话存在未决问答卡片时禁止改写，恰好避开前一轮次存在悬空 tool_use 的时刻。
- **封装为单一服务入口**。「以拼装出的前缀供 SDK resume」是非官方用法，风险与知识都收敛在这一处，调用方不感知前缀是怎么拼出来的。
- **前置依赖**：SDK 侧用户消息 uuid 目前在回显去重时被丢弃，需持久化 ArcReel 用户消息 id ↔ SDK entry 的映射，改写锚点才能定位到 transcript 中的截断位置。

## 明确不采用

- **SDK 原生 `fork_session` + prompt 覆盖**（唯一的官方姿势）：末尾分叉带走全部历史，被否定的错误分支仍在 agent 上下文里吃 token、继续污染后续生成——与「回到发出前」的语义直接矛盾，纠偏效果退化为追加指令。
- **原地截断原会话**：删除编辑点之后的 transcript 行与事件日志行、同 session_id resume。破坏事件日志的 append-only 根基（`docs/adr/0048`），前端增量投影器与 SSE `Last-Event-ID` 续传语义连带失效，且被弃分支的备份要另行实现。前缀分叉下这些问题不存在：原会话数据整体不动即是备份，新会话事件日志按 `docs/adr/0048` 既有的重放重建机制从 transcript 懒生成。
- **等待上游实现**：中间点分叉在上游长期处于 feature request 状态，无时间表；纠偏能力是弱模型场景的现实痛点，不适合无限期挂起。

## Consequences

- 原会话标记 superseded 并记录指向新会话的指针，会话列表过滤隐藏，数据完整保留；闲置驱逐照常。
- 改写不回退文件与项目数据副作用。SDK 的 `enable_file_checkpointing` + `rewind_files` 可作后续增强，但其盲区（Bash 写入不追踪、项目数据经任务队列/DB 落盘）决定了它最多是部分回滚，不改变本决策。
- 事件日志与前端投影契约零改动：截断语义完全由「新会话」表达，append-only、前缀不变、SSE 续传三个既有约定原样成立。
