# Agent 记忆的官方定义、内容边界与写入最佳实践

> 用途：为 ArcReel 内嵌 Agent 的「Agent 记忆」Spec（地图 https://github.com/ArcReel/ArcReel/issues/2306 ）提供术语定义、内容边界、写入措辞与用户交互先例的一手依据。
> 对应议题：https://github.com/ArcReel/ArcReel/issues/2308
> 范围：只采信 Anthropic 一手来源——Claude Code 文档（code.claude.com）、Claude 平台文档（platform.claude.com）、Agent SDK 文档、Anthropic 工程博客与研究文章、Claude 帮助中心（support.claude.com）、Claude 官方博客（claude.com/blog，原 anthropic.com/news 已 308 重定向至此）、Claude Code CHANGELOG 与 npm 发布时间。第三方转录的系统提示、Cookbook 之外的社区文章一律不采。
> 调研日期：2026-09-03。文档页面本身多数不显示日期，时效锚点用页面提及的最高版本号或 CHANGELOG 条目（版本日期取自 npm registry `@anthropic-ai/claude-code` 的 `time` 字段）。旧于 2026 年的材料在正文标注日期。
> 结论标记：凡官方没有明说的，一律写「未核实」，不以推断顶替。

---

## 一、结论速览

| 票中问题 | 一句话结论 |
| --- | --- |
| 1 定义与分类 | Anthropic 把跨会话知识分成「人写的指令（CLAUDE.md）」与「Claude 自写的笔记（auto memory）」两套互补机制；memory tool 是平台层由客户端持久化的同类文件式记忆；transcript 只是会话历史，官方明确它与记忆职责不同、保留策略也不同。 |
| 2 记什么、不记什么 | auto memory 只记四类（user / feedback / project / reference），跳过能从代码推出的内容与 CLAUDE.md 已写的内容；结构是 `MEMORY.md` 索引（每条一行，启动时只载前 200 行或 25KB）+ 按需读取的主题文件。 |
| 3 写入触发与措辞 | Claude Code 主会话的 auto memory 系统提示官方未公开；可对齐的公开措辞有三处：memory tool 由 API 自动注入的「MEMORY PROTOCOL / ASSUME INTERRUPTION」、文档给出的「keep it coherent and organized / Only write down information relevant to <topic>」强化句、以及超限时回给模型的索引压缩错误文本。 |
| 4 用户可见可编辑先例 | Claude Code：记忆是纯 Markdown，用户随时可编辑删除，`/memory` 提供入口与开关；官方明说「你可以编辑或删除，但 Claude 会继续更新它」。Claude.ai：2026-08-25 起记忆是逐条 Topics，设置页可读、改、删，聊天中可说「记住 / 改 / 忘记」，下次对话生效。两边都没有「Claude 不会覆盖用户改动」的承诺。 |

---

## 二、问题 1：官方定义与分类

### 2.1 Claude Code：两套互补机制

Claude Code 文档 memory 页开门见山定义了跨会话知识的两种载体（https://code.claude.com/docs/en/memory ，页面提及 v2.1.239，访问 2026-09-03）：

> Each Claude Code session begins with a fresh context window. Two mechanisms carry knowledge across sessions: **CLAUDE.md files**: instructions you write to give Claude persistent context · **Auto memory**: notes Claude writes itself based on your corrections and preferences

> Claude Code has two complementary memory systems. Both are loaded at the start of every conversation. Claude treats them as context, not enforced configuration.

对照表原文（同页）：

| 维度 | CLAUDE.md | Auto memory |
| --- | --- | --- |
| Who writes it | You | Claude |
| What it contains | Instructions and rules | Learnings and patterns |
| Scope | Project, user, or org | Per repository, shared across worktrees |
| Loaded into | Every session | Every session (first 200 lines or 25KB) |
| Use for | Coding standards, workflows, project architecture | Your preferences, corrections you give Claude, project context Claude can't derive from the code |

**CLAUDE.md 四个作用域**（同页，按加载顺序从宽到窄）：Managed policy（组织级，IT 部署）→ User（`~/.claude/CLAUDE.md`，个人跨项目）→ Project（`./CLAUDE.md` 或 `./.claude/CLAUDE.md`，随源码共享给团队）→ Local（`./CLAUDE.local.md`，个人本项目，加入 `.gitignore`）。所有文件拼接进上下文而非互相覆盖；注入形态是「system prompt 之后的一条用户消息」（"CLAUDE.md content is delivered as a user message after the system prompt, not as part of the system prompt itself."）。

**Auto memory 的位置与结构**（同页）：

> Each project gets its own memory directory at `~/.claude/projects/<project>/memory/`. The `<project>` path is derived from the git repository, so all worktrees and subdirectories within the same repo share one auto memory directory. Outside a git repo, the project root is used instead.

> The directory contains a `MEMORY.md` index and one topic file per memory

> `MEMORY.md` acts as an index of the memory directory. Claude reads and writes files in this directory throughout your session, using `MEMORY.md` to keep track of what's stored where.

> Auto memory is machine-local. … Files are not shared across machines or cloud environments.

位置可改：`autoMemoryDirectory` 设置「is read from any settings scope: user, project, local, policy, or `--settings`」，值须为绝对路径或以 `~/` 开头（CHANGELOG 2.1.74，npm 2026-03-11）；`CLAUDE_CODE_PROJECT_DIR_NAME` 配合 `CLAUDE_CONFIG_DIR` 可指定 `<project>` 目录名（v2.1.234+）。开关：`autoMemoryEnabled`（任意 settings 作用域）或环境变量 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`；`--bare` 启动也整体关闭（CHANGELOG 2.1.81，npm 2026-03-20）。

**在 Agent SDK 下**（https://code.claude.com/docs/en/agent-sdk/claude-code-features ，「What settingSources does not control」表）：

> Auto memory at `~/.claude/projects/<project>/memory/` — Loaded into the system prompt at session start. The agent writes new memories there with the standard `Write` and `Edit` tools rather than a dedicated memory tool, so those tools must be enabled for the agent to save memories — To disable: Set `autoMemoryEnabled: false` in settings, or `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env`

> Do not rely on default `query()` options for multi-tenant isolation. Because the inputs above are read regardless of `settingSources`, an SDK process can pick up host-level configuration and per-directory memory. For multi-tenant deployments, run each tenant in its own filesystem and set `settingSources: []` plus `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env`.

即 auto memory 在 SDK 下默认启用、不受 `setting_sources` 控制、写入依赖 `Write` / `Edit` 工具。SDK overview 也把它列为能力（"Skills, commands, and memory — Load automatically from your project's `.claude/` and from `~/.claude/`, same as Claude Code"，https://code.claude.com/docs/en/agent-sdk/overview ）。

**子智能体记忆**是 auto memory 的一部分：`AgentDefinition.memory: 'user' | 'project' | 'local'`，落 `~/.claude/agent-memory/<name>/`、`.claude/agent-memory/<name>/`、`.claude/agent-memory-local/<name>/`；"if you turn auto memory off … the `memory` field has no effect"（https://code.claude.com/docs/en/sub-agents ）。主会话的 auto memory 不注入子智能体（"The main conversation's auto memory isn't loaded into subagents; the exception is a fork"，memory 页）。

**压缩后**：auto memory 与项目根 CLAUDE.md 一样「Re-injected from disk」（https://code.claude.com/docs/en/context-window#what-survives-compaction ）。

### 2.2 平台层：memory tool

定义（https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool ，页面无日期，示例模型为 `claude-opus-5`，属 2026 现行版）：

> The memory tool lets Claude store and retrieve information across conversations in a directory of memory files. Claude can create, read, update, and delete files that persist between sessions, building up knowledge over time without keeping everything in the context window.

> Memory supports just-in-time context retrieval. Rather than loading all relevant information up front, an agent records what it learns in memory files and reads them back on demand.

> The memory tool operates client-side: Claude requests file operations, and your application executes them. You control where and how the data is stored through your own infrastructure.

要点：tool 定义 `{"type": "memory_20250818", "name": "memory"}`；命令 `view / create / str_replace / insert / delete / rename`；约定根目录 `/memories`（"The `/memories` path is a prefix that your handler maps onto real storage"）；现行文档写明 memory tool 本身不再需要 beta header（"the memory tool itself doesn't require a beta header"），与 context editing 搭配时用 `anthropic-beta: context-management-2025-06-27`；支持「all Claude 4 and later models」。首发是 2025-09-29 随 Sonnet 4.5 的 public beta（https://claude.com/blog/context-management ）。

与 Claude Code auto memory 的关系：两者同为「文件式、按需读取」的记忆，差别在谁执行文件操作——auto memory 由 Claude Code 用标准 `Write` / `Edit` 落在本机目录；memory tool 由你的应用执行并决定存储。Cookbook 2026-03-20 的总述："Claude Code employs multiple of these strategies in production: compaction for long conversations and two complementary memory systems for cross-session persistence. Our API offers first-party implementations of all three: server-side compaction, context editing (which includes tool-result clearing), and the memory tool."（https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools ）

### 2.3 会话 transcript 与记忆的边界

Agent SDK sessions 页（https://code.claude.com/docs/en/agent-sdk/sessions ）：

> A session is the conversation history the SDK accumulates while your agent works. It contains your prompt, every tool call the agent made, every tool result, and every response. The SDK writes it to disk automatically so you can return to it later.

> Sessions persist the **conversation**, not the filesystem.

hosting 页把三类本地状态分列：Session transcripts（`~/.claude/projects/`）、`CLAUDE.md` memory files、Working-directory artifacts，并明说 "**Transcripts only**: `SessionStore` mirrors transcripts, not `CLAUDE.md` memory files or other working-directory artifacts."（https://code.claude.com/docs/en/agent-sdk/hosting ）。保留策略也不同："Claude Code deletes old session transcripts after the `cleanupPeriodDays` retention period, but excludes the files in the memory directory from that retention sweep. `MEMORY.md` and topic files stay until you or Claude edits or deletes them."（memory 页；v2.1.228 前有 bug 会误删，https://code.claude.com/docs/en/claude-directory ）。

### 2.4 工程博客的分类：记忆是三种长时程策略之一

《Effective context engineering for AI agents》（https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents ，2025-09-29）：

> To enable agents to work effectively across extended time horizons, we've developed a few techniques that address these context pollution constraints directly: compaction, structured note-taking, and multi-agent architectures.

> Structured note-taking, or agentic memory, is a technique where the agent regularly writes notes persisted to memory outside of the context window. These notes get pulled back into the context window at later times.

> Compaction maintains conversational flow for tasks requiring extensive back-and-forth; Note-taking excels for iterative development with clear milestones; Multi-agent architectures handle complex research and analysis where parallel exploration pays dividends.

《Building effective agents》（2024-12-19）只把 memory 列为 LLM 的增强件之一（"an LLM enhanced with augmentations such as retrieval, tools, and memory"），无专门定义。2026 年 anthropic.com 上未见专门讨论 memory 的新工程文章（限定域名两次搜索未命中）；相关的是《Long-running Claude for scientific computing》（https://www.anthropic.com/research/long-running-Claude ，2026-03-23）把进度文件称为 "the agent's portable long-term memory, acting as a sort of lab notes"。

### 2.5 Claude.ai：产品记忆

帮助中心（https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context ，Intercom 相对时间 "Updated today"）：

> Claude saves memory as a set of individual topics as you chat, rather than summarizing conversations after they end.

> Each project has its own separate memory space and dedicated project summary, so the context within each of your projects is focused, relevant, and separate from other projects or non-project chats.

它与「搜索过往对话」是两个独立开关（"Generate memory from chats" 与 "Search and reference chats"）；后者是 RAG 工具调用。旧版（2025-09 至 2026-07）是每 24 小时合成一份 "memory summary"，2026-07-10 起改为逐条 Topics（https://support.claude.com/en/articles/12138966-release-notes ）。Claude Code 记忆与 Claude.ai 记忆是两套独立系统，帮助中心分列且未提打通（https://support.claude.com/en/articles/14554000-claude-code-power-user-tips ："This is separate from your user-level ~/.claude/CLAUDE.md and project-level ./CLAUDE.md files, which you maintain by hand."）。

---

## 三、问题 2：记什么、不记什么；渐进式披露与容量

### 3.1 Auto memory 只记四类

memory 页原文：

> As it works, Claude saves four kinds of notes for itself. Claude records the kind as a `type` field in the memory file's frontmatter:
> · `user`: your role, expertise, and working preferences
> · `feedback`: corrections you give Claude and approaches you confirm
> · `project`: ongoing work, deadlines, and decisions that Claude can't derive from the code or git history
> · `reference`: where to find information outside the project, such as an issue tracker or dashboard

> Claude skips anything it can derive from the codebase, such as architecture, file paths, or debugging fixes. It also skips anything your CLAUDE.md files already say.

> Claude doesn't save something every session. It decides what's worth remembering based on whether the information would be useful in a future conversation.

对票中「偏好 vs 事实 vs 进度 vs 一次性指令」四分法的映射：

| 票中类别 | 官方口径 |
| --- | --- |
| 偏好 | 记：`user`（角色、专长、工作偏好）与 `feedback`（纠正与被确认的做法） |
| 事实 | 只记 Agent 推不出的：`project`（决策、期限）与 `reference`（外部线索）；能从代码 / git 推出的、CLAUDE.md 已写的一律跳过 |
| 进度 | `project` 类允许记 "ongoing work"，但官方例子是期限与决策；Claude Code 未把制作进度式内容单列。ArcReel 已决定进度由服务端步骤视图承载，不记入记忆 |
| 一次性指令 | 官方无此措辞；判据是「未来会话是否有用」，只对本次操作成立的要求不满足此判据 |

用户口头要求「记住 X」走 auto memory；要写进 CLAUDE.md 需明说：

> When you ask Claude to remember something, like "always use pnpm, not npm" or "remember that the API tests require a local Redis instance," Claude saves it to auto memory. To add instructions to CLAUDE.md instead, ask Claude directly, like "add this to CLAUDE.md," or edit the file yourself via `/memory`.

注意 claude-directory 页有一处旧口径（"Claude saves notes as it works: build commands, debugging insights, architecture notes."），与 memory 页「跳过 architecture / debugging fixes」冲突；两处都是官方原文，本笔记以 memory 页为准并如实记录差异。

### 3.2 CLAUDE.md（指令）该写什么

memory 页："Keep it to facts Claude should hold in every session: build commands, conventions, project layout, "always do X" rules. If an entry is a multi-step procedure or only matters for one part of the codebase, move it to a skill or a path-scoped rule instead."；何时加入："Claude makes the same mistake a second time / A code review catches something Claude should have known about this codebase / You type the same correction or clarification into chat that you typed last session / A new teammate would need the same context to be productive"。

best-practices 页（https://code.claude.com/docs/en/best-practices ，原 anthropic.com/engineering/claude-code-best-practices 已 308 重定向）："Keep it concise. For each line, ask: *"Would removing this cause Claude to make mistakes?"* If not, cut it. Bloated CLAUDE.md files cause Claude to ignore your actual instructions!"；Exclude 列包括 "Anything Claude can figure out by reading code / … / Information that changes frequently / Long explanations or tutorials"。`#` 快捷键已在 CHANGELOG 2.0.70 移除（"Removed # shortcut for quick memory entry (tell Claude to edit your CLAUDE.md instead)"）。

### 3.3 渐进式披露的推荐结构与容量约束

结构：索引 + 主题文件，索引每条一行，主题文件按需读。

> The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes first, are loaded at the start of every conversation. Content beyond that threshold is not loaded at session start. Claude keeps `MEMORY.md` concise by moving detailed notes into separate topic files.

> This limit applies only to `MEMORY.md`. … Claude Code doesn't load topic files such as `user_role.md` or `feedback_testing.md` at startup. Claude reads them on demand using its standard file tools when it needs the information.

主题文件带 frontmatter（`name` / `description` / `type`，Claude Code v2.1.214+ 自动补 ISO `modified` 时间戳，"Claude Code never adds frontmatter to a file that has none"）。claude-directory 页示例：

```
---
name: Debugging patterns
description: Auth token rotation and database connection troubleshooting for this project
type: reference
---
```

容量约束的执行方式（memory 页；CHANGELOG 2.1.83 加 25KB、2.1.186 加临近提醒、2.1.210 加超限错误、2.1.211 只计实际加载内容）：

> After Claude writes to `MEMORY.md`, Claude Code measures the file against the 200-line and 25KB read limits. If the file is near a limit, Claude Code reminds Claude to shorten it: keep one line per entry, move detail into topic files, and merge or drop stale entries. If the file is over a limit, the write still succeeds, but Claude Code returns an error telling Claude to rewrite the index, because everything past the limit is dropped on the next load.

CLAUDE.md 侧的规模建议：单文件 "target under 200 lines"，超过 4 MiB 整体跳过；`/doctor`（v2.1.206+）会建议裁掉「能从代码推出的」内容。

memory tool 侧的对应建议：`view` 对超过 16,000 字符的文件截断，"Consider capping how many characters the `view` command returns, and let Claude page through the rest with `view_range`."；"Track memory file sizes and cap how large a file can grow." / "Periodically delete memory files that haven't been accessed in a long time."（memory-tool 页）。Cookbook（https://platform.claude.com/cookbook/tool-use-memory-cookbook ，页面标注 2025-05-22 但标题提及 Sonnet 4.6，日期存疑）的清单："Store task-relevant patterns, not conversation history / Organize with clear directory structure / Use descriptive file names / Periodically review and clean up memory"；反面 "Store sensitive information (passwords, API keys, PII) / Let memory grow unbounded / Store everything indiscriminately"。

### 3.4 Claude.ai 的内容边界（作为「不记什么」的产品口径）

记（11817273）："Your role, projects, and professional context / The people and places in your work and life / Communication preferences and working style / Technical preferences and coding style / Project details and ongoing work"。

默认不记（2026-08-25，https://claude.com/blog/claudes-memory-works-everywhere-and-you-decide-whats-in-it ）："By default, Claude does not store topics related to personal or sensitive subject matter, like your health, race, ethnicity, religious beliefs, politics, gender identity, and other similar areas."；用户可开 "Include sensitive topics in memory"，每次保存此类内容时输入框上方出现提示。

永不记（同上）："Some information is never saved to memory, even if you ask. This includes government ID numbers, criminal history, financial account numbers, and immigration status. Claude will let you know when it can't save something for this reason."

「一次性请求不记」：Claude.ai 官方无此表述，未核实。

---

## 四、问题 3：写入触发时机与措辞

### 4.1 Claude Code 主会话的 auto memory 系统提示：未公开

> Claude Code's system prompt isn't published. To give Claude standing instructions, use `CLAUDE.md` files or the `--append-system-prompt` flag.（https://code.claude.com/docs/en/settings#system-prompt ）

官方只描述效果：界面出现 "Saved 2 memories" / "Recalled 2 memories" 表示 Claude 正在读写 `~/.claude/projects/<project>/memory/`（memory 页）；子智能体的系统提示「includes instructions for reading and writing to the memory directory … with instructions to curate `MEMORY.md` if it exceeds that limit」（sub-agents 页），原文同样未公开。网络上的第三方转录不属一手来源，本笔记不采。

CHANGELOG 时间线（npm 日期）：2.1.32（2026-02-05）"Claude now automatically records and recalls memories as it works" 首次出现；2.1.59（2026-02-25）"Claude automatically saves useful context to auto-memory. Manage with /memory"；2.1.63（2026-02-28）跨 worktree 共享；2.1.75（2026-03-13）加 last-modified 帮助区分新旧；2.1.186（2026-06-22）临近上限时提醒压缩索引；2.1.210（2026-07-14）超限改为显式错误。

### 4.2 官方可见的、发给模型的措辞（三处）

**（a）memory tool 由 API 自动注入的协议**（memory-tool 页，"When the memory tool is present in your request's `tools`, the API automatically adds this instruction to the system prompt. You don't need to send it yourself"）：

```
IMPORTANT: ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE.
MEMORY PROTOCOL:
1. Use the `view` command of your `memory` tool to check for earlier progress.
2. ... (work on the task) ...
   - As you make progress, record status / progress / thoughts etc in your memory.
ASSUME INTERRUPTION: Your context window might be reset at any moment, so you risk losing any progress that is not recorded in your memory directory.
```

**（b）文档给出的可选强化句**（同页）：

> Note: when editing your memory folder, always try to keep its content up-to-date, coherent and organized. You can rename or delete files that are no longer relevant. Do not create new files unless necessary.

> You can also guide what Claude writes to memory. For example: "Only write down information relevant to <topic> in your memory system."

前一句官方说明 "Claude's tool description already tells it to keep the memory directory organized, so you don't need to repeat that instruction"，只在仍然杂乱时加。

**（c）Claude Code 超限时回给模型的错误文本**（https://code.claude.com/docs/en/errors#memory-index-is-over-its-read-limit ；"Claude Code delivers the error to Claude after the write rather than printing it as a banner in your terminal"）：

```
Error: this write left the memory index at MEMORY.md at 214 lines, over its 200-line read limit. The write succeeded, but everything past the limit is silently dropped each time the index is loaded — entries at the end are already invisible to readers. Rewrite it to under 140 lines now: keep one line per entry, move detail into topic files, and merge or drop stale entries.
```

临近上限时的「milder reminder」原文未公开。

### 4.3 触发时机的官方模式

- **随做随记，假设随时中断**：memory tool 协议要求「As you make progress, record」，且「ASSUME INTERRUPTION」；context editing 在接近清理阈值时「Claude receives an automatic warning to preserve important information」，让 Claude 先把要保留的内容写入记忆（https://platform.claude.com/docs/en/build-with-claude/context-editing ）。
- **多会话项目按「初始化 / 开场读 / 收尾更新」三段**（memory-tool 页 2026 现行版）："For software projects that span multiple agent sessions, set up memory files deliberately instead of writing them ad hoc as work progresses." → "Initializer session … Subsequent sessions: Each new session opens by reading those memory files. … End-of-session update: Before a session ends, it updates the progress log with what was completed and what remains."（此模式面向进度型记忆，ArcReel 已决定进度不进记忆，仅供对照）
- **Claude Code 的判据**：不是每会话都写，"based on whether the information would be useful in a future conversation"；用户明说「记住」即写。
- **Anthropic SDK 官方示例的 system prompt**（https://github.com/anthropics/anthropic-sdk-python/blob/main/examples/memory/basic.py ，代码非文档）："DO NOT just store the conversation history / No need to mention your memory tool or what you are writing in it to the user, unless they ask / Store facts about the user and their preferences / Before responding, check memory to adjust technical depth and response style appropriately / Keep memories up-to-date - remove outdated info, add new details as you learn them"。

### 4.4 对 ArcReel `CLAUDE.<mode>.md` 写入指引的对齐要点

综合以上官方措辞，一段创作领域指引可以覆盖且只覆盖这些方面（措辞待 Spec 定，此处只列官方有依据的要素）：

1. **判据**：只记「未来会话有用、且 Agent 无法从项目数据 / 指令文件推出」的内容（memory 页）。
2. **记什么**：创作者的偏好与纠正（画幅、配音、风格禁忌等对应官方 `user` / `feedback`）、只在对话中出现的决策（`project`）、外部线索（`reference`）。
3. **不记什么**：制作进度、一次性指令、能从 project.json 等项目数据读出的事实、`CLAUDE.<mode>.md` 已写的规则；敏感个人信息（Claude.ai 与 Cookbook 口径）。
4. **形态**：索引每条一行、细节进主题文件、及时合并过期条目、不必要不建新文件（memory 页 + memory-tool 强化句）。
5. **限定范围**：可用 "Only write down information relevant to <topic>" 句式把范围收到创作领域（memory-tool 页）。
6. **不必重复原生已有的指令**：原生系统提示已含读写与整理指令，指引只补领域内容，避免与 best-practices「过度指定」失败模式相撞。

---

## 五、问题 4：用户可见 / 可编辑记忆的官方先例

### 5.1 Claude Code `/memory`

> The `/memory` command lists your CLAUDE.md, CLAUDE.local.md, and other memory file locations across user and project scopes … It also lets you toggle auto memory on or off and provides an option to open the auto memory folder. Select any file to open it in your editor; selecting one that doesn't exist yet creates it first. To check which files actually loaded into the current session, run `/context`.（memory 页）

> Auto memory files are plain markdown you can edit or delete at any time. Run `/memory` to browse and open memory files from within a session.（同页）

> Run `/memory` and select the auto memory folder to browse what Claude has saved. Everything is plain markdown you can read, edit, or delete.（同页）

交互形态：文件列表 + 外部编辑器打开整份文件（v2.1.216 起不再等编辑器关闭）；「Saved N memories」通知中的文件名可点击打开（CHANGELOG 2.1.86）。无逐条 UI、无逐条确认。

### 5.2 「用户改了 Agent 写的文件」的官方原则

claude-directory 页对 `MEMORY.md` 的描述是唯一直接表态：

> Claude creates and updates this file as it works; you do not write it yourself. It acts as an index that Claude reads at the start of every session, pointing to topic files for detail. You can edit or delete it, but Claude will keep updating it.

主题文件："You never create these yourself. Claude reads a topic file back only when the current task relates to it."

配套事实：用户编辑后 Claude 下次写入会照常补 `modified` 字段（"Any file that has frontmatter gets the field the next time Claude writes it"）；文件保留到「you or Claude edits or deletes them」。**没有**「尊重用户改动、不回滚用户删除的条目」之类的合并保护承诺，未核实。

### 5.3 Claude.ai 的逐条可编辑记忆（2026-08-25 起）

> See exactly what Claude remembers about you in Settings > Memory. Everything Claude remembers is listed under Topics. Select any topic to read it, then use the edit icon to change it or select "Delete" to remove it. Fix something in one topic and the change applies to every conversation from then on.（11817273）

> You can also update memory directly from a chat. Tell Claude what you'd like it to remember, change, or forget, and the update applies to your next conversation.（同上）

设置页另有自然语言修改框 "Tell Claude what to change or remove"（https://support.claude.com/en/articles/12123587-import-and-export-your-memory-from-claude ）。Pause 保留现有记忆但不再读写；Reset 永久删除全部记忆含 project memory、不可撤销。删除对话不会删掉由它产生的记忆条目（新版），但可逐条删。Team / Enterprise 由 Owner 开关，"Owners can't view or edit a user's individual memories"，组织关闭时 "all existing memory entries for all users are deleted immediately"。

同样**没有**「Claude 不覆盖用户编辑」的承诺；release notes 措辞是 "entries that Claude reads and updates during your conversations"（2026-07-10）。

### 5.4 对 ArcReel 的先例映射

| ArcReel 已定决策 | 官方先例 |
| --- | --- |
| 目录文件列表 + Markdown 全文编辑 | Claude Code `/memory`：文件列表 + 打开整份文件编辑 |
| 每级「清空」二次确认 | Claude.ai Reset memory："cannot be undone"，需再次点击确认 |
| Agent 自主写、用户可改、不逐条确认 | 两边一致；两边都接受「用户改了，Claude 之后继续更新」 |
| 用户记忆 / 项目记忆两级 | Claude Code user / project 作用域；Claude.ai 跨对话记忆 / project memory 隔离 |

---

## 六、未能核实的点

- Claude Code 主会话注入的 auto memory 系统提示原文（官方明确不公开）；临近上限的「milder reminder」原文。
- 用户编辑与 Claude 写入冲突时的合并 / 保护原则（Claude Code 与 Claude.ai 均无表述）。
- CHANGELOG 2.1.172 提到的 `CLAUDE_MEMORY_STORES`（团队记忆存储）在文档页无说明。
- memory-tool 文档页、原 claude-code-best-practices 博文的发布日期；Cookbook memory 页标注日期与其内容存在矛盾。
- 「一次性请求不记」在 Claude Code / Claude.ai 均无原话，只能由「未来会话是否有用」判据推出。

## 七、来源清单

Claude Code 文档：memory、best-practices、settings、settings-reference、commands、sub-agents、errors、claude-directory、glossary、context-window（均 https://code.claude.com/docs/en/… ）；CHANGELOG https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md 。
Agent SDK 文档：overview、sessions、modifying-system-prompts、claude-code-features、hosting、subagents（https://code.claude.com/docs/en/agent-sdk/… ）。
平台文档：memory-tool、context-editing、compaction（https://platform.claude.com/docs/en/… ）；Cookbook tool-use-memory-cookbook、tool-use-context-engineering-context-engineering-tools。
工程 / 研究文章：effective-context-engineering-for-ai-agents（2025-09-29）、building-effective-agents（2024-12-19）、effective-harnesses-for-long-running-agents（2025-11-26）、research/long-running-Claude（2026-03-23）。
Claude 产品：https://claude.com/blog/memory （2025-09-11，含 2025-10-23 更新）、https://claude.com/blog/claudes-memory-works-everywhere-and-you-decide-whats-in-it （2026-08-25）、https://claude.com/blog/context-management （2025-09-29）；帮助中心 11817273、12123587、12260368（incognito）、12138966（release notes）、14554000。
代码：https://github.com/anthropics/anthropic-sdk-python/blob/main/examples/memory/basic.py 。

---

## 八、CONTEXT.md 词条草案

拟加入「Agent 运行时」节，位于「SDK transcript」词条之前；「指令」词条现有的「长期偏好由 Agent 记忆承载」保持不变即可指向本词条。只写含义与 Avoid，不写实现。

```markdown
**Agent 记忆（agent memory）**：
Agent 跨会话持久保存、自主写入并可由创作者查看修改的关于创作者与项目的自然语言笔记，只记 Agent 无法从项目数据或指令文件推出、且未来会话仍有用的偏好、纠正、决策与外部线索；分项目记忆与用户记忆两级。
_Avoid_: 把项目正式数据、SDK transcript / 会话事件日志、一次性指令或制作进度当作记忆；把 `CLAUDE.<mode>.md` 这类人写的指令文件叫作记忆。

**项目记忆（project memory）**：
随项目走的 Agent 记忆，只在该项目的会话中生效，承载该项目的风格取向、被确认的做法与创作者对本项目的纠正。
_Avoid_: 跨项目复用；把它写进项目正式数据；与项目设置混为一谈。

**用户记忆（user memory）**：
按用户归属、对该用户所有项目生效的 Agent 记忆，承载创作者的身份背景与长期工作偏好。
_Avoid_: 绑定服务实例或宿主目录；写入只对某一个项目成立的内容。
```
