---
status: accepted
---

# Agent 记忆两级落盘：项目记忆走原生 auto memory 重定向，用户记忆自建于数据根

内嵌创作 Agent 的「Agent 记忆」分项目记忆与用户记忆两级，而 Claude Code 的 auto memory 一个会话只认一个目录，两级必有一级自建。决定：**原生 auto memory 接项目记忆**，目录重定向到 `<项目目录>/.arcreel/memory/`；**用户记忆自建**，落 `<数据根>/.arcreel/users/<user_id>/memory/`，服务端在会话装配时把其 `MEMORY.md` 索引按原生同样的截断规则注入 append prompt，Agent 用 Write/Edit 写入。原生那套派生规则、后台提取与索引超限告警都按「当前工作目录」设计，项目记忆变动最频繁、最受益；用户记忆小而稳定，注入一段索引即可。

**重定向经 `ClaudeAgentOptions.settings` 的 flag settings 层**按会话注入 `autoMemoryDirectory` 绝对路径，不物化任何 settings 文件、不切 `CLAUDE_CONFIG_DIR` 与 HOME。会话 init 消息的 `memory_paths.auto` 与预期不符时记 error 日志、会话照开，不硬失败。

**不放 `.claude/` 下**：项目 `.claude/` 是 profile 物化树，manifest 失配与「恢复内置 profile」都会整树删除，记忆放进去会被静默清空；`.arcreel/` 是既有的 ArcReel 内部状态目录，校验器跳过点目录、归档导出只拷贝可见项，记忆天然不入归档、不报未识别目录。数据根下的 `_` 前缀目录表示跨项目共享素材库，语义不同，故不用 `_users/`。

## 明确不采用

- **原生接用户记忆、项目记忆自建**：对称可行，但原生的后台提取偏向当前工作内容，落到用户级会把项目细节混进跨项目笔记。
- **两级都自建并禁用原生**：连同原生的写入指令与索引告警一起丢掉，缺口最大。
- **物化 `.claude/settings.local.json` 承载 `autoMemoryDirectory`**：路径按项目变化，与 sha256 manifest 的「未改内置文件」判定冲突，且 SDK 会话是否采用项目级值未验证。
- **按用户切 `CLAUDE_CONFIG_DIR`**：连带搬 transcript、`~/.claude.json` 与用户级 settings，影响面远超记忆本身。
- **迁移仓库 slug 下既有的 `~/.claude/projects/<repo>/memory/`**：内容与开发者笔记混杂、无法按项目归属，只是开发机现象。

## Consequences

- `AgentAccessPolicy` 把两级记忆目录列为 Agent 可读可写区：hook 层放行 Read/Write/Edit/Glob/Grep，内核沙箱层对用户记忆目录加 `allowWrite`（项目记忆在 cwd 内本已可写）。用户记忆目录由 policy 从 `projects_root` + `user_id` 纯派生，`user_id` 与 `project_cwd` 同级逐调用传参，policy 仍为进程级单例。
- 子进程 HOME、`CLAUDE_CONFIG_DIR` 与 `claude_projects_dir` 的 tool-results 读例外均不改。
- 子智能体 `memory: "project"` 落 `<cwd>/.claude/agent-memory/`，同样会被 profile reset 清掉；子智能体记忆作用域选型须另行考虑这一点。
