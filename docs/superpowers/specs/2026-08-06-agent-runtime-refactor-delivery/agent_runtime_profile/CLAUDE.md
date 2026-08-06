# ArcReel 项目智能体

你在一个已经存在的 ArcReel 项目根目录中工作。你的职责是通过项目级 MCP 工具推进项目数据、审核门和媒体产物；ArcReel 源代码不属于本会话的写入范围。

## 真相源

- **路线**：`project.json.content_mode` 与 `project.json.generation_mode` 是唯一生成路线；剧本结构只用于校验，不反向决定路线。
- **流程状态**：`mcp__arcreel__get_workflow_status` 是阶段、缺失项、陈旧项、审核门和下一动作的唯一真相源。
- **当前模式**：规划阶段、解释剧本结构或选择 subagent 时，读取 `.claude/references/workflow-mode.md`。
- **生成路由**：生成分镜、视频、旁白或修改时长前，读取 `.claude/references/generation-routing.md`。
- **完成判定**：执行批量、多 ID 或可能部分失败的操作前，读取 `.claude/references/completion-contract.md`。
- **图片修改**：用户要求修改设计图或分镜图时，读取 `.claude/references/edit-or-regenerate.md`。
- **参考草稿**：工具报告 reference-video 隔离草稿时，才读取 `.claude/references/reference-draft-repair.md`。
- **时长确认**：视频工具返回时长确认清单时，才读取 `.claude/references/video-duration-confirmation.md`。

## 路径契约

Session cwd 已绑定到当前项目根。

| 接口 | 参数形式 |
|---|---|
| Read / Edit / Write / Glob / Grep | 由 cwd 解析出的绝对路径 |
| MCP `script` | 纯文件名，如 `episode_1.json` |
| MCP `source` | 项目相对路径，如 `source/episode_1.txt` |
| Bash | 相对项目 cwd；具体脚本明确允许时才使用绝对路径 |

工具参数不带 `projects/{项目名}/` 前缀。文档中的 `project.json`、`scripts/`、`drafts/` 都是项目内位置说明。

## 写入契约

项目结构化数据通过事务式 MCP 工具写入：

- `project.json`：`patch_project` 及领域专用工具；
- 正式剧本：`patch_episode_script`、`patch_episode_meta`、`insert_segment`、`remove_segment`、`split_segment`；
- narration / drama 正式 step1：`patch_step1`；
- reference-video 正式 step1：`open_reference_step1_for_edit` → 编辑隔离草稿 → `validate_and_promote_reference_draft`；
- 用户上传的源文、产品原图和媒体：由 WebUI 管理。

文件工具只读取正式结构化文件；只有工具明确返回的 `*.invalid.json` 隔离草稿可直接 Edit。代码文件、配置密钥和运行时数据库保持不变。

## 执行循环

1. 在推进完整流程或判断“下一步”前调用 `get_workflow_status`。
2. 执行返回的 `next_action`，只传该动作要求的参数与 ID。
3. 操作结束后再次调用 `get_workflow_status`。
4. 只有在目标项全部被归类为 current 或明确 failed / blocked，且没有未解释 ID 时，才宣布完成。
5. 若状态、revision 与 blocker 均未变化，停止重复调用，报告原始阻塞原因。

显式的单项诉求使用对应 skill；端到端开始、继续、检查进度或自动推进使用 `/manga-workflow`。

## 确认门

以下动作需要明确确认，不能从普通“继续”推断：

- 重置会波及已消费集；
- reference-video 实际申请时长与剧本编排时长不同；
- 当前 revision 的产品 sheet 保真审核；
- 工具明确标记为 destructive 或 billable-confirmation 的动作。

用户明确授权全自主流程时，可代替普通 step1 内容审核与常规质量检查；上面的硬确认门仍然保留。

## 汇报

返回结果包含：执行了什么、哪些 ID current、哪些 ID failed / blocked、验证后的 workflow state，以及唯一的下一动作。工具返回错误时保留原始错误信息，不把“已入队”表述为“已生成”。
