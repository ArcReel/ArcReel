---
id: contributing
title: Contributing Guide
sidebar_position: 2
custom_edit_url: https://github.com/ArcReel/ArcReel/blob/main/CONTRIBUTING.md
---

# Contributing Guide {#contributing}

Contributions of code, bug reports, and feature proposals are welcome!

## Local Development Environment {#local-development}

```bash
# Prerequisites: Python 3.12+, Node.js 20+, uv, pnpm, ffmpeg
# The documentation site website/ also needs Node 24 (pinned in website/.node-version)
# Operating system: Linux / MacOS / Windows WSL2 (native Windows is unsupported)

# Install dependencies
uv sync
cd frontend && pnpm install && cd ..

# Install the pre-commit hooks once (ruff / eslint / pull_request_target tripwire)
uv run pre-commit install

# Initialize the database
uv run alembic upgrade head

# Start the backend (terminal 1)
# Note: --reload-dir is required to limit the watched directories; otherwise watchfiles
# scans node_modules / .venv / .git / .worktrees and hundreds of thousands of files, costing 50%+ of one core
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241

# Start the frontend (terminal 2)
cd frontend && pnpm dev

# Open http://localhost:5173
```

### Documentation Site {#docs-site}

`website/` is a separate package root with its own lockfile and is not grouped into a workspace with frontend:

```bash
cd website && pnpm install

pnpm start        # Development preview
pnpm build        # Dual-locale build; a broken link or anchor fails outright
pnpm typecheck

# Site search only works against build output, not in the dev server
pnpm build && pnpm serve

# Sync the repo-root CONTRIBUTING.md into the docs-site page (start / build already run this automatically, so a manual run is rarely needed)
pnpm sync-contributing

# CI consistency gate: page inventory / orphan translations / docs-site headings missing an explicit anchor / UI JSON key completeness — a non-zero exit on any hit;
# it reads output already synced by sync-contributing, so run sync-contributing first
pnpm check-consistency
```

## Testing {#testing}

```bash
# Backend tests; single file: uv run python -m pytest path/to/test.py, -k to filter by keyword, -v for verbose output
uv run python -m pytest

# Frontend typecheck + lint + tests
cd frontend && pnpm check
```

pytest runs with `asyncio_mode = "auto"`; async tests need no manual marking.

> **Transition note**: this chapter describes the target state after remediation; the existing suite and the related engineering configuration are being aligned with it in batches under a remediation spec. Where a rule does not match the current tree (existing test directories and tiers, frontend calls that bypass the `API` class with direct `fetch`/`EventSource`, the current CI/lint configuration for coverage and the eslint-enforced rules, vitest settings such as `testTimeout`, the not-yet-created `src/test/` shared infrastructure), the chapter is the direction of travel. `scripts/audit_tests.py` and the CI `test-lint` step do not exist yet; they land with the first gate, and each gate ships in the same PR that clears its remaining violations. Until the directory migration and automatic marker injection land, classification markers are still written by hand (collection enforces exactly one; semantics per the table below). Delete this note once remediation completes.

### Tiers and layout {#test-tiers}

Every backend test belongs to exactly one tier; CI runs `-m "not e2e"` by default:

| Tier | Meaning | Boundary |
|---|---|---|
| `unit` | Fast and isolated | No real DB, subprocesses, or network; `tmp_path` local filesystem is allowed |
| `integration` | Real cross-module collaboration | Real DB, filesystem, ffmpeg subprocesses; anything touching a real DB (including all alembic migration tests) belongs here |
| `e2e` | End-to-end | Depends on real external services (remote APIs, LLM calls); skipped in CI by default, run locally when needed |

- The layout is `tests/unit|integration|e2e/<mirror of the top-level source package>` (such as `tests/unit/lib/…`, `tests/integration/server/…`). Tier markers are injected automatically by conftest based on the path—never written by hand; `uses_db` combined with `unit` fails at collection time. Mirror correctness relies on review; there is no mechanical check.
- alembic migration tests keep one file per migration script, under `tests/integration/lib/db/migrations/`, sharing that directory's conftest `alembic_cfg`; migration tests stay on SQLite, with the PostgreSQL side covered by the CI workflow's alembic upgrade/downgrade commands.

### File size and naming {#test-file-size}

- One test file corresponds to one subject under test; when a single subject outgrows readable size, split by behavior area using semantic topic suffixes.
- Two gates: the split-naming suffixes `_more` / `_full` / `_coverage` / `_extra` / `_additional` are forbidden; a 3000-line circuit breaker per file (it exists to stop unbounded file growth, not as a size standard).

### Test doubles {#test-doubles}

- **Priority**: real objects (in-memory SQLite, `tmp_path`) > handwritten doubles in `tests/fakes.py` (admission criteria in its module docstring) > `spec`/`autospec` Mocks > bare `MagicMock`/`AsyncMock`. Mocks may only replace repository boundaries (third-party SDKs, network transport, subprocesses, filesystem, clocks); collaborators inside the repository use real objects or handwritten fakes.
- **Never patch private symbols of production code** (gate, no exemptions): `patch("lib.x._y")`, `monkeypatch.setattr(mod, "_y")`, and `patch.object(Cls, "_y")` are all forbidden. Use a seam when internal behavior must be controlled.
- **A seam is explicit parameter injection**: a constructor or keyword parameter with a production default and no behavior change, such as `retry_async(operation, *, clock=..., jitter=...)`; never a module-level replaceable global. Applies to: polling clocks/intervals/backoff, capability resolvers, HTTP probe clients, filesystem and subprocesses.
- **Assert outbound HTTP with respx**: keep the real httpx client and intercept at the transport layer (`AsyncOpenAI` traffic is captured the same way), asserting the actually serialized request.
- **Patch consolidation** (gate): a patch target string appearing in ≥3 test files must be consolidated into a shared fixture / helper, no longer written inline per file; for FastAPI route dependencies prefer `app.dependency_overrides` over patching.

### Meaningless-test criteria {#meaningless-tests}

Meaningless tests are negative value; deletion beats retention. Three mechanical criteria (gate):

1. **All assertions land on double call records**—the test's subject has become the mock itself. Assert real output instead: the returned object's type and attributes, the real request captured by respx, or observable state.
2. **Patching the unit under test's own logic steps and then testing that unit**—an integration test mocking the public entry point of the module under test is a special case of this.
3. **Zero-assertion tests**—if you can say what to protect, add the assertion; if you cannot, delete the test.

On a hit, disposal follows three fixed steps, with no per-case discretion: ① the behavior is already substantively covered by another test → delete, with no coverage compensation; ② uncovered but not worth protecting (no real branch or contract) → delete; ③ worth protecting → rework, and cases needing a production seam move to the seam-consolidation batch.

Four audit criteria rely on review and dedicated audits, not gates: weakened duplication (same path as another test with weaker assertions), over-specification (asserting log text, dict key order, private attributes, and other implementation details rather than contracts), severe setup-to-assertion imbalance, and assertion helpers that only assert double call records internally.

### Shared infrastructure {#shared-test-fixtures}

- **Three roles**: `tests/conftest.py` holds only fixtures and collection-time hooks and must never be imported (gate); `tests/fakes.py` holds double implementations, no fixtures; `tests/factories.py` holds test input builders (data and media file builders). Topic-specific shared modules (such as `tests/auth_deps.py`) are allowed; every public symbol in fakes / factories / topic modules must be used by ≥2 test files (gate)—single-file symbols move back into that file.
- **Local conftest**: provides fixtures for its own directory only; must not share a fixture name with the root conftest; fixtures shared across directories move up to the root conftest; conftests never import each other (gate).
- **Fixture overriding and duplication** (gate): a test file must not define a fixture with the same name as any conftest fixture; a fixture name defined in ≥3 test files moves up to a conftest.
- **DB fixtures**: all derive from the single dialect-aware engine fixture, thereby automatically receiving the `uses_db` marker and inclusion in the PostgreSQL compatibility selection.

### Timing and flakiness {#timing-and-flakiness}

- Waiting, retry, and timeout logic is always driven through a clock seam or event handshake—no real `time.sleep` wall-clock waits.
- Flaky failures are ordinary defects: fix them in place (clock seam / event handshake), or delete them under the meaningless-test criteria if a fix is impractical or not worthwhile. No automatic retries (pytest-rerunfailures, CI job-level retry)—automatic retry hides failures that should stay visible.
- Probabilistic stress tests (real concurrency + real time) must be explicitly registered in this section. The sole registered exemption: the atomic-write stress test in `tests/test_project_manager_concurrent_save.py` (`integration` tier).

### Coverage {#coverage}

Coverage is a signal, not a gate: CI never fails on a coverage number, and Codecov is the sole signal carrier (PR coverage comments and trend graphs, all statuses informational). Never write tests for a coverage number; deleting meaningless tests is allowed to lower coverage.

### Gates {#test-gates}

- Single entry point: `uv run python scripts/audit_tests.py --check`, the same command locally and in CI (a standalone `test-lint` step); output is `rule-id file:line fix guidance`.
- Zero tolerance: the violation count is always 0—no baseline, no ratchet, no exemption annotations; a script false positive is fixed in the script, never by tagging the test as an exception; a new rule ships in the same PR that clears its existing violations.
- Division of labor: the AST script owns code structure; pytest collection time does only tier-related checks; no new runtime checks. Frontend semantic rules belong to eslint; structural rules are scanned by the same script over `frontend/src/**/*.test.*`.

### Frontend tests (vitest) {#frontend-vitest}

- **API stubbing**: `vi.spyOn(API, method)` is the standard stubbing boundary (the `API` class is the frontend's only outbound gateway); tests of `api.ts` itself use handwritten fetch/Response stubs; msw is not introduced. Whole-module `vi.mock("@/api")` and `vi.mock("react-i18next")` are forbidden (enforced by eslint; the global setup already loads the real Chinese i18n, and mocking it hides missing translations).
- **SSE stubbing**: use the shared `FakeEventSource` in `src/test/`, returned from a spy on `API.openProjectEventStream`.
- **Mocking internal child components** requires one of three categories: heavyweight (virtualization/animation/canvas), side-effecting (sends requests/starts timers), or pure display irrelevant to the test; a component mocked in ≥3 files moves up to `src/__mocks__/`.
- **Shared infrastructure**: cross-cutting utilities unrelated to the subject under test (`createDeferred`, `FakeEventSource`, factories) appearing in ≥3 files move up to `src/test/`; API spy combinations are exempt from consolidation (each file spies a different combination of methods; there is no common shape to extract); local `renderXxx` helpers move up only when ≥3 files repeat the same shape.
- **Layout and size**: test files sit next to their source files, without `__tests__/` directories; "one file, one subject", the split-naming ban, and the 3000-line circuit breaker match the backend, with semantic topic suffixes allowed (such as `ShotDetail.drama.test.tsx`).
- **Testability rework**: no production behavior changes; structural extraction at the pure-function or hook level is allowed.
- **Configuration and lint**: `testTimeout` stays at the vitest default of 5s, with individual slow tests overriding it explicitly with an explanation; eslint enables the vitest, testing-library, and jest-dom plugins (`expect-expect` catches zero-assertion tests); bare `toHaveBeenCalled` has no ban—assertion strength is a review concern.

## Code Quality {#code-quality}

**Lint & Format (ruff):**

```bash
uv run ruff check . && uv run ruff format .
```

- Rules: `E`/`F`/`I`/`UP`, with `E402` and `E501` ignored
- line-length: 120
- Enforced in CI: `ruff check . && ruff format --check .`

**Lint (frontend ESLint):**

```bash
cd frontend && pnpm lint          # Check
cd frontend && pnpm lint:fix      # Auto-fix what can be fixed
```

- Configuration: `frontend/eslint.config.js` (flat config)
- Rules: `typescript-eslint/recommendedTypeChecked` + `react/recommended` + `react-hooks/recommended` + `jsx-a11y/recommended`
- Typed linting enables `projectService: true`, allowing async-related checks such as `no-floating-promises` and `no-misused-promises`
- Enforced in CI: the `frontend-tests` job's `Lint` step

### ESLint disable conventions {#eslint-disable-policy}

The project has followed a zero-warning policy since PR 3 (#219); every rule is an error. If a rule must be bypassed, follow these conventions:

- **Form**: `// eslint-disable-next-line <rule> -- <中文理由>`; the reason after `--` is **required**
- **Forbidden**: file-level `/* eslint-disable */`, `// eslint-disable-line` without a reason, and combined use with `@ts-ignore`
- **PR description requirement**: every new disable must be listed in the PR body as a table with `rule | file:line | 理由`
- **File-level disabling** is allowed only through an `eslint.config.js` `files` override, with the reason documented in a config comment
- **Unacceptable reasons**: "too much trouble," "leave it like this for now," or "later fix"
- **Acceptable reason examples**: "React setter reference is stable," "mount-only initialization," or "generated preview video has no subtitle source"

**Local IDE recommendation (do not commit to the repository):**

`.vscode/` is already in `.gitignore`. Add `frontend/.vscode/settings.json` locally to make VS Code / Cursor show lint warnings in real time and apply automatic fixes when saving:

```json
{
  "eslint.workingDirectories": [{ "pattern": "./frontend" }],
  "editor.codeActionsOnSave": { "source.fixAll.eslint": "explicit" }
}
```

**Known constraint:**

- TypeScript version lock: the peer range of `typescript-eslint@8.x` is `typescript <6.1`; upgrade `typescript-eslint` before upgrading TypeScript to 6.1+

## Documentation Maintenance {#docs-maintenance}

The only published location for user documentation is [docs.arc-reel.com](https://docs.arc-reel.com/en/); source files live in `website/docs/` (see "Documentation Site" above for local builds and previews). Chinese is the sole authoring source; English translations are generated by AI, and humans review only the Chinese source. Internal documentation (ADRs, `CONTEXT.md`, `AGENTS.md`, the security threat model, provider API documentation indexes, and so on) is not published on the site and remains under the repository's `docs/` directory. `SECURITY.md` also remains in the repository root because the GitHub Security tab depends on it.

This file is the source of truth for the contributing guide. During builds, it is copied to the site's development section (`website/scripts/sync-contributing.mjs`); the Chinese copy is not committed.

### Page responsibilities {#page-responsibilities}

| Page | Should contain | Should not contain |
|---|---|---|
| `README.md` | Product positioning, core value, and the shortest path to getting started | A complete model list, every environment variable, or internal implementation details |
| `website/docs/index.mdx` | Documentation-site positioning, primary entry points, and a navigation overview | Complete instructions for specific features |
| `website/docs/guide/getting-started.md` | The complete path from deployment to the first generated video | Production-grade reverse proxy and backup strategies |
| `website/docs/guide/workflows.md` | Content modes, video generation routes, review checkpoints, and selection guidance | Provider credentials and operations commands |
| `website/docs/guide/providers.md` | Provider types, capability coverage, selection principles, and configuration hierarchy | Price promises likely to become outdated |
| `website/docs/guide/jianying-export.md` | Locating the Jianying draft directory, exporting, and further editing steps | The video generation process itself |
| `website/docs/guide/faq.md` | Frequently asked questions and short answers | Long tutorials |
| `website/docs/ops/deployment.md` | Deployment, upgrades, backup, recovery, monitoring, and security | Product marketing copy |
| `website/docs/ops/migrate-to-postgres.md` | SQLite-to-PostgreSQL migration, verification, and rollback steps | Day-to-day PostgreSQL deployment and operations guidance |
| `website/docs/dev/architecture.md` | Stable architectural boundaries, data flows, and extension points | Temporary implementation plans and incomplete designs |
| `SECURITY.md` | Supported versions, supported deployment boundaries, private vulnerability reporting, and coordinated disclosure policy | Details of unfixed vulnerabilities and dynamic risk registers |
| `docs/security/threat-model.md` | Security assets, trust boundaries, attack surfaces, existing controls, and reassessment triggers | Directly exploitable unfixed vulnerabilities and patch history |

### Writing conventions {#writing-conventions}

- **Keep the README stable**: the README only needs to help a first-time repository visitor answer, "What is ArcReel, is it right for me, how is it different from calling a model API directly, and what is the fastest way to run it?" Put specific model names, prices, and API parameters on the corresponding site pages so that the homepage does not need to be rewritten every time a provider changes.
- **Treat runtime capabilities as authoritative for provider information**: documentation describes the media types covered, how ArcReel unifies configuration, how to choose between different capabilities, and where to confirm specifics; the models actually selectable on the Settings page and the provider's official documentation are definitive.
- **Give headings explicit anchor IDs**: write every heading on a published page as `## 标题 {#english-id}`. The Chinese and English locales share the same anchor to prevent changes to copy from invalidating automatically generated Chinese slugs. Use relative file paths for cross-references within the site (such as `../ops/deployment.md`), and use absolute GitHub links when pointing to repository files not published on the site.
- **Commit documentation changes with feature changes**: when adding a content mode or video generation route, adding a provider or media capability, or changing deployment directories, ports, environment variables, data directories, backup methods, migration behavior, public APIs, licenses, or commercial-use terms, update the corresponding documentation at the same time.
- **No JSX or import in docs-site `.md` files**: `website/docusaurus.config.ts` sets `markdown.format: "detect"`, so `.md` files are parsed as CommonMark rather than MDX. Neither raises a compile error, and neither is executed as MDX: a JSX tag is output verbatim as raw HTML (a tag with children leaks that content directly onto the page), and an import statement is displayed verbatim as page text. Use `.mdx` for pages that need JSX.

## Workflow {#workflow}

### Branch strategy (trunk-based) {#branching-strategy}

- `main` is the only long-lived branch. Complete all work on short-lived branches created from the latest `main`, then merge them back into `main` through a PR
- Never push directly with `git push origin main`. Even personal branches use the PR workflow; review the diff and acceptance checklist yourself first

### Branch naming convention {#branch-naming}

Use `<type>/<slug>`, where `type` is one of the conventional commit types:

- `feat/` — New feature (for example, `feat/reference-video-backend`)
- `fix/` — Bug fix (for example, `fix/queue-lease-timeout`)
- `refactor/` — Refactoring (for example, `refactor/session-actor`)
- `docs/` — Documentation only (for example, `docs/contribution-infra`)
- `chore/` — Builds, tooling, version numbers, or cleanup (for example, `chore/freeze-versions`)
- `ci/` — CI configuration (for example, `ci/testing-discipline`)
- `test/` — Tests only

Use lowercase words separated by hyphens for `slug`, briefly describing the branch's focus.

### Short branch lifetime {#short-lived-branches}

The time from creation to merge must be ≤3 days. If it runs longer, split it or rebase it onto the main branch first—**do not** drag a one-month-old branch directly into review.

### Squash merge {#squash-merge}

Squash each PR into one commit when merging into `main`, with a conventional commit message (see the next section). Choose "Squash and merge" from the GitHub merge button.

## Commit Conventions {#commit-convention}

Commit messages follow the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
feat: 新增功能描述
fix: 修复问题描述
refactor: 重构描述
docs: 文档变更
chore: 构建/工具变更
```

## Release Process {#release-process}

Version numbers and the changelog are maintained automatically by [release-please](https://github.com/googleapis/release-please) (configuration in `.release-please-config.json`, workflow in `.github/workflows/release-please.yml`). **Developers do not need to bump version numbers manually**—only write compliant conventional commits.

### Workflow {#release-workflow}

1. Squash-merge the PR into `main` according to the conventional commits specification
2. release-please scans commits since the previous release and automatically opens or updates a Release PR titled like `chore(main): release X.Y.Z`, containing the next version bump and an updated `CHANGELOG.md`
3. Merging that Release PR automatically creates a `vX.Y.Z` tag and publishes a GitHub Release

### commit type → version increment {#commit-type-version-bump}

| commit type | Version increment | changelog |
|-------------|---------|-----------|
| `feat`      | minor   | ✨ 新功能 |
| `fix`       | patch   | 🐛 Bug 修复 |
| `feat!` / any type + `!` / footer containing `BREAKING CHANGE:` | **major** (minor when version <1.0.0) | ⚠️ BREAKING CHANGES (at the top of the changelog) |
| `perf` / `refactor` / `docs` / `revert` | No increment | Shown (⚡ / ♻️ / 📚 / ↩️) |
| `chore` / `ci` / `build` / `test` / `style` | No increment | Hidden |

> By default, only `feat` and `fix` (as well as breaking changes) trigger a version bump in release-please. Configuring `perf`/`refactor`/`docs`/`revert` with `hidden: false` affects only their presentation in the changelog; it does not make them trigger a patch bump. If an iteration contains only these commit types, no Release PR is produced until the next `fix`/`feat` commit arrives.

The fields in `pyproject.toml` and `frontend/package.json` named `version` are maintained automatically by release-please (see the `pyproject.toml` comment `# managed by release-please`) and are **read-only for developers**. `uv.lock` is also synchronized automatically by running `uv lock` in the release-please workflow on the Release PR branch. The actual version state is defined by the git tag and `.release-please-manifest.json`.

### commit examples {#commit-examples}

```
# New feature (minor bump)
feat(image-backends): 支持 OpenAI DALL-E 3 后端

# Bug fix (patch bump)
fix(queue): 修复任务 lease 超时后未正确归还的问题

# With a scope and a body
feat(grid): 支持 grid_12 布局

将多宫格分镜系统扩展到 12 宫格，适用于长篇剧集的批量预览。
```

**This repository does not use breaking-change markers.** The frontend and backend are released together, and the backend API does not make versioned compatibility guarantees—the bundled frontend evolves with each version, while external integrations (OpenClaw and others) fetch the latest contract at runtime through `/skill.md` rather than depending on a version number. When deleting or changing endpoints referenced by `public/skill.md.template`, update that template at the same time. Classify API changes normally as `fix`/`refactor`; do not add a `!` suffix or a `BREAKING CHANGE:` footer. To correct an incorrectly marked commit after it has been merged, edit that PR's description and append a `BEGIN_COMMIT_OVERRIDE`/`END_COMMIT_OVERRIDE` block. release-please then recalculates the changelog and version number according to the override (this requires squash merging, which this repository uses). The workflow runs only on pushes to main; after editing, wait for the next push to main or rerun the release-please workflow manually. During the 0.x stage, `bump-minor-pre-major` limits the version jump caused by an incorrect marker to minor, but does not correct the changelog.

The following syntax is documented only to help identify incorrect markers. There are two equivalent ways to mark a **breaking change**:

```
# Form 1: append ! after the type
feat(api)!: 移除 /api/v1/legacy 端点

# Form 2: a footer containing BREAKING CHANGE (more common; allows a multi-line description)
feat(auth): 统一 API Key 验证逻辑

BREAKING CHANGE: /api/v1/api-keys 的返回结构改为 { items: [...] }，
旧客户端需要适配。
```

In both forms, release-please will:
- Bump the version number to major; when the current version is <1.0.0, the `bump-minor-pre-major` configuration limits this to a minor bump
- Insert a separate **⚠️ BREAKING CHANGES** section at the top of the changelog, summarizing the description of each breaking change
- Keep the commit's regular entry under the corresponding type section (such as `✨ 新功能`)
