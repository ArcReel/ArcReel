# mutmut mutation-testing runbook

Steps for running mutation batches, reviewing results, and accepting reworked tests. Configure in `pyproject.toml` under `[tool.mutmut]`; configuration comments explain choices.

For mutation-score purpose, see the final paragraph of "Each case protects an observable contract" in `docs/standards/testing.md`. Mutation testing stays outside CI: run manually by batch, listing modules in `[tool.mutmut] only_mutate`.

**Survival brings tests under review; it does not justify deletion.** Evaluate survivors in already covered code individually under "Each case protects an observable contract" in `docs/standards/testing.md`. Do not act on:

- Mutants requiring changed inputs rather than stronger assertions on existing inputs (coverage compensation).
- Equivalent mutants (source changes without behavior changes).
- Mutants in untested code.

## 1. Environment

mutmut belongs to separate `mutation` dependencies, not default `uv sync`:

```bash
uv sync --group mutation
```

Mutated modules import mutmut trampolines, requiring installation in `.venv`; `uv run --with mutmut` does not cover later review. Run `uv sync` to remove it after batches if desired.

## 2. Run a batch

### 2.1 Choose modules

Choose around 8 untested modules outside section 8's reviewed table. Open one issue per batch recording modules, verdicts, and research branches; add modules to the table after merge.

- **Admission**: exclude all `server/routers`; elsewhere exclude files where decorators skip ≥50% of lines. mutmut 3.7.0 generates no mutants for whole decorated-function subtrees (only single `@staticmethod` / `@classmethod` exempt; decorated classes entirely skipped, not configurable). Routes/`@tool` closures yield almost none. Census inadmissible files:

  ```bash
  uv run python scripts/decorator_census.py --per-file --min-skip 50
  ```

  Without arguments, report directory-level skipped-line proportions to identify worthwhile directories.

- **Priority**: untested, test-rich, larger modules; prefer `server/services`, `lib/backends/video_backends`, `lib/custom_provider`, `lib/script/reference_video`. Do not invent candidate/actionability predictors: mock density/weak-assertion proportions failed to predict results. Test speed is not admission; `tests/conftest.py` / `tests/mutmut_plugin.py` fixed timeout/parallel-child problems, including clean database-integration runs.
- **Stopping point**: none predetermined. Calculate actionability (survivors classified ③ worth protecting / all survivors) after each batch; stop after two consecutive significant drops.

### 2.2 Run once

1. `git fetch`, branch from `origin/main`, and verify prior batches' merged PRs are present. Stale local `main` resurrects previously addressed mutants, wasting review rounds.
2. Set batch modules in `[tool.mutmut]` `only_mutate`; empty means all `source_paths`, taking hours.
3. Run:

   ```bash
   uv run mutmut run
   ```

   Keep CPU-count parallelism. `tests/mutmut_plugin.py`, loaded through `[tool.mutmut] pytest_add_cli_args`, gives sessions private temp directories; creator-process guards in `tests/conftest.py` prevent cross-child database cleanup.
4. Copy `mutants/**/*.meta` somewhere future `mutmut run` calls cannot overwrite for section 5 baselines. Batch 1 uses `research/mutmut-batch-1` branch `baseline/`.

Restore `only_mutate` to comments afterward; `mutants/` is ignored by `.gitignore`.

**Tests reading source text instead of importing must use nodeid `--deselect` in `pytest_add_cli_args`, never whole-file `--ignore`.** Source files in `mutants/` contain every variant (strings gain `XXfooXX` / `FOO` forms), so scans of `lib/` `server/` literals against registries fail stats with unregistered values. Whole-file `--ignore` also removes behavioral tests, falsely leaving uniquely covered mutants alive (over 100 tests besides three scanners in `test_task_failure_capability.py`). Excluded scanners are `test_capability_codes_registered_no_drift` / `test_no_unscannable_capability_construction_sites` / `test_capability_construction_sites_supply_every_template_param`. New exclusion comments in `[tool.mutmut]` must explain unique kills and why exclusion misses none: exclusions may add false survivors, never conceal false kills.

## 3. Read results

**First check each module's mutant order of magnitude.** Expect approximately 1000 per thousand source lines. Far fewer (such as below one tenth) signals unassociated functions, not good testing: mutmut silently zeros modules without terminal reports. Exclude them from batches without classification; misspelled `only_mutate` names also yield zero, so check spelling.

**Calculate from `.meta`, not terminal summaries.** Each module has `mutants/` `<模块>.py.meta`, whose `exit_code_by_key` maps mutant names to pytest exit codes. Terminal summaries count only recognized exits; segmentation faults (−11) disappear entirely, as two did in #2257.

| exit code | mutmut interpretation | Repository treatment |
| --- | --- | --- |
| 1 | killed | killed |
| 0 | survived (🙁) | survived; classify |
| 36 / −24 / 24 / 152 / 255 | timeout (⏰) | Review in fresh processes under section 4 |
| 3 | pytest internal errors counted killed | Review |
| Other (including −11 segmentation faults, None unchecked) | suspicious/unrecorded | Review |

One criterion: **review every exit other than 1 or 0**; classify only confirmed survivors. Zero already means all pytest cases passed and confirms survival without review.

## 4. Fresh-process timeout review

Timeouts are rare fallbacks, not normal. A prior cause was fork: mutmut forks per mutant, inheriting module-level `lib.db.engine` aiosqlite connections without their worker threads. First queries then wait forever and exhaust budgets. `tests/conftest.py` uses `os.register_at_fork` to discard child pool connections (`dispose(close=False)`); full 8-module/8-way reruns had zero timeouts. Remaining timeouts are not kills: rerun associated cases in fresh processes, taking 3–11 seconds each.

```bash
# Run at project root: tests-for-mutant reads mutants/mutmut-stats.json; inside mutants/ it reports Failed to load stats
uv run mutmut tests-for-mutant lib.speech.speech_rate.x_estimate_spoken_seconds__mutmut_8 > nodeids.txt

# Run inside mutants/ with the .venv interpreter, not uv run (see section 6)
cd mutants && MUTANT_UNDER_TEST=lib.speech.speech_rate.x_estimate_spoken_seconds__mutmut_8 \
  ../.venv/bin/python -m pytest -x -q @../nodeids.txt
```

Pytest natively accepts `@文件` argument files, one argument per line with spaces/quotes unchanged, supporting over 180 such parametrized nodeids. Never use `$(cat …)` or bare `xargs`: zsh makes the former one argument, silently running zero tests, while the latter splits spaced nodeids.

Interpretation: **one failure means killed; all passing means survived.** For confirmed kills, including uncounted crashes, set baseline-copy exits from section 2 to 1, placing them inside section 5's baseline-killed guardrail. Leave confirmed survivors unchanged. Of batch 1's 616 mutants, only two segmentation faults needed review, both confirmed killed.

## 5. Three-layer acceptance after rework

After assertion changes, **rerun whole batches** with `mutmut run`, then compare. `mutmut run` skips existing results; mutant lists also live in `.meta`, so deleting only `.meta` yields zero mutants. For same-module reruns, move `mutants/mutmut-stats.json` out, delete all `mutants/`, recreate `mutants/`, then restore stats, saving five-minute collection. Changed `only_mutate` module sets require deleting all `mutants/`, including stats: stats contain function hits only for mutated modules; new modules otherwise appear 🫥 unassociated.

```bash
uv run python scripts/mutmut_compare.py \
  --baseline research/mutmut-batch-1/baseline \
  --current mutants \
  --reworked reworked.txt \
  --equivalent equivalent.txt
```

`reworked.txt` lists targeted mutant names one per line; optional `equivalent.txt` lists equivalent ones. Output has three layers/review lists: exit 0 pass, 1 failure/pending review, 2 inconsistent inputs (different mutant sets, unknown listed mutants, etc.). Pending reviews prevent passing. Review under section 4, set current `.meta` confirmed kills to 1/survivors to 0, and compare again.

| Layer | Passing condition | On failure |
| --- | --- | --- |
| Reworked targets | All exit 1 | Timeout → section 4; one failure means killed. Still alive → four-way triage below |
| Baseline killed | None becomes exit 0 | Timeout → fresh-process review; one failure preserves guardrails, all passing alone means regression, caused by breaking other tests |
| Baseline survivors not reworked | Record incidental kills only; equivalent exits remain 0 | If equivalents are killed, fresh-process review under section 4 without `-x`, three identical-command runs. Count failure locations by assertion file/line in tracebacks, not nodeids: cases stop at first failures, and identical nodeids cannot distinguish new/existing assertions. Same points across all three, all newly added → wrong classification, revise verdict tables (if assertions exceed the table's stated contract, narrow assertions and retain equivalence). Same points including existing assertions → also revise: baseline survival means missing stats association, not equivalence. All three pass or points differ across runs → invalidate the whole run: equivalents cannot be killed, so subprocess results are unreliable, such as abnormal exits marked killed. Equivalent timeouts/crashes → section 4, one failure means killed. Recompare after verdict/`.meta` changes. Other unworked abnormal exits do not affect conclusions |

An independent hard gate: `git diff --name-only origin/main` must contain no `lib/` or `server/`. Production changes regenerate `mutants/` and reindex mutant names, invalidating comparison.

**Four-way triage** when reworked targets survive, decided once without reverting added assertions: incomplete/unexecuted assertions → keep fixing; different inputs actually required → stop under no-coverage-compensation; equivalent → stop.

Partial modules cannot test layer two; rerun whole batches.

### 5.1 Split one module batch into multiple rework tickets

Survivors commonly span tickets/sessions, sharing section 2 baselines but accepted independently:

- **Tracer first**: choose two reworks and finish assertion edits → complete gates → full rerun → three-layer acceptance → PR merge before scaling. Tracers share baselines with later tickets, appearing there as unreworked baseline survivors whose kills are recorded only.
- `--reworked` lists this ticket only; `--equivalent` lists the whole batch, checking zero kills on every ticket.
- Earlier tickets' killed targets become later tickets' unreworked baseline survivors; record kills without anomalies.
- Before each branch, `git fetch` and verify modules unchanged on `origin/main` since baseline: empty `git diff --stat <基线提交> origin/main -- <模块列表>`. Rework PRs cannot change them, but other commits on `main` can.
- Other module changes make comparison reject differing source hashes for same-named functions, invalidating that module's baseline/verdicts. Function-local mutant numbering shifts with source. **Rerun its baseline/reclassify survivors**, retaining unchanged modules' old baselines (changed `only_mutate` module sets require deleting stats). Never rebase research branches: they store result snapshots, whose mutant names rebasing cannot fix. Even one-function changes invalidate entire modules; mutmut guarantees no numbering stability elsewhere.

## 6. Known pitfalls

- **New test files touching no mutated functions stop incremental stats.** mutmut runs stats on new cases alone and exits `Stopping early, because we could not find any test case for any mutant` if none touch mutants. Delete `mutants/mutmut-stats.json` for full stats.
- **Never `uv run` inside `mutants/`.** Its copied `pyproject.toml` creates another environment without mutmut, causing trampoline `ModuleNotFoundError`. Use `../.venv/bin/python`.
- **`tests-for-mutant` works only at project root**, reading `mutants/mutmut-stats.json`, absent from inside `mutants/`.
- **Do not reduce `timeout_multiplier`.** It saves no time, misclassifying confirmed survivors as killed and hiding ineffective tests.
- **Mass −11 segmentation faults invalidate runs; do not review individually.** On macOS without `*_proxy`, `urllib.request.getproxies()` calls SystemConfiguration through `_scproxy`; forked multithreaded children segfault, affecting `openai.OpenAI` / `httpx.Client` construction (577/2186 in batch 2), while fresh-process reviews pass normally. `tests/mutmut_plugin.py` replaces both entry points with constants. Recurrence requires one `PYTHONFAULTHANDLER=1 uv run mutmut run <mutant>` crash-stack investigation, not load/flakiness assumptions. −11 should be sporadic (2/616 in batch 1).
- **Use full test selections.** `tests/unit` alone is 5.5× faster but yields roughly 45% false survivors, each needing full-suite review and higher overall cost.

## 7. Cost reference

Measured batch 1: 8 modules, 616 mutants.

| Item | Value |
| --- | ---: |
| Mutant density | Approximately 1100 / thousand source lines |
| Wall time, 8-way | Approximately 10 minutes for 8 modules/616 mutants, excluding approximately five-minute full stats; 17:47 before eliminating timeouts, which drive wall time |
| Fresh-process timeout review | 3–11 seconds each |
| Individual classification | Approximately 3 seconds each |

## 8. Reviewed modules

Choose outside this table; reruns add no value, repeating uncovered/equivalent results. Verdict tables/baselines live on batch research branches, outside `main`.

| Module | Batch | Ticket | Research branch |
| --- | --- | --- | --- |
| `lib/backends/generation_type_buckets.py` | Batch 1 group A | #2257 | `research/mutmut-batch-1` (`baseline/A/`) |
| `lib/infra/content_digest.py` | Batch 1 group A | #2257 | `research/mutmut-batch-1` (`baseline/A/`) |
| `lib/episode/episode_paths.py` | Batch 1 group A | #2257 | `research/mutmut-batch-1` (`baseline/A/`) |
| `lib/script/grid/splitter.py` | Batch 1 group A | #2257 | `research/mutmut-batch-1` (`baseline/A/`) |
| `lib/speech/speech_rate.py` | Batch 1 group A | #2257 | `research/mutmut-batch-1` (`baseline/A/`) |
| `lib/infra/text_metrics.py` | Batch 1 group A | #2257 | `research/mutmut-batch-1` (`baseline/A/`) |
| `lib/custom_provider/discovery.py` | Batch 1 group B | #2257 | `research/mutmut-batch-1` (`baseline/B/`) |
| `lib/backends/text_backends/openai.py` | Batch 1 group B | #2257 | `research/mutmut-batch-1` (`baseline/B/`) |
| `lib/backends/text_backends/instructor_support.py` | Batch 2 pool 1 | #2297 | `research/mutmut-batch-2` |
| `lib/backends/text_backends/ark.py` | Batch 2 pool 1 | #2297 | `research/mutmut-batch-2` |
| `lib/backends/text_backends/gemini.py` | Batch 2 pool 1 | #2297 | `research/mutmut-batch-2` |
| `lib/backends/text_backends/grok.py` | Batch 2 pool 1 | #2297 | `research/mutmut-batch-2` |
| `lib/backends/video_backends/gemini.py` | Batch 2 pool 1 | #2297 | `research/mutmut-batch-2` |
| `lib/backends/image_backends/dashscope.py` | Batch 2 pool 1 | #2297 | `research/mutmut-batch-2` |
| `lib/backends/image_backends/minimax.py` | Batch 2 pool 1 | #2297 | `research/mutmut-batch-2` |
| `lib/custom_provider/backends.py` | Batch 2 pool 1 | #2297 | `research/mutmut-batch-2` |
| `server/agent_runtime/sdk_tools/asset_inventory.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |
| `server/agent_runtime/sdk_tools/enqueue_assets.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |
| `server/agent_runtime/sdk_tools/entry.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |
| `server/agent_runtime/sdk_tools/episode_planning.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |
| `server/agent_runtime/sdk_tools/generation_batches.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |
| `server/agent_runtime/sdk_tools/patch_episode_meta.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |
| `server/agent_runtime/sdk_tools/patch_project.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |
| `server/agent_runtime/sdk_tools/rename_asset.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |
| `server/agent_runtime/sdk_tools/retry_project_migration.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |
| `server/agent_runtime/sdk_tools/workflow_plan.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |
| `server/agent_runtime/sdk_tools/workflow_status.py` | Batch 2 pool 2 | #2298 | `research/mutmut-batch-2` (`pool2/`) |

Of batch 2 pool 2's 11 modules, nine yielded zero mutants because whole `@tool` closures were skipped. Section 2.1 admission excludes them; list them to prevent reruns.
