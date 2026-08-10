---
status: accepted
---

# 消息改写由应用层前缀分叉实现，不依赖 SDK 原生 fork

消息改写要求「回到某条历史用户消息发出前，用改写后的内容重新发出」，即从会话中间点分叉并丢弃其后内容。Claude Agent SDK 的 `fork_session` 接近但不够用：它接受 `up_to_message_id` 在指定消息处截断，但 `_build_fork_lines` 会滤掉全部 `isSidechain` 条目、并给每条消息重新分配 uuid——subagent 子时间线整段丢失，同一条历史消息在原会话与分支里也不再同名。`rewind_files` 只回退文件、明确不回退对话（官方文档原话 "It does not rewind the conversation itself"）。Claude Code CLI 的 `/rewind` 能回退对话，但那是 CLI 在应用层截断重放自己 transcript 的内部功能，未经 SDK 下放。

决定在 ArcReel 应用层自实现**前缀分叉**：把改写点之前的 transcript 前缀（含 subagent 子路径）从 DB 镜像（`agent_session_entries`）复制到新 session_id 下，以 `resume=新id` 启动新 SessionActor，改写后的消息作为新会话首个输入。这与 CLI 内部实现 `/rewind` 的思路同构——在自己持有的 transcript 存储上操作，区别只是 CLI 截 jsonl、我们复制 DB 行。

关键约束与风险收敛：

- **分叉点固定在用户消息边界**。前一轮次必然完整收尾，tool_use/tool_result 配对与 sidechain 完整性由此保证；会话存在未决问答卡片时禁止改写，恰好避开前一轮次存在悬空 tool_use 的时刻。
- **封装为单一服务入口**。「以拼装出的前缀供 SDK resume」是非官方用法，风险与知识都收敛在这一处，调用方不感知前缀是怎么拼出来的。
- **条目 uuid 随前缀原样复制**，不重映射。唯一索引与物化目录都以 session_id 分域，跨会话同 uuid 不照面；同一条历史消息在所有分支里保持同名，跨分支对齐与分支的分支因此都不需要额外的身份簿记。

## 锚点身份

改写锚点由前端给出的是事件日志条目的 uuid，而截断要落在 transcript 条目上，两者分属两个 uuid 域。绝大多数条目上这两个域同名：懒生成时用户条目直接取 transcript 条目的 uuid，前缀复制又保留 uuid，因此恒等关系在分支的分支上递归成立。唯一的例外是活跃路径——POST 受理时就要给出条目身份，那时 SDK 还没分配 uuid，只能先 mint 一个 `user-<hex>`，事后由回显认领配对落进映射表 `agent_session_user_message_links`。

解析因此分两段，收敛在 `EventLogService.resolve_user_message_anchor` 一处：映射表优先，未命中时校验该 uuid 在事件日志里对应的是主线 plain user 条目（无 subtype、无 parent_tool_use_id），通过即按恒等性采用。回退不校验 transcript 存在性——映射缺失的活跃路径条目会走到这里并返回一个 transcript 查无此条的 uuid，由前缀分叉在切片时拒绝。同理，transcript 把工具回执也写成 `type:"user"` 条目，混排文本的那种从条目类型上与真用户消息无从区分，由前缀分叉按「载有 tool_result」拒绝——否则配对的 tool_use 会悬在前缀末尾。两道校验分工明确，两条解析路径共同受益。

## 明确不采用

- **SDK 原生 `fork_session` + prompt 覆盖**（唯一的官方姿势）：即使用 `up_to_message_id` 截断，丢 sidechain 与重映射 uuid 两条也已否掉它——前者让分支失去 subagent 历史，后者让跨分支对齐无从谈起；而不带截断的整史分叉更是让被否定的错误分支继续在上下文里吃 token、污染后续生成，纠偏效果退化为追加指令。
- **原地截断原会话**：删除编辑点之后的 transcript 行与事件日志行、同 session_id resume。破坏事件日志的 append-only 根基（`docs/adr/0048`），前端增量投影器与 SSE `Last-Event-ID` 续传语义连带失效，且被弃分支的备份要另行实现。前缀分叉下这些问题不存在：原会话数据整体不动即是备份，新会话事件日志按 `docs/adr/0048` 既有的重放重建机制从 transcript 懒生成。
- **等待上游实现**：中间点分叉在上游长期处于 feature request 状态，无时间表；纠偏能力是弱模型场景的现实痛点，不适合无限期挂起。

## 发布靠补偿，不靠事务

一次分叉要落三样东西：新会话的 transcript 前缀、新会话的元数据行、原会话的 superseded 指针。它们跨 transcript 镜像与会话元数据两处存储，各自提交，没有共享的事务边界——store 的每次 append 就是一次提交，且 transcript 行不依赖元数据行就能被会话枚举看见。因此发布不是原子的，中途失败由服务整体撤回：清指针、删新会话 transcript（连子代理子路径）、删新会话元数据行，三步各自容错，清理自身的失败只记日志、不掩盖原始错误。把 `create` 与 `mark_superseded` 收进同一个事务不改变这个性质，只是把窗口挪到 transcript 一侧。

可容忍的窗口边界：撤回之前的那一瞬，新会话行可能已经存在而指针尚未落定。此时它是一个空转的会话——没有 actor、没有输入。要让它造成实际损害，需要「会话列表恰好轮询到这一瞬」叠加「用户当场删除这个刚看见的会话」，与仓库既有的并发取舍同族（单用户本地部署）。指针本身则用条件更新守护：`WHERE superseded_by IS NULL` 保证同一会话不会被两次分叉各写一次，重复分叉在这一步失败并触发整体撤回。

## Consequences

- 原会话标记 superseded 并记录指向新会话的指针，会话列表过滤隐藏，数据完整保留；闲置驱逐照常。删除链中某个分支时，指向它的前身改指它自己的后继，前身不会带着一个指向不存在会话的指针永久留在列表之外。
- `superseded_by` 表达的是替换式 UX——一个会话只呈现一条时间线，被取代者退场。分叉血统另存 `fork_parent_session_id` + `fork_anchor_uuid`（transcript 域），在 branch 时刻写入即不再变化：前者是可被删除接手改写的呈现投影，后者是不可变的事实。将来若要把分支呈现成树，放开单子约束加表现层工作即可，数据层不必迁移。
- 改写不回退文件与项目数据副作用。SDK 的 `enable_file_checkpointing` + `rewind_files` 可作后续增强，但其盲区（Bash 写入不追踪、项目数据经任务队列/DB 落盘）决定了它最多是部分回滚，不改变本决策。
- 事件日志与前端投影契约零改动：截断语义完全由「新会话」表达，append-only、前缀不变、SSE 续传三个既有约定原样成立。
