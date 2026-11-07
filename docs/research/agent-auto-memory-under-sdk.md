# Claude Code auto memory under Agent SDK: mechanisms and configuration

> Status: research complete, for map [#2306](https://github.com/ArcReel/ArcReel/issues/2306); implementation tickets #2309/#2310 follow.
> Related: [#2307](https://github.com/ArcReel/ArcReel/issues/2307), this ticket.
> Baseline: Python `claude-agent-sdk`0.2.139, bundled CLI 2.1.233 (`.venv/lib/python3.12/site-packages/claude_agent_sdk/_cli_version.py`). Documented versions follow Claude Code CHANGELOG. CLI evidence comes from `strings` on bundled `claude_agent_sdk/_bundled/claude`, bun-packaged Mach-O with minified identifiers labeled `fn:<压缩名>` for reproduction.

## Background

Embedded creative Agents start through `server/agent_runtime/options_assembler.py` using `SystemPromptPreset(preset="claude_code")` + `setting_sources=["project"]`, cwd `app_data_dir()/<project_name>`, default `<repo>/projects/<project_name>` (`lib/infra/app_data_dir.py`). Investigate activation, paths, redirection, and subagent memory semantics.

## Overview

| Question | Finding |
|---|---|
| 1 Enabled by default? | Yes; SDK/`-p` do not disable. Controlled by `autoMemoryEnabled`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY`, `--bare`/`CLAUDE_CODE_SIMPLE`, safe mode, `/pause-memory`; `exclude_dynamic_sections` relocates only |
| 2 Storage | `<配置目录>/projects/<slug(git 仓库根 ?? cwd)>/memory/`; `MEMORY.md`200 lines/25000 bytes, topic frontmatter; instructions§2.4 |
| 3 Redirection | `autoMemoryDirectory` absolute/`~/`, policy>`--settings`>trusted/noninteractive local/project>user; `CLAUDE_CONFIG_DIR` also moves transcripts/settings; `HOME` undocumented; `CLAUDE_CODE_PROJECT_DIR_NAME` needs≥2.1.234, unavailable locally |
| 4 Subagents | `user`→`<配置目录>/agent-memory/<name>/`; `project`→`<cwd>/.claude/agent-memory/<name>/`; `local`→`<cwd>/.claude/agent-memory-local/<name>/`; master-switch dependent, ignores `autoMemoryDirectory` |
| 5 Main scope switch | None; HOME/config+git-root slug only; project-local requires absolute `autoMemoryDirectory` |

## 1. Defaults and controls

### 1.1 Official documentation

- "Auto memory is on by default."; [memory#enable-or-disable-auto-memory](https://code.claude.com/docs/en/memory#enable-or-disable-auto-memory)
- Settings key `autoMemoryEnabled` (Boolean, default `true`, allowed in any settings file). With `false`, "Claude doesn't read from or write to the auto memory directory". See [settings-reference#automemoryenabled](https://code.claude.com/docs/en/settings-reference#automemoryenabled)
- Environment variable `CLAUDE_CODE_DISABLE_AUTO_MEMORY`: `1` disables; `0` forces it on, even with `--bare` or `autoMemoryEnabled: false`. "When disabled, Claude does not create or load auto memory files." See [env-vars](https://code.claude.com/docs/en/env-vars)
- `--bare` / `CLAUDE_CODE_SIMPLE=1` (introduced in v2.1.81): CHANGELOG wording "auto-memory fully disabled". See [cli-reference](https://code.claude.com/docs/en/cli-reference), [CHANGELOG 2.1.81](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md)
- SDK mode **does not disable it**: auto memory is "Loaded into the system prompt at session start", listed in the "What settingSources does not control" table, "read regardless of its value". Disable through `autoMemoryEnabled: false` or `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env`. See [agent-sdk/claude-code-features#what-settingsources-does-not-control](https://code.claude.com/docs/en/agent-sdk/claude-code-features#what-settingsources-does-not-control)
- Likewise for `-p`: "Without it [`--bare`], `claude -p` loads the same context an interactive session would, including anything configured in the working directory or `~/.claude`." See [headless](https://code.claude.com/docs/en/headless)
- Under the SDK, Agents save memory through ordinary `Write`/`Edit` tools, without a dedicated memory tool, "so those tools must be enabled for the agent to save memories". See the same claude-code-features page
- `exclude_dynamic_sections` **cannot exclude** memory; it only moves it to the first user message: "the working directory, the git-repo flag, the platform, the active shell, the OS version, and auto memory paths still reach Claude, but as part of the first user message rather than the system prompt." Applies only to the `claude_code` preset. Added in Python SDK 0.1.57 (SDK CHANGELOG #797); corresponding CLI `--exclude-dynamic-system-prompt-sections` added to print mode in v2.1.98. See [agent-sdk/modifying-system-prompts](https://code.claude.com/docs/en/agent-sdk/modifying-system-prompts#improve-prompt-caching-across-users-and-machines)
- Multitenant warning: "Do not rely on default `query()` options for multi-tenant isolation... set `settingSources: []` plus `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env`." See claude-code-features, [agent-sdk/hosting](https://code.claude.com/docs/en/agent-sdk/hosting)

### 1.2 Bundled CLI 2.1.233 source

Master `fn:Ig()` short-circuits in order:

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

Schema `autoMemoryEnabled`: "Enable auto-memory for this project. When false, Claude will not read from or write to the auto-memory directory."

System assembly `fn:lW`: `claude_code` inserts memory through `xF("memory", twn(...))`; `excludeDynamicSections` uses static `fn:Qad`, "The directory path is provided in your session context." `fn:Cvi → eld` supplies first-user `# auto memory\nMemory directory: \`<dir>\``. Custom string `system_prompt` bypasses `lW`, adding memory only with internal `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` (`fn:twn` call `m=o!==void 0&&Jvn()?await twn(r):null`).

Print/SDK have no separate disabling branch: `fn:eBe()` (`if(xn())return!0`, `xn()` noninteractive) actually treats noninteractive workspaces as trusted.

### 1.3 Local verification

Minimal `uv run python` probes, `query()` + `SystemPromptPreset(preset="claude_code")` + `setting_sources=["project"]`, `max_turns=1`, once per cwd:

| cwd | Init `memory_paths.auto` | Created at startup |
|---|---|---|
| Nongit `.../sdkcheck/nogit` | `~/.claude/projects/-…-sdkcheck-nogit/memory/` | Same directory, empty `memory/`, transcript `.jsonl` |
| Git subdirectory `.../sdkcheck/gitrepo/sub` | `~/.claude/projects/-…-sdkcheck-gitrepo/memory/`, root slug | Empty `-…-gitrepo/memory/`, transcript cwd `-…-gitrepo-sub/<id>.jsonl` |

SDK memory is **enabled**; init contains internal `memory_paths`, CLI 2.1.233 `memory_paths:{auto?:string,team?:string}`, `@internal`. Startup creates empty `memory/`, `fn:jIe` ensureMemoryDirExists.

**Why no local `-Users-<user>-MyProjects-ArcReel-projects-*/memory`?** The memory directory derives from `Lu(cwd) ?? cwd`; `Lu` resolves the git repository root (`fn:Lu → Zc → rootByPath`). ArcReel’s project cwd `<repo>/projects/<name>` is inside the ArcReel repository (`projects/` is merely gitignored, not a separate repository). SDK session memory therefore lands in `~/.claude/projects/-Users-<user>-MyProjects-ArcReel/memory/`, the **same directory** used by developers running interactive Claude Code in the repository. This is neither disabled nor untriggered: the v2.1.63 rule "auto memory shared across git worktrees of the same repository" merges it into the repository root. There is also no local `-…-ArcReel-projects-<name>/` transcript directory, indicating no SDK sessions started from that data dir during the 30-day retention window (or the data dir points elsewhere); this is unrelated to the missing memory directory.

Risk: creative/developer sessions share `MEMORY.md`, contaminating each other. Creative Agents may see developer feedback such as avoiding white-label-fork mentions in PRs; developer sessions may see creative-project memories.

## 2. Storage rules

### 2.1 Directory derivation, `fn:X4s.resolve`

```js
resolve=mu(()=>{
  let e=pnd()??f1_();            // CLAUDE_COWORK_MEMORY_PATH_OVERRIDE ?? autoMemoryDirectory
  if(e)return e;
  let t=k8.join(Rwe(),"projects"), // Rwe = CLAUDE_CODE_REMOTE_MEMORY_DIR ?? 配置目录 En()
      r=Lu(Va())??Va();           // git 仓库根 ?? projectRoot(cwd)
  return(k8.join(t,WT(r),d1_)+k8.sep).normalize("NFC")},  // d1_="memory"
  ()=>`${Va()}|${eBe()}`)
```

- Config `fn:En`=`CLAUDE_CONFIG_DIR ?? ~/.claude`.
- Slug `fn:WT`: `fEo(e)=e.replace(/[^a-zA-Z0-9]/g,"-")`; beyond `Yre=200` characters, truncate to 200 and append a path hash. Documentation says the same: "`<project>` is your working directory path with non-alphanumeric characters replaced by `-`… truncates the name to 200 characters and appends a hash"; [sessions#where-transcripts-are-stored](https://code.claude.com/docs/en/sessions#where-transcripts-are-stored)
- Documentation: "The `<project>` path is derived from the git repository, so all worktrees and subdirectories within the same repo share one auto memory directory. Outside a git repo, the project root is used instead." See [memory#storage-location](https://code.claude.com/docs/en/memory#storage-location); v2.1.63 CHANGELOG.
- Transcript `fn:bN(cwd)` remains cwd-slug, not git-root, verified above.

### 2.2 Index and truncation

- `M_="MEMORY.md"`, `iQ=200`lines, `Mce=25000`bytes. `fn:pRr` truncates200 lines then25000 bytes through last newline, warning: "`MEMORY.md` is N lines (limit: 200). Only part of it was loaded. Keep index entries to one line under ~200 chars; move detail into topic files."
- Documentation: "The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes first, are loaded at the start of every conversation." The 25KB limit arrived in v2.1.83; near-limit reminders in v2.1.186; errors after over-limit writes in v2.1.210; counting only loaded content in v2.1.211. See [memory#how-it-works](https://code.claude.com/docs/en/memory#how-it-works), CHANGELOG
- Model-facing error: "Error: this write left the memory index at MEMORY.md at 214 lines, over its 200-line read limit. The write succeeded, but everything past the limit is silently dropped each time the index is loaded — entries at the end are already invisible to readers. Rewrite it to under 140 lines now…" [errors#memory-index-is-over-its-read-limit](https://code.claude.com/docs/en/errors#memory-index-is-over-its-read-limit)
- Topics load on-demand via file tools, not startup. Frontmatter `type`: `user | feedback | project | reference`; CLI ISO `modified`,2.1.214.
- Same attachment channel as CLAUDE.md: `fn:Wst("AutoMem")` returns `Q2e()`/`MEMORY.md` paths, label " (user's auto-memory, persists across conversations)" beside " (project instructions, checked into the codebase)".
- Retention: memory excluded from `cleanupPeriodDays`;2.1.228 fixed accidental deletion. [claude-directory#cleaned-up-automatically](https://code.claude.com/docs/en/claude-directory#cleaned-up-automatically)

### 2.3 Background extraction

Besides the model calling `Write` itself, background "extractMemories" exists (v2.1.59 "Claude automatically saves useful context to auto-memory"): `fn:IAa` is triggered by `executeExtractMemories` after the main loop, with condition `!i.agentId && Yvn()`. Here `Yvn()` means GrowthBook flag `tengu_passport_quail` and (interactive or `tengu_slate_thimble`). Thus background extraction in noninteractive/SDK mode depends on server rollout flags, not local configuration. It skips sessions that already wrote memory files directly (`tengu_extract_memories_skipped_direct_write`).

### 2.4 Instructions received by Agents

Official documentation explicitly says the system prompt is unpublished ([settings](https://code.claude.com/docs/en/settings): "Claude Code's system prompt isn't published"). The following templates come from the CLI 2.1.233 binary; GrowthBook `tengu_stone_shell` selects between the two variants:

Default `fn:$ad`:

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

stone_shell `fn:Vad` / `m2_` excerpt:

```
# auto memory
You have a persistent, file-based memory at `{memory_dir}`.
The files there are lessons you saved from prior sessions, what you save there in this session is all that persists after the session is completed or if the user stops responding. …
A good memory is applicable, durable, and legible: …
You must NOT save a memory unless you have validated that it is applicable, durable, AND legible.
Check each reply before you send it — … did the user's latest message teach you a durable, applicable lesson? … If you've decided to write to your memory, you MUST make your memory write before treating your turn as finished …
```

With `exclude_dynamic_sections=True`, system static `fn:Azs`: "You have a persistent, file-based memory system. The directory path is provided in your session context." First user messages supply paths.

## 3. Redirect memory directories

| Option | Semantics | Side effects | Sources |
|---|---|---|---|
| `autoMemoryDirectory`,settings2.1.74 | Absolute/`~/`; `fn:und/dnd` rejects relative paths, drive roots, NUL, falling back. `fn:f1_` priority `policySettings` > `flagSettings` (`--settings`) > `localSettings` > `projectSettings` when `eBe()` true > `userSettings`. Noninteractive `eBe()` always true, so SDK reads project `.claude/settings.json`. Fixed directory, no further project derivation | Memory only; transcripts/resume/settings unchanged. Schema says "Ignored if set in projectSettings (checked-in .claude/settings.json) for security", inconsistent with code trust gating; docs describe workspace trust | [settings-reference#automemorydirectory](https://code.claude.com/docs/en/settings-reference#automemorydirectory),CHANGELOG 2.1.74,`fn:f1_` |
| `CLAUDE_CONFIG_DIR` | "Override the configuration directory (default: `~/.claude`). All settings, session history, and plugins are stored under this path." | Also moves `settings.json`, user-level `CLAUDE.md`/`rules/`, `projects/<slug>/*.jsonl` (transcripts, affecting `resume`/`continue` lookup), `projects/<slug>/memory/`, `agent-memory/`, plugins, and `~/.claude.json` (`fn:Jiy`). Can only be set through the process environment, user settings, or managed settings; project-level `env` no longer applies since v2.1.251. SDK uses `env={"CLAUDE_CONFIG_DIR": ...}`. User settings move as well; `user` in `setting_sources` reads the new directory. | [env-vars](https://code.claude.com/docs/en/env-vars), [claude-directory](https://code.claude.com/docs/en/claude-directory), hosting |
| `HOME` | Undocumented; `os.homedir()` default, `fn:En`; changing `HOME` changes all config plus shells/toolchains | Not recommended | — |
| `CLAUDE_CODE_PROJECT_DIR_NAME`,2.1.234 | Alongside `CLAUDE_CONFIG_DIR`, `<config dir>/projects/<name>/` stores transcripts/memory | **Bundled2.1.233 lacks string/support**, SDK≥0.2.140 required | [sessions#name-the-project-directory-yourself](https://code.claude.com/docs/en/sessions#name-the-project-directory-yourself) |
| `CLAUDE_CODE_REMOTE_MEMORY_DIR` / `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` | Internal: former replaces `Rwe()` root for memory/user agent-memory; latter directly sets memory without `~` expansion, also adds memory to custom system prompts | Undocumented/version-unstable, avoid dependencies | `fn:Rwe`,`fn:pnd`,`fn:Ig` |
| Other SDK options | No directory field in `ClaudeAgentOptions`; `setting_sources` independent; `cwd` controls slug. `SessionStore` mirrors transcripts only | — | [agent-sdk/python](https://code.claude.com/docs/en/agent-sdk/python),hosting |

ArcReel can project `.claude/settings.json` with absolute project-derived `autoMemoryDirectory`, e.g. `<app_data_dir>/<project_name>/.claude/memory/`, alongside existing cwd `.claude/` CLAUDE.md variants. No `CLAUDE_CONFIG_DIR` change, preserving transcripts/session_store.

## 4. Subagent `AgentDefinition.memory`

- `user | project | local`; Python `AgentDefinition.memory: Literal["user","project","local"] | None = None`, `types.py:98`. SDK 0.1.49; CLI frontmatter2.1.33. [agent-sdk/python#agentdefinition](https://code.claude.com/docs/en/agent-sdk/python#agentdefinition), SDK/CLI CHANGELOG.
- `user`→`~/.claude/agent-memory/<name-of-agent>/`;`project`→`.claude/agent-memory/<name-of-agent>/`;`local`→`.claude/agent-memory-local/<name-of-agent>/`. Prefer `project`. Automatically enable Read/Write/Edit and load `MEMORY.md`200 lines/25 KB. [sub-agents#enable-persistent-memory](https://code.claude.com/docs/en/sub-agents#enable-persistent-memory)
- `fn:mRr(name, scope)` sanitizes names with `Rzs`, `/[^a-zA-Z0-9\-_]/g → "-"`, empty→`unknown`;`project`→`join(Yt(), ".claude", "agent-memory", name)`;`local`→`join(Yt(), ".claude", "agent-memory-local", name)` (with `CLAUDE_CODE_REMOTE_MEMORY_DIR`, `<dir>/projects/<slug(git根)>/agent-memory-local/`);`user`→`join(Rwe(), "agent-memory", name)`. `Yt()` is **current cwd**, without git-root merging. `mRr` ignores `autoMemoryDirectory`, so main redirection does not move subagents.
- Depends on the master switch: "if you turn auto memory off, with the `autoMemoryEnabled` setting or `CLAUDE_CODE_DISABLE_AUTO_MEMORY`, the `memory` field has no effect"; same sub-agents page
- Main-conversation auto memory does not enter non-fork subagents: "The main conversation's auto memory isn't loaded into subagents; the exception is a fork". See [memory#how-it-works](https://code.claude.com/docs/en/memory#how-it-works)

## 5. Main Agent project/user scope switching

None. The main Agent directory has only the §2.1 resolution chain: internal override → `autoMemoryDirectory` → `<配置目录>/projects/<slug(git根??cwd)>/memory/`. Documentation labels `projects/<project>/memory/` "Global only", but `agent-memory/<name>/` "Project and global" ([claude-directory](https://code.claude.com/docs/en/claude-directory)); the memory page’s Scope column says "Per repository, shared across worktrees" and emphasizes "Auto memory is machine-local" ([memory#claude-md-vs-auto-memory](https://code.claude.com/docs/en/memory#claude-md-vs-auto-memory)). A memory location inside the project directory requires `autoMemoryDirectory` pointing to an absolute project path (which may be in project `.claude/settings.json`, read by noninteractive SDK mode), or relocating all of `CLAUDE_CONFIG_DIR`.

## Relevant CHANGELOG

| Version | Entry |
|---|---|
| 2.1.32 | Claude now automatically records and recalls memories as it works (first introduction) |
| 2.1.33 | `memory` frontmatter field for agents (`user`/`project`/`local`) |
| 2.1.50 | `CLAUDE_CODE_SIMPLE` fully strips session memory and others |
| 2.1.59 | Claude automatically saves useful context to auto-memory. Manage with /memory |
| 2.1.63 | Project configs & auto memory shared across git worktrees of the same repository |
| 2.1.74 | Added `autoMemoryDirectory` setting |
| 2.1.75 | last-modified timestamps to memory files |
| 2.1.77 | Fixed `--resume` truncation race with memory-extraction writes |
| 2.1.81 | `--bare` flag, auto-memory fully disabled |
| 2.1.83 | `MEMORY.md` index truncates at 25KB as well as 200 lines |
| 2.1.98 | `--exclude-dynamic-system-prompt-sections` in print mode |
| 2.1.186 | Reminder to compact `MEMORY.md` near the size limit |
| 2.1.210 / 2.1.211 | Over-limit write error; count loaded content only |
| 2.1.214 | ISO `modified` timestamp in memory frontmatter |
| 2.1.228 | Fixed session cleanup deleting contents inside memory folder |
| 2.1.234 | `CLAUDE_CODE_PROJECT_DIR_NAME` |
| 2.1.251 | Project-level `env` no longer sets `CLAUDE_CONFIG_DIR` |

No introduction entries found for `autoMemoryEnabled`/`CLAUDE_CODE_DISABLE_AUTO_MEMORY`. Python 0.1.49 `AgentDefinition.memory`;0.1.57 `exclude_dynamic_sections`;0.1.60 fixes falsy `setting_sources=[]`;0.2.139 bundles 2.1.233,0.2.140 bundles 2.1.235.

## Unverified/documentation gaps

- Whether custom string `system_prompt` still injects `MEMORY.md` through CLAUDE.md attachments into first user messages: code gates only `Ig()`/CLAUDE.md loading, not tested.
- Schema ignores project `autoMemoryDirectory` in `projectSettings`, but `fn:f1_` reads with true `eBe()`. Actual SDK adoption untested; #2309 should assert init `memory_paths.auto`.
- SDK extractMemories execution depends on server GrowthBook flags, undeterminable locally.
- `HOME` overrides undocumented/untested.

## Sources

Accessible official docs: memory/settings/settings-reference/env-vars/sub-agents/sessions/claude-directory/cli-reference/headless/errors/agent-sdk/{modifying-system-prompts,sessions,subagents,python,typescript,hosting,claude-code-features,permissions,secure-deployment}, prefix `https://code.claude.com/docs/en/`.
CHANGELOG: `https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md`; `https://raw.githubusercontent.com/anthropics/claude-agent-sdk-python/main/CHANGELOG.md`.
Local `.venv/lib/python3.12/site-packages/claude_agent_sdk/{types.py,_cli_version.py,_internal/transport/subprocess_cli.py,_bundled/claude}`, `server/agent_runtime/options_assembler.py`, `lib/infra/app_data_dir.py`; probe description in ticket comments, scratchpad not committed.
