---
status: accepted
---

> The declaration-description authoring language is superseded by the [maintenance-language policy](../../AGENTS.md#maintenance-language) and the [English translation initiative](../plans/english-translation/README.md): descriptions are now authored in English. This changes only authoring language, preserving the single-source architecture, schemas, IDs, and policies described below.

# One Agent tool declaration source; only two host differences

Each tool has one host-independent declaration: name, full Chinese description (parameter descriptions on request-model fields), Pydantic-derived schema, required migration policy without default, domain key, and handler. Thin adapters project it to ArcReel/external registration/envelopes. Differences are limited to project selection and long-task waiting. For existing-project tools, embedded sessions select projects without schema `project`, while external Agents specify them. `list_projects`/`create_project` have no targets and neither exposes `project`. Embedded long tasks wait for terminal results; external calls return handles immediately, with polling instructions derived from flags. Everything else shares one source: adapters do not rewrite problem codes or introduce separate domain keys, and they neither add nor remove result fields. One `ToolOutcome` encodes identical JSON, embedded summaries accompany JSON rather than replacing it. Long tasks return different-phase `ToolOutcome`s; equality applies when external polling reaches terminal results, whose generation payload matches embedded results.

Separate host declarations drifted within a month: errors rewritten to `internal_error`, duplicate domain keys, three migration-error forms, four missing policies, and tests attached to fields discarded by SDK envelopes.

## Considered Options

- **Optimize hosts separately** (short English external descriptions or summary-only embedded results): every special case invites drift while both serve the same creative Agents with no contract reason to differ.
- **Share handwritten `MIGRATION_BLOCKED_TOOL_IDS`**: no force over new tools. Required declaration policies enforce ADR 0062's entry-declared blocking.

## Consequences

- Test domain behavior once at handler (`ToolOutcome`) level. Adapters have table-driven host-consistency tests and a few envelope tests, without domain assertions.
- Derive `ARCREEL_MCP_TOOL_IDS` and migration-blocked sets, retaining names for profile lint/frontend display-name tests.
- Derive migration-rejection description suffixes from policies, not declaration prose. Both hosts share the derivation function, so stored descriptions differ from final descriptions.
