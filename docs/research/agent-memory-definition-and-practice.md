# Official definitions, content boundaries, and writing best practices for Agent memory

> Purpose: provide first-party evidence for terminology, content boundaries, writing guidance, and user-interaction precedents for the ArcReel embedded Agent memory Spec (map https://github.com/ArcReel/ArcReel/issues/2306 ).
> Related issue: https://github.com/ArcReel/ArcReel/issues/2308
> Scope: only Anthropic first-party sources are accepted: Claude Code documentation (code.claude.com), Claude platform documentation (platform.claude.com), Agent SDK documentation, Anthropic engineering blog and research articles, Claude Help Center (support.claude.com), the official Claude blog (claude.com/blog, to which anthropic.com/news now returns a 308 redirect), the Claude Code CHANGELOG, and npm publication dates. Third-party system-prompt transcripts and community articles outside the Cookbook are excluded.
> Research date: 2026-09-03. Most documentation pages do not display dates; recency is anchored to the highest version mentioned on the page or a CHANGELOG entry (release dates come from the npm registry `@anthropic-ai/claude-code` `time` field). Materials older than 2026 are dated in the body.
> Conclusion labels: anything not explicitly stated by official sources is marked “unverified”; inference does not replace verification.

---

## 1. Conclusions at a glance

| Issue question | One-sentence conclusion |
| --- | --- |
| 1 Definition and classification | Anthropic divides cross-session knowledge into two complementary mechanisms: human-authored instructions (CLAUDE.md) and notes Claude writes itself (auto memory). The memory tool provides similar file-based memory persisted by the client at the platform layer. A transcript is only conversation history; official sources explicitly distinguish its responsibility and retention policy from memory. |
| 2 What to remember and omit | Auto memory saves only four categories (user / feedback / project / reference), skipping information derivable from code or already in CLAUDE.md. Its structure is a `MEMORY.md` index (one line per entry; only the first 200 lines or 25KB load at startup) plus topic files read on demand. |
| 3 Writing triggers and wording | The main Claude Code conversation’s auto-memory system prompt is not officially published. Three public sources of wording can guide alignment: the API-injected memory-tool “MEMORY PROTOCOL / ASSUME INTERRUPTION”, the documentation’s reinforcing “keep it coherent and organized / Only write down information relevant to <topic>” instructions, and the index-compression error returned to the model when the limit is exceeded. |
| 4 User-visible, editable precedents | Claude Code: memory is plain Markdown that users can edit or delete at any time; `/memory` provides access and a toggle. Official sources explicitly say users can edit or delete it, but Claude keeps updating it. Claude.ai: since 2026-08-25, memory consists of individual Topics that can be read, edited, or deleted in Settings; chat requests to remember / change / forget take effect in the next conversation. Neither promises that Claude will not overwrite user changes. |

---

## 2. Question 1: official definitions and classification

### 2.1 Claude Code: two complementary mechanisms

The Claude Code memory documentation opens by defining two carriers of cross-session knowledge (https://code.claude.com/docs/en/memory , page mentions v2.1.239, accessed 2026-09-03):

> Each Claude Code session begins with a fresh context window. Two mechanisms carry knowledge across sessions: **CLAUDE.md files**: instructions you write to give Claude persistent context · **Auto memory**: notes Claude writes itself based on your corrections and preferences

> Claude Code has two complementary memory systems. Both are loaded at the start of every conversation. Claude treats them as context, not enforced configuration.

Original comparison table (same page):

| Dimension | CLAUDE.md | Auto memory |
| --- | --- | --- |
| Who writes it | You | Claude |
| What it contains | Instructions and rules | Learnings and patterns |
| Scope | Project, user, or org | Per repository, shared across worktrees |
| Loaded into | Every session | Every session (first 200 lines or 25KB) |
| Use for | Coding standards, workflows, project architecture | Your preferences, corrections you give Claude, project context Claude can't derive from the code |

**Four CLAUDE.md scopes** (same page, broadest to narrowest in loading order): Managed policy (organization-wide, deployed by IT) → User (`~/.claude/CLAUDE.md`, personal, across projects) → Project (`./CLAUDE.md` or `./.claude/CLAUDE.md`, shared with the team through source control) → Local (`./CLAUDE.local.md`, personal to this project, added to `.gitignore`). All files are concatenated into context rather than overriding each other. They are injected as a user message after the system prompt ("CLAUDE.md content is delivered as a user message after the system prompt, not as part of the system prompt itself.").

**Auto-memory location and structure** (same page):

> Each project gets its own memory directory at `~/.claude/projects/<project>/memory/`. The `<project>` path is derived from the git repository, so all worktrees and subdirectories within the same repo share one auto memory directory. Outside a git repo, the project root is used instead.

> The directory contains a `MEMORY.md` index and one topic file per memory

> `MEMORY.md` acts as an index of the memory directory. Claude reads and writes files in this directory throughout your session, using `MEMORY.md` to keep track of what's stored where.

> Auto memory is machine-local. … Files are not shared across machines or cloud environments.

The location is configurable: the `autoMemoryDirectory` setting “is read from any settings scope: user, project, local, policy, or `--settings`”; its value must be an absolute path or begin with `~/` (CHANGELOG 2.1.74, npm 2026-03-11). `CLAUDE_CODE_PROJECT_DIR_NAME` together with `CLAUDE_CONFIG_DIR` can specify the `<project>` directory name (v2.1.234+). Toggles: `autoMemoryEnabled` (any settings scope) or the `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` environment variable. Starting with `--bare` also disables it entirely (CHANGELOG 2.1.81, npm 2026-03-20).

**Under the Agent SDK** (https://code.claude.com/docs/en/agent-sdk/claude-code-features , “What settingSources does not control” table):

> Auto memory at `~/.claude/projects/<project>/memory/` — Loaded into the system prompt at session start. The agent writes new memories there with the standard `Write` and `Edit` tools rather than a dedicated memory tool, so those tools must be enabled for the agent to save memories — To disable: Set `autoMemoryEnabled: false` in settings, or `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env`

> Do not rely on default `query()` options for multi-tenant isolation. Because the inputs above are read regardless of `settingSources`, an SDK process can pick up host-level configuration and per-directory memory. For multi-tenant deployments, run each tenant in its own filesystem and set `settingSources: []` plus `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env`.

Thus auto memory is enabled by default under the SDK, is not controlled by `setting_sources`, and requires `Write` / `Edit` tools to save notes. The SDK overview also lists it as a capability ("Skills, commands, and memory — Load automatically from your project's `.claude/` and from `~/.claude/`, same as Claude Code", https://code.claude.com/docs/en/agent-sdk/overview ).

**Subagent memory** is part of auto memory: `AgentDefinition.memory: 'user' | 'project' | 'local'`, stored in `~/.claude/agent-memory/<name>/`, `.claude/agent-memory/<name>/`, or `.claude/agent-memory-local/<name>/`; "if you turn auto memory off … the `memory` field has no effect" (https://code.claude.com/docs/en/sub-agents ). The main conversation’s auto memory is not injected into subagents ("The main conversation's auto memory isn't loaded into subagents; the exception is a fork", memory page).

**After compaction**: auto memory, like the project-root CLAUDE.md, is “Re-injected from disk” (https://code.claude.com/docs/en/context-window#what-survives-compaction ).

### 2.2 Platform layer: memory tool

Definition (https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool , undated page; example model `claude-opus-5`, the current 2026 version):

> The memory tool lets Claude store and retrieve information across conversations in a directory of memory files. Claude can create, read, update, and delete files that persist between sessions, building up knowledge over time without keeping everything in the context window.

> Memory supports just-in-time context retrieval. Rather than loading all relevant information up front, an agent records what it learns in memory files and reads them back on demand.

> The memory tool operates client-side: Claude requests file operations, and your application executes them. You control where and how the data is stored through your own infrastructure.

Key points: tool definition `{"type": "memory_20250818", "name": "memory"}`; commands `view / create / str_replace / insert / delete / rename`; conventional root `/memories` ("The `/memories` path is a prefix that your handler maps onto real storage"). Current documentation says the memory tool itself no longer requires a beta header ("the memory tool itself doesn't require a beta header"); use `anthropic-beta: context-management-2025-06-27` when combining it with context editing. It supports “all Claude 4 and later models”. It first launched as a public beta with Sonnet 4.5 on 2025-09-29 (https://claude.com/blog/context-management ).

Relationship to Claude Code auto memory: both are file-based memory read on demand; they differ in who performs file operations. Auto memory uses Claude Code’s standard `Write` / `Edit` tools to write to a local directory; with the memory tool, your application executes operations and determines storage. The Cookbook’s 2026-03-20 overview states: "Claude Code employs multiple of these strategies in production: compaction for long conversations and two complementary memory systems for cross-session persistence. Our API offers first-party implementations of all three: server-side compaction, context editing (which includes tool-result clearing), and the memory tool." (https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools )

### 2.3 Boundary between session transcripts and memory

Agent SDK sessions page (https://code.claude.com/docs/en/agent-sdk/sessions ):

> A session is the conversation history the SDK accumulates while your agent works. It contains your prompt, every tool call the agent made, every tool result, and every response. The SDK writes it to disk automatically so you can return to it later.

> Sessions persist the **conversation**, not the filesystem.

The hosting page lists three separate kinds of local state: Session transcripts (`~/.claude/projects/`), `CLAUDE.md` memory files, and Working-directory artifacts, explicitly stating "**Transcripts only**: `SessionStore` mirrors transcripts, not `CLAUDE.md` memory files or other working-directory artifacts." (https://code.claude.com/docs/en/agent-sdk/hosting ). Retention also differs: "Claude Code deletes old session transcripts after the `cleanupPeriodDays` retention period, but excludes the files in the memory directory from that retention sweep. `MEMORY.md` and topic files stay until you or Claude edits or deletes them." (memory page; a bug before v2.1.228 caused erroneous deletion, https://code.claude.com/docs/en/claude-directory ).

### 2.4 Engineering-blog classification: memory is one of three long-horizon strategies

《Effective context engineering for AI agents》（https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents ，2025-09-29）：

> To enable agents to work effectively across extended time horizons, we've developed a few techniques that address these context pollution constraints directly: compaction, structured note-taking, and multi-agent architectures.

> Structured note-taking, or agentic memory, is a technique where the agent regularly writes notes persisted to memory outside of the context window. These notes get pulled back into the context window at later times.

> Compaction maintains conversational flow for tasks requiring extensive back-and-forth; Note-taking excels for iterative development with clear milestones; Multi-agent architectures handle complex research and analysis where parallel exploration pays dividends.

“Building effective agents” (2024-12-19) only lists memory as one LLM augmentation ("an LLM enhanced with augmentations such as retrieval, tools, and memory"), without a dedicated definition. No new 2026 engineering article specifically about memory was found on anthropic.com (two domain-restricted searches returned no match). The related “Long-running Claude for scientific computing” (https://www.anthropic.com/research/long-running-Claude , 2026-03-23) calls progress files "the agent's portable long-term memory, acting as a sort of lab notes".

### 2.5 Claude.ai: product memory

Help Center (https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context , Intercom relative timestamp "Updated today"):

> Claude saves memory as a set of individual topics as you chat, rather than summarizing conversations after they end.

> Each project has its own separate memory space and dedicated project summary, so the context within each of your projects is focused, relevant, and separate from other projects or non-project chats.

Memory and searching previous conversations have independent toggles ("Generate memory from chats" and "Search and reference chats"); the latter invokes a RAG tool. The old version (2025-09 through 2026-07) synthesized a "memory summary" every 24 hours; individual Topics replaced it starting 2026-07-10 (https://support.claude.com/en/articles/12138966-release-notes ). Claude Code memory and Claude.ai memory are separate systems: the Help Center lists them separately and does not mention integration (https://support.claude.com/en/articles/14554000-claude-code-power-user-tips : "This is separate from your user-level ~/.claude/CLAUDE.md and project-level ./CLAUDE.md files, which you maintain by hand.").

---

## 3. Question 2: what to remember and omit; progressive disclosure and capacity

### 3.1 Auto memory saves only four categories

Original text from the memory page:

> As it works, Claude saves four kinds of notes for itself. Claude records the kind as a `type` field in the memory file's frontmatter:
> · `user`: your role, expertise, and working preferences
> · `feedback`: corrections you give Claude and approaches you confirm
> · `project`: ongoing work, deadlines, and decisions that Claude can't derive from the code or git history
> · `reference`: where to find information outside the project, such as an issue tracker or dashboard

> Claude skips anything it can derive from the codebase, such as architecture, file paths, or debugging fixes. It also skips anything your CLAUDE.md files already say.

> Claude doesn't save something every session. It decides what's worth remembering based on whether the information would be useful in a future conversation.

Mapping to the issue’s four categories, “preferences vs. facts vs. progress vs. one-time instructions”:

| Issue category | Official guidance |
| --- | --- |
| Preferences | Save: `user` (role, expertise, working preferences) and `feedback` (corrections and confirmed approaches) |
| Facts | Save only what the Agent cannot infer: `project` (decisions, deadlines) and `reference` (external pointers). Skip anything inferable from code / git or already in CLAUDE.md |
| Progress | The `project` category allows "ongoing work", but official examples concern deadlines and decisions; Claude Code does not separately classify production-progress content. ArcReel has already decided to represent progress through the server-side step view, outside memory |
| One-time instructions | Official sources do not use this wording. The criterion is usefulness in a future conversation; requirements that apply only to the current operation do not meet it |

A spoken request to “remember X” uses auto memory; adding it to CLAUDE.md requires an explicit request:

> When you ask Claude to remember something, like "always use pnpm, not npm" or "remember that the API tests require a local Redis instance," Claude saves it to auto memory. To add instructions to CLAUDE.md instead, ask Claude directly, like "add this to CLAUDE.md," or edit the file yourself via `/memory`.

Note an older statement on the claude-directory page ("Claude saves notes as it works: build commands, debugging insights, architecture notes.") conflicts with the memory page’s instruction to skip architecture / debugging fixes. Both are official text; this note follows the memory page and records the difference faithfully.

### 3.2 What belongs in CLAUDE.md (instructions)

Memory page: "Keep it to facts Claude should hold in every session: build commands, conventions, project layout, "always do X" rules. If an entry is a multi-step procedure or only matters for one part of the codebase, move it to a skill or a path-scoped rule instead." When to add entries: "Claude makes the same mistake a second time / A code review catches something Claude should have known about this codebase / You type the same correction or clarification into chat that you typed last session / A new teammate would need the same context to be productive".

Best-practices page (https://code.claude.com/docs/en/best-practices , formerly anthropic.com/engineering/claude-code-best-practices, now a 308 redirect): "Keep it concise. For each line, ask: *"Would removing this cause Claude to make mistakes?"* If not, cut it. Bloated CLAUDE.md files cause Claude to ignore your actual instructions!" Its Exclude column includes "Anything Claude can figure out by reading code / … / Information that changes frequently / Long explanations or tutorials". The `#` shortcut was removed in CHANGELOG 2.0.70 ("Removed # shortcut for quick memory entry (tell Claude to edit your CLAUDE.md instead)").

### 3.3 Recommended progressive-disclosure structure and capacity limits

Structure: index + topic files, one line per index entry, with topic files read on demand.

> The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes first, are loaded at the start of every conversation. Content beyond that threshold is not loaded at session start. Claude keeps `MEMORY.md` concise by moving detailed notes into separate topic files.

> This limit applies only to `MEMORY.md`. … Claude Code doesn't load topic files such as `user_role.md` or `feedback_testing.md` at startup. Claude reads them on demand using its standard file tools when it needs the information.

Topic files have frontmatter (`name` / `description` / `type`; Claude Code v2.1.214+ automatically adds an ISO `modified` timestamp; "Claude Code never adds frontmatter to a file that has none"). Example from the claude-directory page:

```
---
name: Debugging patterns
description: Auth token rotation and database connection troubleshooting for this project
type: reference
---
```

How capacity limits are enforced (memory page; CHANGELOG 2.1.83 added 25KB, 2.1.186 added near-limit reminders, 2.1.210 added over-limit errors, and 2.1.211 counts only content actually loaded):

> After Claude writes to `MEMORY.md`, Claude Code measures the file against the 200-line and 25KB read limits. If the file is near a limit, Claude Code reminds Claude to shorten it: keep one line per entry, move detail into topic files, and merge or drop stale entries. If the file is over a limit, the write still succeeds, but Claude Code returns an error telling Claude to rewrite the index, because everything past the limit is dropped on the next load.

CLAUDE.md size guidance: "target under 200 lines" per file; files over 4 MiB are skipped entirely. `/doctor` (v2.1.206+) recommends removing content derivable from code.

Corresponding memory-tool guidance: `view` truncates files over 16,000 characters; "Consider capping how many characters the `view` command returns, and let Claude page through the rest with `view_range`."; "Track memory file sizes and cap how large a file can grow." / "Periodically delete memory files that haven't been accessed in a long time." (memory-tool page). The Cookbook (https://platform.claude.com/cookbook/tool-use-memory-cookbook , dated 2025-05-22 but mentioning Sonnet 4.6 in its title, so the date is questionable) lists: "Store task-relevant patterns, not conversation history / Organize with clear directory structure / Use descriptive file names / Periodically review and clean up memory"; counterexamples: "Store sensitive information (passwords, API keys, PII) / Let memory grow unbounded / Store everything indiscriminately".

### 3.4 Claude.ai content boundaries (product guidance on what not to remember)

Save (11817273): "Your role, projects, and professional context / The people and places in your work and life / Communication preferences and working style / Technical preferences and coding style / Project details and ongoing work".

Not saved by default (2026-08-25, https://claude.com/blog/claudes-memory-works-everywhere-and-you-decide-whats-in-it ): "By default, Claude does not store topics related to personal or sensitive subject matter, like your health, race, ethnicity, religious beliefs, politics, gender identity, and other similar areas." Users can enable "Include sensitive topics in memory"; a notice appears above the input box whenever such content is saved.

Never saved (same source): "Some information is never saved to memory, even if you ask. This includes government ID numbers, criminal history, financial account numbers, and immigration status. Claude will let you know when it can't save something for this reason."

“Do not remember one-time requests”: not stated in official Claude.ai sources; unverified.

---

## 4. Question 3: writing triggers and wording

### 4.1 Main-conversation Claude Code auto-memory system prompt: unpublished

> Claude Code's system prompt isn't published. To give Claude standing instructions, use `CLAUDE.md` files or the `--append-system-prompt` flag.（https://code.claude.com/docs/en/settings#system-prompt ）

Official sources describe only the effect: "Saved 2 memories" / "Recalled 2 memories" in the interface indicates Claude is reading or writing `~/.claude/projects/<project>/memory/` (memory page). The subagent system prompt "includes instructions for reading and writing to the memory directory … with instructions to curate `MEMORY.md` if it exceeds that limit" (sub-agents page), but its original text is likewise unpublished. Third-party transcripts online are not first-party sources and are excluded here.

CHANGELOG timeline (npm dates): 2.1.32 (2026-02-05), first appearance of "Claude now automatically records and recalls memories as it works"; 2.1.59 (2026-02-25), "Claude automatically saves useful context to auto-memory. Manage with /memory"; 2.1.63 (2026-02-28), shared across worktrees; 2.1.75 (2026-03-13), last-modified information to distinguish old and new; 2.1.186 (2026-06-22), reminder to compress the index near its limit; 2.1.210 (2026-07-14), explicit error when over limit.

### 4.2 Three public sources of model-facing wording

**(a) Protocol automatically injected by the API for the memory tool** (memory-tool page: "When the memory tool is present in your request's `tools`, the API automatically adds this instruction to the system prompt. You don't need to send it yourself"):

```
IMPORTANT: ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE.
MEMORY PROTOCOL:
1. Use the `view` command of your `memory` tool to check for earlier progress.
2. ... (work on the task) ...
   - As you make progress, record status / progress / thoughts etc in your memory.
ASSUME INTERRUPTION: Your context window might be reset at any moment, so you risk losing any progress that is not recorded in your memory directory.
```

**(b) Optional reinforcing instructions from the documentation** (same page):

> Note: when editing your memory folder, always try to keep its content up-to-date, coherent and organized. You can rename or delete files that are no longer relevant. Do not create new files unless necessary.

> You can also guide what Claude writes to memory. For example: "Only write down information relevant to <topic> in your memory system."

For the first instruction, official guidance says "Claude's tool description already tells it to keep the memory directory organized, so you don't need to repeat that instruction"; add it only if the directory remains disorganized.

**(c) Error text returned to the model when Claude Code exceeds the limit** (https://code.claude.com/docs/en/errors#memory-index-is-over-its-read-limit ; "Claude Code delivers the error to Claude after the write rather than printing it as a banner in your terminal"):

```
Error: this write left the memory index at MEMORY.md at 214 lines, over its 200-line read limit. The write succeeded, but everything past the limit is silently dropped each time the index is loaded — entries at the end are already invisible to readers. Rewrite it to under 140 lines now: keep one line per entry, move detail into topic files, and merge or drop stale entries.
```

The original near-limit “milder reminder” is unpublished.

### 4.3 Official trigger patterns

- **Record as work proceeds, assuming interruption at any time**: the memory-tool protocol requires “As you make progress, record” and “ASSUME INTERRUPTION”. Near its clearing threshold, context editing gives "Claude receives an automatic warning to preserve important information", allowing Claude to save information to memory first (https://platform.claude.com/docs/en/build-with-claude/context-editing ).
- **Three stages for multi-session projects: initialization / opening read / closing update** (current 2026 memory-tool page): "For software projects that span multiple agent sessions, set up memory files deliberately instead of writing them ad hoc as work progresses." → "Initializer session … Subsequent sessions: Each new session opens by reading those memory files. … End-of-session update: Before a session ends, it updates the progress log with what was completed and what remains." (This pattern concerns progress-oriented memory; ArcReel has decided to exclude progress from memory, so it is included only for comparison.)
- **Claude Code criterion**: do not write every session; decide "based on whether the information would be useful in a future conversation". Write when the user explicitly says “remember”.
- **System prompt in the official Anthropic SDK example** (https://github.com/anthropics/anthropic-sdk-python/blob/main/examples/memory/basic.py , code rather than documentation): "DO NOT just store the conversation history / No need to mention your memory tool or what you are writing in it to the user, unless they ask / Store facts about the user and their preferences / Before responding, check memory to adjust technical depth and response style appropriately / Keep memories up-to-date - remove outdated info, add new details as you learn them".

### 4.4 Alignment points for ArcReel `CLAUDE.<mode>.md` writing guidance

Combining the official wording above, creative-domain guidance can cover only the following aspects (the Spec will determine wording; this list includes only elements supported by official evidence):

1. **Criterion**: save only information useful in future conversations that the Agent cannot infer from project data / instruction files (memory page).
2. **What to remember**: creator preferences and corrections (aspect ratio, voiceover, style prohibitions, etc., corresponding to official `user` / `feedback`), decisions appearing only in conversation (`project`), and external pointers (`reference`).
3. **What not to remember**: production progress, one-time instructions, facts readable from project data such as project.json, rules already in `CLAUDE.<mode>.md`, and sensitive personal information (Claude.ai and Cookbook guidance).
4. **Form**: one line per index entry, details in topic files, timely consolidation of stale entries, and no new files unless necessary (memory page + reinforcing memory-tool instruction).
5. **Scope restriction**: use "Only write down information relevant to <topic>" to restrict scope to the creative domain (memory-tool page).
6. **Do not repeat existing native instructions**: the native system prompt already includes reading, writing, and organization instructions. Add only domain content to avoid the best-practices failure mode of overspecification.

---

## 5. Question 4: official precedents for user-visible / editable memory

### 5.1 Claude Code `/memory`

> The `/memory` command lists your CLAUDE.md, CLAUDE.local.md, and other memory file locations across user and project scopes … It also lets you toggle auto memory on or off and provides an option to open the auto memory folder. Select any file to open it in your editor; selecting one that doesn't exist yet creates it first. To check which files actually loaded into the current session, run `/context`. (memory page)

> Auto memory files are plain markdown you can edit or delete at any time. Run `/memory` to browse and open memory files from within a session. (same page)

> Run `/memory` and select the auto memory folder to browse what Claude has saved. Everything is plain markdown you can read, edit, or delete. (same page)

Interaction: a file list + opening the entire file in an external editor (since v2.1.216, no longer waits for the editor to close). Filenames in "Saved N memories" notifications can be clicked to open them (CHANGELOG 2.1.86). No per-entry UI or per-entry confirmation.

### 5.2 Official principles when a user edits Agent-written files

The claude-directory description of `MEMORY.md` is the only direct statement:

> Claude creates and updates this file as it works; you do not write it yourself. It acts as an index that Claude reads at the start of every session, pointing to topic files for detail. You can edit or delete it, but Claude will keep updating it.

Topic files: "You never create these yourself. Claude reads a topic file back only when the current task relates to it."

Related facts: after a user edits a file, Claude adds the `modified` field as usual on its next write ("Any file that has frontmatter gets the field the next time Claude writes it"); files remain until "you or Claude edits or deletes them". There is **no** promise of merge protection such as respecting user changes or not restoring user-deleted entries; unverified.

### 5.3 Individually editable Claude.ai memory (since 2026-08-25)

> See exactly what Claude remembers about you in Settings > Memory. Everything Claude remembers is listed under Topics. Select any topic to read it, then use the edit icon to change it or select "Delete" to remove it. Fix something in one topic and the change applies to every conversation from then on.（11817273）

> You can also update memory directly from a chat. Tell Claude what you'd like it to remember, change, or forget, and the update applies to your next conversation. (same source)

Settings also provides a natural-language edit box: "Tell Claude what to change or remove" (https://support.claude.com/en/articles/12123587-import-and-export-your-memory-from-claude ). Pause retains existing memory but stops reading and writing it; Reset permanently deletes all memory including project memory and cannot be undone. Deleting a conversation does not delete the memory entries it produced (new version), but entries can be deleted individually. Team / Enterprise Owners control the toggle; "Owners can't view or edit a user's individual memories"; disabling memory for the organization means "all existing memory entries for all users are deleted immediately".

There is likewise **no** promise that Claude will not overwrite user edits; release notes say "entries that Claude reads and updates during your conversations" (2026-07-10).

### 5.4 Mapping precedents to ArcReel

| Settled ArcReel decision | Official precedent |
| --- | --- |
| Directory file list + full-text Markdown editing | Claude Code `/memory`: file list + opening the entire file for editing |
| Second confirmation for “Clear” at each level | Claude.ai Reset memory: "cannot be undone", requiring another click to confirm |
| Agent writes autonomously; users can edit; no per-entry confirmation | Both agree; both allow users to edit and Claude to continue updating afterward |
| User memory / project memory levels | Claude Code user / project scopes; Claude.ai isolation between cross-conversation memory and project memory |

---

## 6. Unverified points

- Original auto-memory system prompt injected into the main Claude Code conversation (officially unpublished), and original near-limit “milder reminder”.
- Merge / protection principles when user edits conflict with Claude writes (neither Claude Code nor Claude.ai states them).
- `CLAUDE_MEMORY_STORES` (team memory stores), mentioned in CHANGELOG 2.1.172, has no documentation-page explanation.
- Publication dates of the memory-tool documentation and former claude-code-best-practices blog post; the Cookbook memory page’s stated date conflicts with its content.
- Neither Claude Code nor Claude.ai explicitly says “do not remember one-time requests”; it can only be inferred from the criterion of future-conversation usefulness.

## 7. Sources

Claude Code documentation: memory, best-practices, settings, settings-reference, commands, sub-agents, errors, claude-directory, glossary, context-window (all https://code.claude.com/docs/en/… ); CHANGELOG https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md .
Agent SDK documentation: overview, sessions, modifying-system-prompts, claude-code-features, hosting, subagents (https://code.claude.com/docs/en/agent-sdk/… ).
Platform documentation: memory-tool, context-editing, compaction (https://platform.claude.com/docs/en/… ); Cookbook tool-use-memory-cookbook, tool-use-context-engineering-context-engineering-tools.
Engineering / research articles: effective-context-engineering-for-ai-agents (2025-09-29), building-effective-agents (2024-12-19), effective-harnesses-for-long-running-agents (2025-11-26), research/long-running-Claude (2026-03-23).
Claude products: https://claude.com/blog/memory (2025-09-11, including the 2025-10-23 update), https://claude.com/blog/claudes-memory-works-everywhere-and-you-decide-whats-in-it (2026-08-25), https://claude.com/blog/context-management (2025-09-29); Help Center 11817273, 12123587, 12260368 (incognito), 12138966 (release notes), 14554000.
Code: https://github.com/anthropics/anthropic-sdk-python/blob/main/examples/memory/basic.py .

---

## 8. Draft GLOSSARY.md entries

Proposed addition to the “Agent runtime” section before “SDK transcript”. The existing “Instructions” entry’s statement that Agent memory carries long-term preferences can remain unchanged and point to this entry. Include only meaning and Avoid, without implementation.

```markdown
**Agent memory (Agent 记忆)**:
Natural-language notes about the creator and project, persisted across sessions and autonomously written by the Agent, which creators can view and edit. Save only preferences, corrections, decisions, and external pointers that the Agent cannot infer from project data or instruction files and that remain useful in future conversations. There are two levels: project memory and user memory.
_Avoid_: Treating formal project data, SDK transcripts / session event logs, one-time instructions, or production progress as memory; calling human-authored instruction files such as `CLAUDE.<mode>.md` memory.

**Project memory (项目记忆)**:
Agent memory that travels with a project and applies only to that project’s sessions, carrying its stylistic direction, confirmed approaches, and the creator’s corrections for that project.
_Avoid_: Reusing it across projects; writing it into formal project data; confusing it with project settings.

**User memory (用户记忆)**:
Agent memory belonging to a user and applying to all of that user’s projects, carrying the creator’s background and long-term working preferences.
_Avoid_: Binding it to a service instance or host directory; writing content that applies only to one project.
```
