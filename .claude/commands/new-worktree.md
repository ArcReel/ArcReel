---
name: "New Worktree"
description: Create an isolated git worktree and automatically sync local configuration files (settings.local.json, .env, .vscode/) and link the projects/ directory
category: Workflow
tags: [git, worktree, setup]
---

Create an isolated workspace and sync local environment files to the new worktree upon completion.

**Input**: optionally specify a branch name (e.g., `/new-worktree feature/auth`). If not specified, infer from conversation context.

**Announce at start:** "Using the new-worktree command to create an isolated workspace."

---

## Steps

### 1. Determine Branch Name and Base Ref

If the user provided a branch name, use it; otherwise infer from the conversation context (e.g., a feature being discussed).
If the user specified a base ref (e.g., remote branch `origin/feature/xxx`), pass it as the second argument.

### 2. Execute Script

```bash
bash scripts/new-worktree.sh <branch-name> [base-ref]
```

The script automatically:
- Creates a worktree at `.worktrees/<branch-name>`
- Syncs .claude/settings.local.json, .env, .vscode/
- Links the projects/ directory (symbolic link, shared data)
- Installs Python and frontend dependencies

### 3. Validate Baseline (Optional)

After the script completes, run tests to confirm the worktree starts clean:

```bash
cd <worktree-path> && uv run python -m pytest --tb=short -q
```

If tests fail: report the failures, ask whether to continue or investigate first.

### 4. Report Results

```
Worktree ready: <full path>

Synced files:
  ✓ .claude/settings.local.json
  ✓ .env
  ✓ projects/ (symbolic link, shared data)
  ✓ .vscode/

Test baseline: passed (N tests, 0 failures)
Ready to implement <feature-name>
```

---

## Quick Reference

| Situation | Action |
|------|------|
| Worktree directory | Fixed at `.worktrees/` |
| Source file/directory does not exist | Skip silently; note in report |
| Tests fail | Report failures + ask |
