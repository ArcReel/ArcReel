# ArcReel

An AI video creation platform that turns novels, screenplays, or creative ideas into short videos. Three layers: `frontend/` (React SPA) → `server/` (FastAPI, with `agent_runtime/` wrapping the Claude Agent SDK) → `lib/` (core library). The embedded creation Agent's configuration source is `agent_runtime_profile/`, separate from the development `.claude/` directory. uv workspace subpackages live under `lib/` and do not depend on the main repository: market-source tools and the endpoint-definition validator are in `packages/arcreel-market-core/` (`arcreel_market_core`); model-catalog data formats and validators are in `packages/arcreel-catalog-core/` (`arcreel_catalog_core`).

## Toolchain and validation

The backend uses `uv`; the frontend and documentation site use `pnpm`. When changing code or tests, first select and run the relevant tests according to `docs/agents/testing.md`. Before pushing, run the complete gates. In team workflows, the role responsible for pushing runs them:

```bash
uv run python scripts/gate.py --changed            # Select domains by changes relative to origin/main and run them together
uv run python scripts/gate.py backend frontend     # Or name domains explicitly; see --list for trigger paths and steps
```

If another gate run is active, this one queues; waiting is normal. Relevant tests must actually run and pass. Before opening a PR, check whether a mistake in this work warrants improving the environment; criteria and recording instructions are in `docs/agents/lessons.md`, “Closing self-check.” For new or upgraded dependencies, read `docs/agents/dependencies.md`. Development servers, database migrations, and branch and commit conventions: `CONTRIBUTING.md`.

## Code Review Rules

When reviewing a diff, use the index in `CODING_STANDARDS.md` to read the standards matching the changed paths. Report violations with the file and rule heading.

## Architecture

Architecture overview, adding providers, and extending workflow stages: `website/docs/dev/architecture.md`.

## Maintenance language

Scope and compatibility rules: [ADR 0121](docs/adr/0121-english-maintenance-language-preserves-localization-and-contracts.md).

Write repository-owned maintenance sources, development instructions, comments and built-in Agent instruction prose in English. Preserve functional literals, historical compatibility syntax, localized resources and user content. This does not override explicit output-language requirements in existing workflows or change supported languages. Public documentation retains its existing Chinese-source-to-English translation workflow.

## Agent skills

- User escalation: before asking the user a question or requesting a decision, and when handling findings outside the current scope, read `docs/agents/escalation.md`.
- Issue tracking: GitHub Issues, operated with the `gh` CLI. Spec and ticket conventions: `docs/agents/issue-tracker.md`.
- Triage label state machine: `docs/agents/triage-labels.md`.
- Using domain documentation (`GLOSSARY.md` + `docs/adr/`): `docs/agents/domain.md`.
- Project schema migrations: when adding or changing a migration step in `lib/project/project_migrations/`, or changing the artifact backfill planner, read `docs/agents/project-migrations.md`.
