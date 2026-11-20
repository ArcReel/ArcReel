---
id: contributing
title: Contributing Guide
sidebar_position: 2
custom_edit_url: https://github.com/ArcReel/ArcReel/blob/main/CONTRIBUTING.md
---

# Contributing Guide {#contributing}

Contributions of code, bug reports, and feature proposals are welcome.

Contributions whose main purpose is to promote a commercial service (for example, adding an integration for one particular service, or adding service recommendations and links to the docs) do not go through the PR process, and such PRs will be closed. For collaboration, contact support@arc-reel.com.

Issues labeled `ready-for-agent` already have implementation notes and are implemented by the maintainers' coding Agent. An external PR will not get them done sooner; it only adds another round of review, so such PRs will be closed.

Before developing a new feature or making a large-scale change, consider opening an issue to discuss the proposal. This helps avoid spending time on a change that may be closed because it takes a different direction.

Before a PR can be merged, its author must sign the CLA. CLA Assistant posts the signing link as a PR comment.

ArcReel assumes that contributors develop with a coding Agent. `AGENTS.md` at the repository root (`CLAUDE.md` is a link to it) is the Agent's entry point; gate commands, test selection, and coding standards are all linked from there. This page covers only what contributors themselves need to know.

## Local Development Environment {#local-development}

```bash
# Prerequisites: Python 3.12+, Node.js 24.12.0+, uv, pnpm (ffmpeg ships with the Python dependency imageio-ffmpeg)
# The documentation site website/ pins its Node version in website/.node-version
# Operating system: Linux / macOS / Windows WSL2; native Windows can run project creation and basic workflows,
# but the Agent sandbox on Windows degrades to a command-prefix allowlist (see docs/adr/0026); WSL2/Docker is recommended for production

# Install dependencies
uv sync
cd frontend && pnpm install && cd ..

# Install the pre-commit hooks once (ruff / eslint / actionlint / zizmor)
uv run pre-commit install

# Initialize the database
uv run alembic upgrade head

# Start the backend (terminal 1)
# Note: --reload-dir is required to limit the watched directories; otherwise watchfiles scans
# node_modules / .venv / .git / .worktrees and hundreds of thousands of files, using over 50% of one CPU core
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241
# On native Windows, drop --reload: with reload enabled, uvicorn on Windows switches to an event loop
# that cannot create subprocesses, so the built-in Agent cannot launch Claude Code. Restart the backend manually after code changes
uv run uvicorn server.app:app --port 1241

# Start the frontend (terminal 2)
cd frontend && pnpm dev

# Open http://localhost:5173
```

### Documentation Site {#docs-site}

The documentation site in `website/` has its own `pnpm` lockfile. See `website/.node-version` for the Node.js version.

```bash
cd website
pnpm install --frozen-lockfile
pnpm start                 # Local preview
pnpm check                 # Type, format, translation sync, and site consistency checks
pnpm build                 # Build both locales and check links and anchors
```

Site search is available in the built site. Run `pnpm serve` after building to preview it.

## Testing {#testing}

For the development loop and the full gate before pushing, see "工具链与校验" (Toolchain and checks) in [`AGENTS.md`](https://github.com/ArcReel/ArcReel/blob/main/AGENTS.md) and [`docs/agents/testing.md`](https://github.com/ArcReel/ArcReel/blob/main/docs/agents/testing.md). CI runs the same set of checks.

### Frontend Page-Level Tests {#frontend-page-tests}

`frontend/e2e/` is a page-level Playwright suite. It opens each page region at five viewports from 1024×600 to 2560×1440 and checks that the document itself does not scroll, that clipped content remains reachable by scrolling, and that vertical scroll areas, including modal bodies, do not gain horizontal overflow. It also uses axe to check accessibility against WCAG 2.2 AA. The `frontend-e2e` CI job runs in the official Playwright image. Pull requests run the three viewports with screenshot baselines (`E2E_VIEWPORTS=screenshot`), and daily CI runs all five. Use the same image locally to match CI rendering.

```bash
cd frontend
pnpm e2e:server   # Terminal 1: start the Playwright run-server in Docker; the image version is taken from @playwright/test
pnpm e2e:remote   # Terminal 2: build the frontend and run the suite against the browser in the container
```

Without Docker, first run `pnpm exec playwright install chromium`, then run `pnpm e2e`. This approach renders on the local machine, so fonts and layout may differ from CI; the results inside the container are authoritative.

When running several copies of the suite on the same machine in parallel, use environment variables to offset ports and output directories. Defaults in parentheses apply when a variable is unset: `E2E_PORT` is the preview server port (4173), `E2E_OUTPUT_DIR` is the directory for failure screenshots and traces (`frontend/test-results`), and `E2E_SERVER_PORT` is the port the run-server is mapped to on the local machine (3000); `pnpm e2e:server` and `pnpm e2e:remote` read the same variable. With `CI=1`, change the directory of the generated HTML report with Playwright's own `PLAYWRIGHT_HTML_OUTPUT_DIR`. Screenshot baselines are generated with the default ports; after changing a port, screenshots that show the local address on the page are excluded from comparison.

- **Adding scenarios**: register them under `frontend/e2e/regions/` with `defineRegionScenarios`, stating the route to open, the readiness condition, and the states to probe. Scenarios focus on states that change the available height, such as expanding a panel or opening a popup.
- **Exemptions**: exempt decorative clipping layers with `data-overflow-ok="原因"`; the attribute value must state the reason. Regions that genuinely need horizontal scrolling (timeline tracks, code blocks, tag strips) use `overflow-x-auto`, which the probe recognizes by class name, so no extra exemption is needed.
- **Recorded API responses**: `pnpm e2e:record` starts the real backend with `uv` in a temporary data directory and records fixtures in `frontend/e2e/fixtures/recorded/`. It uses the catalog test fixture, a fixed date and version, and a demo project. It skips online catalog sync and `.env` loading. Rerun the recording when changing the API response shape or catalog fixture. `pnpm e2e:record:check` compares two runs with different system clocks, factory snapshots, and version numbers. CI runs this check in `e2e-record-drift` and the local gate runs it in `e2e-record`. Use a scenario's `api` field for stress variants such as long text or large lists.
- **Page screenshots**: after a scenario registers `screenshot`, comparison happens only when it runs with `E2E_SCREENSHOTS=1`, and screenshots are taken only at three viewports: 1024×600, 1440×900, and 2560×1440. The CI `frontend-e2e` job sets this variable, and the job fails when a screenshot differs or its baseline is missing. Baselines can only be generated and compared in the container: run with `pnpm e2e:server` and `pnpm e2e:remote`, regenerate new or intentionally changed screenshots by adding `--update-snapshots=changed`, and commit them after checking each one. Fonts rendered on the local machine differ from the container, so do not use it to generate baselines.

## Code Quality {#code-quality}

The entry point for coding standards is [`CODING_STANDARDS.md`](https://github.com/ArcReel/ArcReel/blob/main/CODING_STANDARDS.md): it maps changed paths to the domain standards under `docs/standards/`, and it contains only project conventions that require judgment and cannot be replaced by tooling. Both local reviews and the AI reviewer on PRs use it as their basis.

### Dependency Management {#dependency-management}

For the steps to add or upgrade a dependency, see [`docs/agents/dependencies.md`](https://github.com/ArcReel/ArcReel/blob/main/docs/agents/dependencies.md).

## Documentation Maintenance {#docs-maintenance}

The [documentation site](https://docs.arc-reel.com/en/) publishes the Chinese source from `website/docs/` and the English translations from `website/i18n/en/`. Local previews and builds copy the root `CONTRIBUTING.md` into the site. Update both locales in the same pull request when changing a published page, the README, this guide, or site UI copy. Follow the documentation maintenance instructions in [AGENTS.md](https://github.com/ArcReel/ArcReel/blob/main/AGENTS.md) to review and verify translations.

### Page Responsibilities {#page-responsibilities}

- **README** explains what ArcReel is for, shows publishable examples, and gives the shortest path to getting started.
- **User guides** explain how creators use ArcReel. The getting-started guide covers the first run, and topic guides cover full workflows and their limits.
- **Operations docs** cover deployment, upgrades, backup and recovery, and database migration.
- **Developer docs** help contributors set up a development environment, understand the architecture, and find extension points. Repository standards, the glossary, and ADRs provide engineering constraints and decision context.

### Writing Conventions {#writing-conventions}

Update the relevant documentation in the same pull request as the feature change. See the [user documentation standards](https://github.com/ArcReel/ArcReel/blob/main/docs/standards/docs.md) and [general coding standards](https://github.com/ArcReel/ArcReel/blob/main/docs/standards/general.md). Write Markdown headings in published pages as `## Heading {#english-id}` with the same anchor in both locales.

Before publishing screenshots, videos, or examples, remove credentials, internal addresses, personal information, and private project content. Confirm that any novels, brands, music, and other materials are cleared for publication. Show real ArcReel output and identify the currency and example whenever you list costs.

## Workflow {#workflow}

### Branch Strategy (trunk-based) {#branching-strategy}

- `main` is the only long-lived branch. Complete all work on short-lived branches created from the latest `main`, then merge them back into `main` through a PR
- Never push directly to `main`. Personal branches are merged through the PR process as well; review the diff and acceptance checklist yourself before submitting

### Branch Naming Convention {#branch-naming}

Use `<type>/<slug>`, where `type` is one of the conventional commit types:

Short-lived run branches of the AFK team workflow are the exception: they use `afk/<batch-id>/stage-<K>` and `issue/<N>`.

- `feat/` — New feature (for example, `feat/reference-video-backend`)
- `fix/` — Bug fix (for example, `fix/queue-lease-timeout`)
- `refactor/` — Refactoring (for example, `refactor/session-actor`)
- `docs/` — Documentation only (for example, `docs/contribution-infra`)
- `chore/` — Builds / tooling / version numbers / cleanup (for example, `chore/freeze-versions`)
- `ci/` — CI configuration (for example, `ci/testing-discipline`)
- `test/` — Tests only

Use lowercase words separated by hyphens for `slug`, briefly describing the branch's topic.

### Short Branch Lifetime {#short-lived-branches}

The time from creation to merge must be ≤ 3 days. If a branch runs longer, split it or rebase it onto the main line first; avoid submitting a long-lived branch directly for review.

### Squash Merge {#squash-merge}

Squash each PR into one commit when merging back into `main`, with a commit message that follows the conventional commits specification (see the next section). Choose "Squash and merge" on GitHub.

Stage PRs created by `afk-team-workflow` are the exception: they use "Rebase and merge" to preserve each issue's conventional commit; fixes from integration review, acceptance testing, and the review loop go into a single conventional integration-fix commit after the issue commits. Push it with `--force-with-lease`.

## Commit Conventions {#commit-convention}

Commit messages follow the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
feat: 新增功能描述
fix: 修复问题描述
refactor: 重构描述
docs: 文档变更
chore: 构建/工具变更
```

The title format is `type(scope): 摘要`; scope is optional. With squash merging, the PR title becomes the changelog entry: describe the benefit users can perceive, use product terminology rather than implementation terminology (status_code, internal class names, and so on) for scope words, and qualify the scope accurately. For type values and changelog categories, see "Release Process" below and `.release-please-config.json`.

## Release Process {#release-process}

Version numbers and the changelog are maintained automatically by [release-please](https://github.com/googleapis/release-please) (configuration in `.release-please-config.json`, workflow in `.github/workflows/release-please.yml`). **Developers do not need to bump version numbers manually**—only write compliant conventional commits.

### Workflow {#release-workflow}

1. Squash-merge ordinary PRs into `main` according to the conventional commits specification; rebase-merge `afk-team-workflow` stage PRs as described in the exception above
2. release-please scans commits since the previous release and automatically opens or updates a Release PR titled like `chore(main): release X.Y.Z`, containing the next version number and an updated `CHANGELOG.md`
3. Merging that Release PR automatically creates a `vX.Y.Z` tag and publishes a GitHub Release

### commit type → version increment {#commit-type-version-bump}

| commit type | Version increment | changelog |
|-------------|---------|-----------|
| `feat`      | minor   | ✨ 新功能 |
| `fix`       | patch   | 🐛 Bug 修复 |
| `feat!` / any type + `!` / footer containing `BREAKING CHANGE:` | **major** (minor when version <1.0.0) | ⚠️ BREAKING CHANGES (at the top of the changelog) |
| `chore` / `ci` / `build` / `test` / `style` | No increment | Hidden |

`perf`, `refactor`, `docs`, and `revert` also appear in the changelog.

The `version` fields in `pyproject.toml` and `frontend/package.json` are maintained automatically by release-please (see the `# managed by release-please` comment in `pyproject.toml`) and are **read-only for developers**. `uv.lock` is likewise synchronized automatically by `uv lock` in the release-please workflow on the Release PR branch. The factory model-catalog snapshot (`lib/catalog/snapshot/`) is refreshed on the Release PR branch the same way: the workflow fetches the latest data from the catalog data repository and commits it once it passes the validator; if the fetch fails or validation fails, the snapshot is left unchanged and the workflow shows a notice. The actual version state is defined by the git tag and `.release-please-manifest.json`.

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

**This repository does not use breaking-change markers.** The frontend and backend are released together from one repository, and the backend API makes no versioned external guarantees—the bundled frontend evolves with each version, while external integrations use `/agent-installation-guide.md` to find the current installation entry point rather than depending on a version number. Classify API removals and changes normally as `fix`/`refactor`; do not add a `!` suffix or write a `BREAKING CHANGE:` footer. Correct an incorrectly marked merge according to its merge method: for an ordinary squash PR, edit the PR body to append a `BEGIN_COMMIT_OVERRIDE`/`END_COMMIT_OVERRIDE` block, then wait for the next main push or rerun the workflow manually; for an AFK rebase stage, after the final main push has updated the Release PR, correct its version and changelog artifacts directly, pass the integrity checks, and then merge the Release PR. During the 0.x stage, `bump-minor-pre-major` only limits the version jump caused by an incorrect marker to minor; it does not correct the changelog.

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
