# ArcReel Agent Runtime Profile — Refactored Target State

This directory is a complete target-state replacement for `agent_runtime_profile`, based on repository commit `6883b9fd4cfc546c68c3f84c5c4f0c81edab677e`.

The profile is written around five rules:

1. The server owns workflow facts; agent documents own the execution process.
2. One meaning has one authoritative home.
3. Normal paths stay in the main document; exceptional branches are disclosed through pointers.
4. Every operation ends on an exhaustive, machine-checkable completion criterion.
5. Generated artifacts are current only when their provenance matches current inputs.

## Deployment order

This profile expects the interfaces and state semantics in `docs/agent-runtime-code-change-contract.md`. Release the code changes and this profile together. The most important dependency is `mcp__arcreel__get_workflow_status`; without it, the workflow skill has no authoritative state source.

## Main structural changes

- Three large `CLAUDE.<mode>.md` files become one common `CLAUDE.md` plus a projected `.claude/references/workflow-mode.md`.
- Three duplicated `manga-workflow/SKILL.<mode>.md` files become one common workflow skill.
- `generation-modes.md` is split into routing, completion, edit, draft-repair, and duration-confirmation references.
- The generic `generate-assets` subagent is renamed to `run-generation-task` so the name identifies one behavior.
- Provider-specific prompt prose that is already implemented in code is removed from the runtime profile.
- Evals assert current MCP behavior instead of obsolete CLI flags.

Top-level `README.md` is intentionally not synchronized into projects by the existing profile manifest logic.
