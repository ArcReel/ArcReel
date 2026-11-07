---
paths:
  - "tests/**"
  - "packages/*/tests/**"
  - "frontend/src/**/*.test.*"
  - "frontend/src/test/**"
  - "frontend/src/__mocks__/**"
  - "frontend/e2e/**"
---

# Testing

`scripts/audit_tests.py --check` enforces structural rules (listed in its module docstring) and gives remediation guidance. This document covers only requirements it cannot decide.

## Tiers and placement

### Choose a tier by the dependencies a case actually touches

| Tier | Allowed dependencies |
|---|---|
| `unit` | In-memory objects, `tmp_path`, and read-only committed repository files; writes only to `tmp_path`; no real DB, subprocesses, or network |
| `integration` | Real DB, runtime filesystem reads/writes (data roots, user directories, etc.), bundled ffmpeg subprocesses |
| `e2e` | Real external services (remote APIs, model calls); skipped by CI by default |

Collection rejects missing tier directories or unit cases using database fixtures from `tests/conftest.py` (injected `uses_db`). Direct real-DB connections, subprocesses, and network access outside those fixtures are not mechanically checked and require review. Subprocesses (git, ffmpeg) or network in unit cases make `-m unit` slow and machine-dependent. Conversely, dependency-free cases in integration miss the fast loop and land in the wrong CI shard. Reading committed repository files (prompt rules, skills, docs) is fast and guaranteed by the checkout, independent of the machine, and remains unit-level.

Placement: one Alembic migration test file per migration script under `tests/integration/lib/db/migrations/`, still using SQLite; workspace-subpackage tests under `packages/<子包>/tests/`, mirroring source structure without tiers; tests asserting natural-language rendering of subpackage outputs under the main repository's `tests/unit/lib/i18n/`.

## Fakes

### Prefer real objects and replace only repository boundaries

Preference: real objects (in-memory SQLite, `tmp_path`) > handwritten fakes in `tests/fakes.py` > Mock with `spec` / `autospec` > bare `MagicMock` / `AsyncMock`. Mock only repository boundaries: third-party SDKs, network, subprocesses, filesystem, and clock. Use real internal objects. Replacing an internal dependency removes coverage of the contract between it and its consumer, so tests pass even when production changes break it.

Frontend follows the same rule: render real internal children by default. Mock heavy components (virtualization, animation, canvas), side-effecting components (requests, timers), or purely presentational components irrelevant to the test. Mocking a child that carries the tested interaction leaves only a shell test.

Exception: the replacement is one of those boundaries, or triggers an otherwise unreachable branch (exceptions, timeouts, external failures).

### Control internal behavior through an explicit seam with a production default

A seam is a constructor or keyword parameter with a production default, preserving production behavior, such as `retry_async(operation, *, clock=..., jitter=...)`. Use it for polling time, intervals, backoff, HTTP probes, filesystem, and subprocesses. Replaceable module globals are not seams: they share mutable state across tests. Testability work must preserve production behavior; frontend permits only pure-function extraction and hook-level structural decomposition.

### Process-cache reset hooks are public reset_for_tests() / reset_<名称>_for_tests() functions

Modules using process-level caches such as `functools.cache` expose `reset_for_tests()` or `reset_<名称>_for_tests()` (for example, `lib.infra.app_data_dir.reset_for_tests`). They clear caches without changing production behavior. This is transitional: new code should prefer cache dependencies as parameters.

### Intercept outbound HTTP with respx and FastAPI dependencies with `dependency_overrides`

respx retains real httpx clients and intercepts at the transport layer, including `AsyncOpenAI` streaming; assert actual serialized requests. Replace route dependencies through `app.dependency_overrides`, not patching.

### Frontend page-level API fixtures are recorded from a real backend

Generate responses under `frontend/e2e/fixtures/recorded/` only through `pnpm e2e:record` against a real backend. Do not handwrite protocol stubs or edit recorded files. Backend PRs changing response shapes must rerecord, exposing those changes in fixture diffs. Handwritten fixtures reflect only the author's understanding: tests continue passing when real fields, frame ordering, or query shapes change, and failures appear only at runtime. Put stress variants such as long text or many entries in scenario `api` fields.

Exception: PRs without response-shape changes need not rerecord. Any recorded-file modification must still come from `pnpm e2e:record`.

## Assertion value

### Each case protects an observable contract

Assert actual outputs: returned types and attributes, real requests captured by respx, observable state. `audit_tests` already rejects mock-call-only assertions, patching private symbols, and duplicate cases in one file. Review judges four forms it cannot decide:

- **Redundant weakening**: the same path as another case, with assertions that are a strict subset.
- **Over-characterization**: asserting log text, dictionary order, private properties, or other implementation details instead of contracts.
- **Setup/assertion imbalance**: dozens of setup lines only to assert a call occurred.
- **Mock-call-only assertion helpers**: hiding the `audit_tests` criterion in a helper.

Such cases have negative value: they protect no behavior but fail in bulk during refactoring. Remove or redesign them rather than adding assertions for counts. Delete tests whose behavior is already covered or lacks a real branch/contract constraint. Rework valuable cases to assert contracts; production seams belong in a separate batch.

Coverage and mutation scores are signals only. Do not write tests for percentages or merely to kill mutants; reduced coverage after deleting meaningless tests is acceptable. Mutation-test batch workflow: `docs/testing/mutmut-runbook.md`.

## Timing

### Drive waits, retries, and timeouts with clock seams or event handshakes

Real-time waits (`time.sleep`, fixed `setTimeout`) slow cases and fail intermittently under load. Treat flakes as ordinary defects: replace them with clock seams or handshakes. If fixing is impossible or not worthwhile, delete or redesign according to “Each case protects an observable contract.” Do not add automatic retries (pytest-rerunfailures, CI job retry): they hide real failures. Frontend retains Vitest's default five-second timeout; explicitly override individually slow cases with a reason.

Exception: probabilistic stress cases (real concurrency + real time) must be registered here. The sole current registration is the atomic-write stress case in `tests/integration/lib/project/test_project_manager_concurrent_save.py`.

### Readiness waits target the asynchronous operation's final step

Asynchronous work often has multiple steps: state update, DB write, broadcast; or rendering followed by subscription effects and refresh settlement. Continuing after an intermediate step lets later steps race assertions or subsequent actions and causes load-dependent flakes. Wait for the final step: terminal broadcast, dequeue event, subscription ready, or refresh settlement. Update other cases using the same wait pattern for that process together.
