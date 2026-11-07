---
status: accepted
---

> Three details are superseded by `docs/adr/0088`: user memory at `<数据根>/.arcreel/users/…`, `_`-prefixed data-root directories meaning shared cross-project material libraries, and deriving user memory from `projects_root` + `user_id` (after separating data root and project directories, derive it from data root instead). Other decisions remain valid.

# Two-level Agent memory: redirect native auto memory for projects and build user memory under data root

The embedded creation Agent has project and user memory, but Claude Code auto memory recognizes only one directory per session, so one tier must be custom. Use **native auto memory for project memory**, redirected to `<项目目录>/.arcreel/memory/`; implement **custom user memory** at `<数据根>/.arcreel/users/<user_id>/memory/`. During session assembly, inject its `MEMORY.md` index into the append prompt using the same truncation rules as native memory, and let the Agent write with Write/Edit. Native derivation, background extraction, and index-overflow warnings are cwd-oriented and most benefit frequently changing project memory; user memory is small and stable, needing only an injected index.

**Redirect through the flag-settings layer of `ClaudeAgentOptions.settings`**, injecting the absolute `autoMemoryDirectory` per session, without materializing settings files or changing `CLAUDE_CONFIG_DIR` and HOME. If init-message `memory_paths.auto` differs from expectations, log an error and still open the session rather than failing hard.

**Do not use `.claude/`**: the project's `.claude/` is its profile materialization tree, removed entirely by manifest mismatches and Restore built-in profile; memory there would silently disappear. `.arcreel/` is the existing internal-state directory. Validators skip hidden directories and archives copy only visible items, so memory naturally stays outside archives and unknown-directory reports. `_`-prefixed data-root directories mean shared cross-project material libraries, a different meaning; therefore do not use `_users/`.

## Rejected alternatives

- **Native user memory and custom project memory**: symmetrically possible, but native background extraction favors current work and would mix project details into cross-project notes.
- **Both custom, disabling native memory**: loses native writing instructions and index warnings, leaving the largest gap.
- **Materialize `.claude/settings.local.json` for `autoMemoryDirectory`**: project-varying paths conflict with sha256 manifest detection of unmodified built-ins, and whether SDK sessions adopt project settings is unverified.
- **Change `CLAUDE_CONFIG_DIR` by user**: moves transcripts, `~/.claude.json`, and user settings, far beyond memory scope.
- **Migrate existing `~/.claude/projects/<repo>/memory/` under repository slugs**: mixed developer notes cannot be assigned to projects reliably and are only a development-machine phenomenon.

## Consequences

- `AgentAccessPolicy` marks both memory directories readable/writable for the Agent: hooks allow Read/Write/Edit/Glob/Grep. Do not add kernel sandbox write allowances for user memory: native tools bypass it and need no such setting; `allowWrite` applies only to Bash, whose writable bwrap bind mount overrides `users/` `denyRead`. Without that allowance, Bash cannot read or write user memory. Project memory remains inside cwd and Bash accesses it normally. Policy derives user memory purely from `projects_root` + `user_id`; pass `user_id` per call alongside `project_cwd`, keeping policy a process singleton.
- Subprocess HOME, `CLAUDE_CONFIG_DIR`, and the `claude_projects_dir` tool-results read exception are unchanged.
- Subagent `memory: "project"` lives at `<cwd>/.claude/agent-memory/` and is also removed by profile reset; subagent memory-scope decisions must consider this separately.
