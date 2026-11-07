# Running tests

After changing code or tests, first run the smallest relevant test set during development. Before pushing, run the complete gates for affected domains listed in `AGENTS.md`.

## Selecting relevant tests

If selection returns zero tests, broaden to the corresponding directory or the complete suite. Zero tests do not count as successful validation.

- **Backend test files changed**: run those files directly.
- **Backend source changed**: `uv run python -m pytest --testmon`. pytest-testmon uses coverage relationships in `.testmondata` to run cases affected by changes. With no data file it runs the complete suite and builds the database; subsequent runs are incremental. Add `-n 4 --dist loadfile` for the initial database or broad changes; parallelism makes small selections slower. Code executed in a separate process is not included in coverage relationships (for example, subprocess imports in `tests/integration/test_imports.py`); explicitly run those test paths when changing modules they touch. Passing directories can narrow selection further. Selectors containing `::`, `-k`, or `-m` disable filtering and are for manual diagnosis only.
- **Changes requiring the complete backend suite**: `pyproject.toml`, `uv.lock`, root `tests/conftest.py`, package initializers containing behavior, or changes involving Alembic, profile, DB, i18n, or shared test infrastructure. Run the complete suite when impact is uncertain as well.
- **Frontend test files changed**: pass those files directly to Vitest.
- **Frontend source changed**: run `pnpm exec vitest related --run <源文件>` in `frontend/`; for branch-level checks use `pnpm exec vitest run --changed <base>`. TypeScript source changes also require a complete typecheck.
- **Changes to the frontend page-level suite (`frontend/e2e/`), layout or scrolling components, the set of called APIs, or the timing of stream connections (including SSE)**: run `pnpm e2e` in `frontend/`. Page-level suites replay only recorded fixtures and scenario `api` overrides; unrecorded requests fail immediately. If local rendering differs from CI, the container result is authoritative; see “前端页面级测试” in `CONTRIBUTING.md` for instructions. When backend response shapes change, also run `pnpm e2e:record` to record new API fixtures.
- **Changes requiring the complete frontend suite (`pnpm check`)**: `package.json`, `pnpm-lock.yaml`, `vitest.config.*`, test setup, i18n, or branding.
- **Source changed in `packages/arcreel-market-core/`**: run all subpackage tests (`cd packages/arcreel-market-core && uv run python -m pytest`; the subpackage has its own pytest configuration and is collected separately), and select main-repository tests according to its consumers. When message keys are added or removed, also run `tests/unit/lib/i18n/`.
- **Source changed in `packages/arcreel-catalog-core/`**: run all subpackage tests (`cd packages/arcreel-catalog-core && uv run python -m pytest`) and select main-repository tests according to its consumers.

## Running in a worktree

Worktrees have no `.venv` or `node_modules`: first run `uv sync` for the backend and `pnpm install --frozen-lockfile` in each of the frontend and documentation-site directories. Copying `.testmondata` from the main checkout avoids testmon's initial database build. Records are indexed by code fingerprints, so parts differing from current code are treated as changed and rerun. Cases marked `local_port` are skipped, with reasons, in sandboxes that forbid binding local ports; run the complete suite once in an environment that allows binding. Root conftest sets `PYTHON_DOTENV_DISABLED=1` for test processes, so they do not read `.env`. Do not create an empty worktree-root `.env` merely to hide the main checkout's `.env`; tests of `.env` reading explicitly `delenv` this setting.

## Event-loop blocking guard

Root conftest loads `tests/blocking_guard`. Throughout synchronous and asynchronous cases, it records blocking calls made by lib/server code on event-loop threads (including the application thread behind synchronous `TestClient`). At case end, violations outside the exemption list fail the case with entries and stacks. Stacks run from inner to outer and prepend the innermost few non-repository frames before the first repository frame (standard-library and third-party frames, with package-relative paths and an omission count for the rest). This shows what actually blocks in dependencies behind a business-code `await`. The entry is the nearest lib/server async function (`<文件>::<限定名>`). Blocking inside a `tests/` fake is counted too: replacing it with a production dependency blocks the same position. For fake-related false positives, fix the fake according to the production thread contract. Use in-memory implementations when possible. If the fake's responsibility is writing to disk (backend artifact writes, simulating concurrent writers, or callbacks that really write formal files), put that I/O in `asyncio.to_thread`. Blocking with no enclosing lib/server async function (test functions directly calling synchronous lib code) is not counted.

- `tests/blocking_guard/exemptions.toml` holds permanent exemptions only for one-time startup and shutdown blocking, each with a reason. There is no other exemption mechanism; fix new blocking calls. During complete runs, exemptions not triggered in that session fail the session. Remove corresponding entries when startup or shutdown paths change. Every exemption must be triggered by a case under `tests/unit`: CI's unit job runs `tests/unit` by path with `--blocking-guard-expect-all-exemptions` and reports untriggered entries as stale. The integration job does not use this flag.

## CI hang diagnostics

Backend test jobs set `ARCREEL_TEST_HANG_DUMP_DIR`. Loaded by root conftest, `tests/hang_watchdog.py` makes the xdist controller and every worker record the case they start. After 180 seconds without progress, the process dumps all thread stacks; session finalization and process exit are covered too. Test steps time out before the job does. After step failure or cancellation, “Hang and crash diagnostics” prints each process's last progress, thread stacks, and kernel OOM records. A process ending in `sessionfinish` or `unconfigure` has finished its cases; one ending at a case's `run` is stuck or died there. For hangs or `node down: Not properly terminated`, inspect this step first. The worker's last case is the case running when it crashed; controller progress includes exit codes or signals. Locate stacks according to how it died:

| Termination | Stack location |
| --- | --- |
| `os._exit` called | The worker's `.stacks`, headed by the literal `调用 os._exit(<退出码>)` |
| SIGTERM, SIGHUP, or SIGINT | The process's corresponding `.SIGTERM`, `.SIGHUP`, or `.SIGINT` file |
| Segmentation fault or another fatal signal | `Fatal Python error` in test-step stderr. If absent, faulthandler's handler could not run, for example after non-main-thread stack overflow |
| SIGKILL | No stacks; inspect kernel OOM records |

CI and local gates pass `--max-worker-restart=0` to xdist: a crashed worker fails immediately rather than being replaced. pytest-xdist 3.8.0 requeues files that already ran when replacing workers, leaving the session stuck at 99% (upstream pytest-dev/pytest-xdist#1378, fix commit 25282ab). Remove this argument after upgrading to a version containing that fix. Set the same environment variable for local reproduction to obtain the same files.
