# Claude Code auto memory 在 Agent SDK 下的机制与配置面

> 状态：调研完成，结论供地图 [#2306](https://github.com/ArcReel/ArcReel/issues/2306) 汇总；实施票 #2309 / #2310 据此展开。
> 关联：[#2307](https://github.com/ArcReel/ArcReel/issues/2307)（本票）。
> 版本基线：Python `claude-agent-sdk` 0.2.139，捆绑 CLI 2.1.233（`.venv/lib/python3.12/site-packages/claude_agent_sdk/_cli_version.py`）。文档引用注明的版本号以 Claude Code CHANGELOG 为准；CLI 源码引用来自捆绑二进制 `claude_agent_sdk/_bundled/claude` 的 `strings` 输出（bun 打包的 Mach-O，标识符已压缩，下文用 `fn:<压缩名>` 标注以便复查）。

## 背景

ArcReel 的内嵌创作 Agent 通过 `server/agent_runtime/options_assembler.py` 以 `SystemPromptPreset(preset="claude_code")` + `setting_sources=["project"]` 启动 SDK 会话，cwd 为 `app_data_dir()/<project_name>`，默认即仓库内 `<repo>/projects/<project_name>`（`lib/app_data_dir.py`）。本调研回答 auto memory 在这一配置下是否生效、落在哪里、能否重定向，以及子智能体 memory 的语义。

## 结论速览

| 问题 | 一行结论 |
|---|---|
| 1 默认启用？ | 是。SDK / `-p` 模式不禁用；受 `autoMemoryEnabled`、`CLAUDE_CODE_DISABLE_AUTO_MEMORY`、`--bare`/`CLAUDE_CODE_SIMPLE`、安全模式、`/pause-memory` 控制；`exclude_dynamic_sections` 只搬位置不关闭。 |
| 2 落盘 | `<配置目录>/projects/<slug(git 仓库根 ?? cwd)>/memory/`，索引 `MEMORY.md` 截断 200 行或 25 000 字节；主题文件带 frontmatter；指令原文见 §2.4。 |
| 3 重定向 | 可以：`autoMemoryDirectory`（绝对路径或 `~/`，policy > `--settings` > local/project（受信任或非交互时） > user）；`CLAUDE_CONFIG_DIR` 整体搬家（连带 transcript / settings）；`HOME` 无文档支持；`CLAUDE_CODE_PROJECT_DIR_NAME` 需 CLI ≥ 2.1.234，本机不支持。 |
| 4 子智能体 memory | `user` → `<配置目录>/agent-memory/<name>/`；`project` → `<cwd>/.claude/agent-memory/<name>/`；`local` → `<cwd>/.claude/agent-memory-local/<name>/`；依附主开关，不受 `autoMemoryDirectory` 影响。 |
| 5 主 Agent 作用域切换 | 没有。主 Agent 只有一种派生规则（HOME/配置目录 + git 根 slug）；唯一"项目内"手段是 `autoMemoryDirectory` 指到绝对路径。 |

## 1. 是否默认启用、由哪些开关控制

### 1.1 官方文档

- "Auto memory is on by default." —— [memory#enable-or-disable-auto-memory](https://code.claude.com/docs/en/memory#enable-or-disable-auto-memory)
- settings 键 `autoMemoryEnabled`（Boolean，默认 `true`，任意 settings 文件均可）。`false` 时 "Claude doesn't read from or write to the auto memory directory"。—— [settings-reference#automemoryenabled](https://code.claude.com/docs/en/settings-reference#automemoryenabled)
- 环境变量 `CLAUDE_CODE_DISABLE_AUTO_MEMORY`：`1` 禁用；`0` 强制开启，即便 `--bare` 或 `autoMemoryEnabled: false`。"When disabled, Claude does not create or load auto memory files." —— [env-vars](https://code.claude.com/docs/en/env-vars)
- `--bare` / `CLAUDE_CODE_SIMPLE=1`（v2.1.81 引入）：CHANGELOG 原文 "auto-memory fully disabled"。—— [cli-reference](https://code.claude.com/docs/en/cli-reference)、[CHANGELOG 2.1.81](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md)
- SDK 模式**不禁用**：auto memory "Loaded into the system prompt at session start"，列在 "What settingSources does not control" 表中，"read regardless of its value"；禁用方法是 `autoMemoryEnabled: false` 或 `env` 里 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`。—— [agent-sdk/claude-code-features#what-settingsources-does-not-control](https://code.claude.com/docs/en/agent-sdk/claude-code-features#what-settingsources-does-not-control)
- `-p` 同理："Without it [`--bare`], `claude -p` loads the same context an interactive session would, including anything configured in the working directory or `~/.claude`." —— [headless](https://code.claude.com/docs/en/headless)
- SDK 下 Agent 用普通 `Write`/`Edit` 写记忆，没有专用 memory 工具，"so those tools must be enabled for the agent to save memories"。—— claude-code-features 同页
- `exclude_dynamic_sections` **不能排除** memory，只是把它挪到首条用户消息："the working directory, the git-repo flag, the platform, the active shell, the OS version, and auto memory paths still reach Claude, but as part of the first user message rather than the system prompt."；仅对 `claude_code` preset 生效。Python SDK 0.1.57 加入（SDK CHANGELOG #797），CLI 对应 `--exclude-dynamic-system-prompt-sections` 于 v2.1.98 加入 print mode。—— [agent-sdk/modifying-system-prompts](https://code.claude.com/docs/en/agent-sdk/modifying-system-prompts#improve-prompt-caching-across-users-and-machines)
- 多租户告警："Do not rely on default `query()` options for multi-tenant isolation... set `settingSources: []` plus `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env`." —— claude-code-features、[agent-sdk/hosting](https://code.claude.com/docs/en/agent-sdk/hosting)

### 1.2 CLI 2.1.233 源码（捆绑二进制）

总开关 `fn:Ig()`，按顺序短路：

```js
function Ig(){
  if(P3())return!1;                       // 会话内 /pause-memory 标记
  if(dd())return!1;                       // CLAUDE_CODE_SAFE_MODE / --safe-mode
  let e=process.env.CLAUDE_CODE_DISABLE_AUTO_MEMORY;
  if(Ln(e))return!1;                      // "1"/"true"/"yes"/"on" → 关
  if(af(e))return!0;                      // "0"/"false"/"no"/"off" → 强制开
  if(V.CLAUDE_CODE_SIMPLE)return!1;       // --bare
  if(V.CLAUDE_CODE_REMOTE&&!process.env.CLAUDE_CODE_REMOTE_MEMORY_DIR&&!V.CLAUDE_COWORK_MEMORY_PATH_OVERRIDE)return!1;
  if(Kvn())return!1;                      // GrowthBook 按模型/组织的远程灰度关闭
  let t=Wo();                             // 合并后的 settings
  if(t.autoMemoryEnabled!==void 0)return t.autoMemoryEnabled;
  return!0}
```

settings schema 描述原文：`autoMemoryEnabled` "Enable auto-memory for this project. When false, Claude will not read from or write to the auto-memory directory."

系统提示词装配 `fn:lW`：`claude_code` preset 下 memory 段以 `xF("memory", twn(...))` 进入系统提示词；`excludeDynamicSections` 为真时改为系统提示词里放静态版 `fn:Qad`（"The directory path is provided in your session context."），目录路径由 `fn:Cvi → eld` 生成 `# auto memory\nMemory directory: \`<dir>\`` 段并注入首条用户消息。传入自定义字符串 `system_prompt` 时 `lW` 根本不被调用，只有内部环境变量 `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` 存在时才追加 memory 段（`fn:twn` 调用点 `m=o!==void 0&&Jvn()?await twn(r):null`）。

print / SDK 模式没有单独的禁用分支：`fn:eBe()`（`if(xn())return!0` ，`xn()` 即"非交互"）反而让非交互模式视同已信任工作区。

### 1.3 本机验证

最小探针（`uv run python`，`query()` + `SystemPromptPreset(preset="claude_code")` + `setting_sources=["project"]`，`max_turns=1`）在两种 cwd 下各跑一次，结果：

| cwd | init 消息 `memory_paths.auto` | 启动后新建目录 |
|---|---|---|
| 非 git 目录 `.../sdkcheck/nogit` | `~/.claude/projects/-…-sdkcheck-nogit/memory/` | 同名目录 + 空 `memory/` + transcript `.jsonl` |
| git 仓库子目录 `.../sdkcheck/gitrepo/sub` | `~/.claude/projects/-…-sdkcheck-gitrepo/memory/`（仓库根 slug） | `-…-gitrepo/memory/`（空）与 `-…-gitrepo-sub/<id>.jsonl`（transcript 按 cwd） |

结论：SDK 模式下 auto memory **启用**，init 系统消息含 `memory_paths`（CLI 2.1.233 schema：`memory_paths:{auto?:string,team?:string}`，标注 `@internal`），空 `memory/` 目录在启动时即创建（`fn:jIe` ensureMemoryDirExists）。

**为何本机没有 `-Users-<user>-MyProjects-ArcReel-projects-*/memory`**：memory 目录用 `Lu(cwd) ?? cwd` 派生，`Lu` 是 git 仓库根解析（`fn:Lu → Zc → rootByPath`）。ArcReel 项目 cwd `<repo>/projects/<name>` 位于 ArcReel 仓库内（`projects/` 只是被 gitignore，不是独立 git 仓库），因此 SDK 会话的记忆落到 `~/.claude/projects/-Users-<user>-MyProjects-ArcReel/memory/`，与开发者在仓库里跑交互式 Claude Code 的记忆**同一目录**。这不是禁用、也不是未触发，而是 v2.1.63 起 "auto memory shared across git worktrees of the same repository" 的派生规则把它归并到了仓库根。本机同样不存在 `-…-ArcReel-projects-<name>/` 的 transcript 目录，说明 30 天保留期内没有从该 data dir 启动过 SDK 会话（或 data dir 指向别处）；这与 memory 目录缺失无关。

风险：创作 Agent 与开发者会话共享同一份 `MEMORY.md`，两者互相污染（创作 Agent 会读到"PR 里不要提白标 fork"之类的开发者反馈，开发者会话会读到创作 Agent 保存的项目记忆）。

## 2. 落盘规则

### 2.1 目录派生（`fn:X4s.resolve`）

```js
resolve=mu(()=>{
  let e=pnd()??f1_();            // CLAUDE_COWORK_MEMORY_PATH_OVERRIDE ?? autoMemoryDirectory
  if(e)return e;
  let t=k8.join(Rwe(),"projects"), // Rwe = CLAUDE_CODE_REMOTE_MEMORY_DIR ?? 配置目录 En()
      r=Lu(Va())??Va();           // git 仓库根 ?? projectRoot(cwd)
  return(k8.join(t,WT(r),d1_)+k8.sep).normalize("NFC")},  // d1_="memory"
  ()=>`${Va()}|${eBe()}`)
```

- 配置目录 `fn:En` = `CLAUDE_CONFIG_DIR ?? ~/.claude`。
- slug `fn:WT`：`fEo(e)=e.replace(/[^a-zA-Z0-9]/g,"-")`；超过 `Yre=200` 字符则截断到 200 并追加路径哈希。文档同义："`<project>` is your working directory path with non-alphanumeric characters replaced by `-`… truncates the name to 200 characters and appends a hash" —— [sessions#where-transcripts-are-stored](https://code.claude.com/docs/en/sessions#where-transcripts-are-stored)
- 文档："The `<project>` path is derived from the git repository, so all worktrees and subdirectories within the same repo share one auto memory directory. Outside a git repo, the project root is used instead." —— [memory#storage-location](https://code.claude.com/docs/en/memory#storage-location)；v2.1.63 CHANGELOG。
- transcript 目录 `fn:bN(cwd)` 不走 git 根，仍按 cwd slug（本机验证表已证实）。

### 2.2 索引与截断

- 常量：`M_="MEMORY.md"`，`iQ=200`（行），`Mce=25000`（字节）。`fn:pRr` 先按 200 行截，再按 25 000 字节截到最后一个换行，并生成告警 "`MEMORY.md` is N lines (limit: 200). Only part of it was loaded. Keep index entries to one line under ~200 chars; move detail into topic files."
- 文档："The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes first, are loaded at the start of every conversation." 25KB 上限 v2.1.83；接近上限提醒 v2.1.186；超限写入后报错 v2.1.210；只计已加载内容 v2.1.211。—— [memory#how-it-works](https://code.claude.com/docs/en/memory#how-it-works)、CHANGELOG
- 超限错误原文（发给模型）："Error: this write left the memory index at MEMORY.md at 214 lines, over its 200-line read limit. The write succeeded, but everything past the limit is silently dropped each time the index is loaded — entries at the end are already invisible to readers. Rewrite it to under 140 lines now…" —— [errors#memory-index-is-over-its-read-limit](https://code.claude.com/docs/en/errors#memory-index-is-over-its-read-limit)
- 主题文件不在启动时加载，按需用文件工具读。frontmatter `type` 四类 `user | feedback | project | reference`；`modified` ISO 时间戳由 CLI 写入（v2.1.214）。
- MEMORY.md 内容走与 CLAUDE.md 相同的附件通道注入（`fn:Wst("AutoMem")` 返回 `Q2e()` 即 `MEMORY.md` 路径；标签文本 " (user's auto-memory, persists across conversations)"，与 " (project instructions, checked into the codebase)" 并列）。
- 保留：memory 目录被排除在 `cleanupPeriodDays` 清理之外，v2.1.228 修复了误删目录内容的问题。—— [claude-directory#cleaned-up-automatically](https://code.claude.com/docs/en/claude-directory#cleaned-up-automatically)

### 2.3 后台自动提取

除模型自己调用 `Write` 外，还有后台 "extractMemories"（v2.1.59 "Claude automatically saves useful context to auto-memory"）：`fn:IAa` 在主循环结束后由 `executeExtractMemories` 触发，条件 `!i.agentId && Yvn()`，其中 `Yvn()` = GrowthBook 标志 `tengu_passport_quail` 且（交互式 或 `tengu_slate_thimble`）。即非交互/SDK 模式下是否跑后台提取由服务端灰度决定，本地不可配置；会话已直接写过 memory 文件时跳过（`tengu_extract_memories_skipped_direct_write`）。

### 2.4 Agent 收到的写入指令

官方文档明言系统提示词不公开（[settings](https://code.claude.com/docs/en/settings)："Claude Code's system prompt isn't published"）。以下为 CLI 2.1.233 二进制中的模板，两个变体由 GrowthBook `tengu_stone_shell` 选择：

基础变体 `fn:$ad`（默认）：

````
# Memory
You have a persistent file-based memory at `<dir>`. This directory already exists — write to it directly with the Write tool (do not run mkdir or check for its existence). Each memory is one file holding one fact, with frontmatter:
```markdown
name: <short-kebab-case-slug>
description: <one-line summary, used to decide relevance during recall>
metadata:
  type: user | feedback | project | reference
<the fact; for feedback/project, follow with **Why:** and **How to apply:** lines. Link related memories with [[their-name]].>
```
…
After writing the file, add a one-line pointer in `MEMORY.md` (`- [Title](file.md) — hook`). `MEMORY.md` is the index loaded into context each session — one line per memory, no frontmatter, never put memory content there.
````

stone_shell 变体 `fn:Vad` / 模板 `m2_`（节选）：

```
# auto memory
You have a persistent, file-based memory at `{memory_dir}`.
The files there are lessons you saved from prior sessions, what you save there in this session is all that persists after the session is completed or if the user stops responding. …
A good memory is applicable, durable, and legible: …
You must NOT save a memory unless you have validated that it is applicable, durable, AND legible.
Check each reply before you send it — … did the user's latest message teach you a durable, applicable lesson? … If you've decided to write to your memory, you MUST make your memory write before treating your turn as finished …
```

`exclude_dynamic_sections=True` 时系统提示词内为 `fn:Azs` 静态版："You have a persistent, file-based memory system. The directory path is provided in your session context." 目录随首条用户消息给出。

## 3. 重定向记忆目录

| 方案 | 语义 | 副作用 | 出处 |
|---|---|---|---|
| `autoMemoryDirectory`（settings，v2.1.74） | 绝对路径或 `~/` 前缀；相对路径、盘符根、含 NUL 均被 `fn:und/dnd` 拒绝并回落默认。优先级（`fn:f1_`）：`policySettings` > `flagSettings`（`--settings`）> （`eBe()` 为真时 `localSettings` > `projectSettings`）> `userSettings`。`eBe()` 在非交互模式恒真，故 SDK 会话会读项目 `.claude/settings.json` 中的该键。它是固定目录，不再按项目派生。 | 只影响 memory；transcript / resume / settings 位置不变。schema 描述写 "Ignored if set in projectSettings (checked-in .claude/settings.json) for security"，与代码的信任门控表述不一致；文档说受 workspace trust 约束。 | [settings-reference#automemorydirectory](https://code.claude.com/docs/en/settings-reference#automemorydirectory)、CHANGELOG 2.1.74、`fn:f1_` |
| `CLAUDE_CONFIG_DIR` | "Override the configuration directory (default: `~/.claude`). All settings, session history, and plugins are stored under this path." | 连带搬走 `settings.json`、用户级 `CLAUDE.md`/`rules/`、`projects/<slug>/*.jsonl`（transcript，影响 `resume`/`continue` 查找）、`projects/<slug>/memory/`、`agent-memory/`、plugins、`~/.claude.json`（`fn:Jiy`）。只能从进程环境、用户或 managed settings 设，v2.1.251 起项目级 `env` 不再生效。SDK 用 `env={"CLAUDE_CONFIG_DIR": ...}`。用户级 settings 也随之消失，`setting_sources` 里的 `user` 会读新目录。 | [env-vars](https://code.claude.com/docs/en/env-vars)、[claude-directory](https://code.claude.com/docs/en/claude-directory)、hosting |
| `HOME` | 官方文档无任何说明；代码默认目录取 `os.homedir()`（`fn:En`），改 `HOME` 等价于改整个配置目录但还会影响 shell/工具链。 | 不推荐。 | — |
| `CLAUDE_CODE_PROJECT_DIR_NAME`（v2.1.234） | 与 `CLAUDE_CONFIG_DIR` 同设时，`<config dir>/projects/<name>/` 同时存 transcript 与 memory。 | **本机捆绑 CLI 2.1.233 不含该字符串，不支持**；需 SDK ≥ 0.2.140。 | [sessions#name-the-project-directory-yourself](https://code.claude.com/docs/en/sessions#name-the-project-directory-yourself) |
| `CLAUDE_CODE_REMOTE_MEMORY_DIR` / `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` | 内部变量：前者替换 `Rwe()` 根（memory 与 user 级 agent-memory 一起搬）；后者直接指定 memory 目录且不做 `~` 展开，还会让自定义 system prompt 也追加 memory 段。 | 未文档化，随版本可变，不宜依赖。 | `fn:Rwe`、`fn:pnd`、`fn:Ig` |
| 其他 SDK 选项 | `ClaudeAgentOptions` 无 memory 目录字段；`setting_sources` 不影响 memory 加载；`cwd` 决定 slug。`SessionStore` 只镜像 transcript，不含 memory。 | — | [agent-sdk/python](https://code.claude.com/docs/en/agent-sdk/python)、hosting |

对 ArcReel 的可行路径：服务端已把项目 `.claude/`（CLAUDE.md 模式变体）投影进 cwd，可同时投影 `.claude/settings.json` 写入按项目生成的绝对 `autoMemoryDirectory`（如 `<app_data_dir>/<project_name>/.claude/memory/`），无需改 `CLAUDE_CONFIG_DIR`，transcript 与 session_store 行为不变。

## 4. 子智能体 `AgentDefinition.memory`

- 允许值 `user | project | local`；Python `AgentDefinition.memory: Literal["user","project","local"] | None = None`（`types.py:98`）。SDK 0.1.49 加入，CLI frontmatter 支持自 v2.1.33。—— [agent-sdk/python#agentdefinition](https://code.claude.com/docs/en/agent-sdk/python#agentdefinition)、SDK CHANGELOG、CC CHANGELOG 2.1.33
- 文档路径：`user` → `~/.claude/agent-memory/<name-of-agent>/`；`project` → `.claude/agent-memory/<name-of-agent>/`；`local` → `.claude/agent-memory-local/<name-of-agent>/`。推荐 `project`。启用后自动开放 Read/Write/Edit，并加载该目录 `MEMORY.md` 前 200 行 / 25KB。—— [sub-agents#enable-persistent-memory](https://code.claude.com/docs/en/sub-agents#enable-persistent-memory)
- 代码 `fn:mRr(name, scope)`：名字先经 `Rzs` 清洗（`/[^a-zA-Z0-9\-_]/g → "-"`，空则 `unknown`）；`project` → `join(Yt(), ".claude", "agent-memory", name)`，`local` → `join(Yt(), ".claude", "agent-memory-local", name)`（有 `CLAUDE_CODE_REMOTE_MEMORY_DIR` 时改到 `<dir>/projects/<slug(git根)>/agent-memory-local/`），`user` → `join(Rwe(), "agent-memory", name)`。`Yt()` 是**当前 cwd**，不做 git 根归并；`mRr` 不读 `autoMemoryDirectory`，因此重定向主 memory 不影响子智能体目录。
- 依附主开关："if you turn auto memory off, with the `autoMemoryEnabled` setting or `CLAUDE_CODE_DISABLE_AUTO_MEMORY`, the `memory` field has no effect" —— sub-agents 同页
- 主会话 auto memory 不进入非 fork 子智能体："The main conversation's auto memory isn't loaded into subagents; the exception is a fork". —— [memory#how-it-works](https://code.claude.com/docs/en/memory#how-it-works)

## 5. 主 Agent 有无 project/user 作用域切换

没有。主 Agent 的目录只有 §2.1 一条解析链：内部覆盖 → `autoMemoryDirectory` → `<配置目录>/projects/<slug(git根??cwd)>/memory/`。文档把 `projects/<project>/memory/` 标为 "Global only"，而 `agent-memory/<name>/` 标为 "Project and global"（[claude-directory](https://code.claude.com/docs/en/claude-directory)）；memory 页表格 Scope 列 "Per repository, shared across worktrees"，并强调 "Auto memory is machine-local"（[memory#claude-md-vs-auto-memory](https://code.claude.com/docs/en/memory#claude-md-vs-auto-memory)）。要得到"项目目录内"的记忆位置，只能用 `autoMemoryDirectory` 指到该项目的绝对路径（可放在项目 `.claude/settings.json`，SDK 非交互模式下会被读取），或整体切换 `CLAUDE_CONFIG_DIR`。

## CHANGELOG 相关条目

| 版本 | 条目 |
|---|---|
| 2.1.32 | Claude now automatically records and recalls memories as it works（首次引入） |
| 2.1.33 | `memory` frontmatter field for agents (`user`/`project`/`local`) |
| 2.1.50 | `CLAUDE_CODE_SIMPLE` fully strips session memory 等 |
| 2.1.59 | Claude automatically saves useful context to auto-memory. Manage with /memory |
| 2.1.63 | Project configs & auto memory shared across git worktrees of the same repository |
| 2.1.74 | Added `autoMemoryDirectory` setting |
| 2.1.75 | last-modified timestamps to memory files |
| 2.1.77 | Fixed `--resume` truncation race with memory-extraction writes |
| 2.1.81 | `--bare` flag, auto-memory fully disabled |
| 2.1.83 | `MEMORY.md` index truncates at 25KB as well as 200 lines |
| 2.1.98 | `--exclude-dynamic-system-prompt-sections` in print mode |
| 2.1.186 | Reminder to compact `MEMORY.md` near the size limit |
| 2.1.210 / 2.1.211 | Over-limit write error；只计已加载内容 |
| 2.1.214 | ISO `modified` timestamp in memory frontmatter |
| 2.1.228 | Fixed session cleanup deleting contents inside memory folder |
| 2.1.234 | `CLAUDE_CODE_PROJECT_DIR_NAME` |
| 2.1.251 | Project-level `env` no longer sets `CLAUDE_CONFIG_DIR` |

CHANGELOG 未出现 `autoMemoryEnabled`、`CLAUDE_CODE_DISABLE_AUTO_MEMORY` 的引入条目。Python SDK：0.1.49 `AgentDefinition.memory`；0.1.57 `exclude_dynamic_sections`；0.1.60 修复 `setting_sources=[]` 被当 falsy；0.2.139 捆绑 CLI 2.1.233；0.2.140 捆绑 2.1.235。

## 未能验证 / 文档空白

- 自定义字符串 `system_prompt` 下 `MEMORY.md` 是否仍经 CLAUDE.md 附件通道注入首条用户消息：源码显示注入点只受 `Ig()` 与 CLAUDE.md 加载开关门控，未做实测。
- `autoMemoryDirectory` 写在 `projectSettings` 时，schema 描述说"忽略"，代码 `fn:f1_` 在 `eBe()` 为真时读取；未实测 SDK 会话是否真的采用项目级值（建议 #2309 实施时用 init 消息 `memory_paths.auto` 做断言）。
- 后台 extractMemories 在 SDK 模式下是否实际运行取决于服务端 GrowthBook 标志，无法在本地判定。
- `HOME` 覆盖无官方说明，未测。

## 来源清单

官方文档（均可访问）：memory、settings、settings-reference、env-vars、sub-agents、sessions、claude-directory、cli-reference、headless、errors、agent-sdk/{modifying-system-prompts, sessions, subagents, python, typescript, hosting, claude-code-features, permissions, secure-deployment}，前缀 `https://code.claude.com/docs/en/`。
CHANGELOG：`https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md`；`https://raw.githubusercontent.com/anthropics/claude-agent-sdk-python/main/CHANGELOG.md`。
本机：`.venv/lib/python3.12/site-packages/claude_agent_sdk/{types.py,_cli_version.py,_internal/transport/subprocess_cli.py,_bundled/claude}`；`server/agent_runtime/options_assembler.py`；`lib/app_data_dir.py`；探针脚本见本票 issue 评论描述（scratchpad，未入库）。
