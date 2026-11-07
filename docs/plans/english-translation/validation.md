# Validation record

## Current state

**Locally validated candidate, completed 2026-11-07.** All 4,423 baseline paths are reconciled: 3,039 changed and 1,384 preserved. No known scoped maintenance-translation or exact-scope review-record gaps remain. Independent review and the required local gates are complete. This is delivery state 2, not behavioral release with a real model.

Final reconciliation closed the reopened frontend parser coverage, all 75 prompt partials, seven late configuration files, one pragma reason, 20 developer ESLint diagnostic literals and their 17 exact test expectations. It also completed nine stale/missing blocks in the existing English provider guide. Preserved Chinese syntax, historical compatibility, localized values, multilingual inputs and user customizations are explicit exceptions, not untranslated backlog.

The reference is commit `7b4f781ab2c57d3d4dd1d0a15bdc5c427262f250`; the initial checkout was clean. Work is on `refactor/english-maintenance-base`. No commit, publication, deployment, paid evaluation or real-project profile synchronization has been performed.

## Baseline

- Frontend `pnpm check`: 303 files and 4,048 tests passed, with the repository-supported Node 24 runtime.
- Website `pnpm check`: passed.
- Backend lint, formatting, type checks, import contracts and dependency checks: passed after configuring the isolated source archive to use the installed environment.
- Full original-source backend pytest: 18,737 passed, 13 skipped, one failure in `test_export_fails_without_partial_archive_when_remote_content_is_unreadable`. It returned 500 instead of 502 with a missing `object_storage_connection` SQLite table. The isolated rerun passed. This is recorded as baseline order/environment instability, not a clean full-suite baseline or a translation regression.
- Package suites: market core 696 passed; catalog core 99 passed.

Dependencies were installed from existing frozen lockfiles without upgrades. Host Node 22 was unsuitable; frontend and website checks used Node 24.12.0. Initial source-archive environment wiring problems are not application failures.

## Candidate evidence already obtained

Counts below describe separate runs and must not be added as unique-test totals.

### Final integration checks

| Check | Result |
| --- | --- |
| Final full backend gate | **Passed: 18,768 tests, 13 skipped, 131 warnings**; Ruff, formatting, basedpyright, import contracts and dependency checks also passed |
| Final market-core, catalog-core, workflows and website gates | **Passed**; package suites retain 696 and 99 passes respectively |
| Final tests, conventions and website gates | **Passed** after the public-English completion: zero test-audit violations; convention and website consistency/type/lint/format audits passed |
| Final frontend `pnpm check` | **Passed: 303 files, 4,048 tests**; complete TypeScript, ESLint and knip checks passed |
| First official full browser suite | 1,495 passed, 5 skipped, 1 failed: notification toast expiry at 1024×600 |
| Exact notification differential in the official image | Candidate 5/5 and immutable-source baseline 5/5 passed, with original assertions, fixtures, screenshots and timeouts |
| Second complete official browser suite | **Passed: 1,496 tests, 5 existing visible-scrollbar skips**, exit 0, 19.8 minutes; original assertions, fixtures, screenshots and timeouts |
| Native coverage of remote skips | **All 5 reference-editor alignment cases passed**, original five viewports and launch options, 8.6 seconds |
| Emitted frontend assets | All 161 files are byte/hash identical between clean baseline, tested candidate and final rebuilt candidate; no filename normalization required |
| Final documentary production build | **Passed for zh-Hans and en** after completing the provider guide |
| Final source manifest | All 4,423 baseline paths present; 4,422 current hashes/blobs/modes independently checked, including 48 symlinks; historical LESSONS path checked by clean Git metadata only |

Portable completed-run logs and their SHA-256 values are indexed in [final-log-manifest.json](final-log-manifest.json). Exact acceptance boundaries are recorded in [final-local-validation.json](final-local-validation.json).

The notification failure has not reproduced in the matched narrow comparisons. Its cause remains unknown; it is not classified as preexisting. The original failed full run is retained. See [the official browser record](frontend-official-e2e-validation.json) and [environment notes](frontend-test-environment-notes.md). The native-host onboarding discrepancy and the contaminated later temporary-baseline repetitions are documented separately, without treating those repetitions as a controlled comparison.

[Trace observations](frontend-toast-trace-observations.json) record the last input point at `(659,449)`, inside the final visible toast region, and `data-expanded` on the remaining toast viewport/items. The installed Base UI implementation expands on hover/focus and pauses timers for either state. This supports a hover/focus timer-pause hypothesis, but the trace does not expose the complete store/timer state throughout the assertion and does not prove the cause. No application behavior, assertion, fixture or timeout was changed to obtain a passing rerun.

The final residual audit found one pragma trailing explanation in `lib/script/grid/layout.py` after the complete backend gate. Its English translation preserves the exact `# pragma: no cover` prefix, line attachment/count and executable AST; all 60 focused layout tests passed. This is an explicit passive source delta after the gate, not a claim that the earlier gate tested different file bytes. Seven late-discovered config-comment files also received independent review, exact parsed-value/directive checks, and a fresh passing website gate. Developer ESLint diagnostics and their exact test consumers are reviewed as a separate narrowly scoped batch.

The final ESLint prose briefly exposed `z-onboarding` as a new Tailwind candidate, adding 42 CSS bytes. That build comparison was rejected and retained. An independently reviewed slash-separated list preserves the same three layer IDs and their respective roles, without changing stylesheets or lint selectors. All 161 emitted files then returned to exact baseline/tested equality, and 234 focused guard tests and the complete 4,048-test frontend gate passed on the repaired source.

| Check | Observed result |
| --- | --- |
| Full backend gate after renderer translation | 18,764 passed, 13 skipped; lint, formatting, types, import contracts and dependency checks passed |
| Full frontend `pnpm check` | 303 test files, 4,048 tests passed |
| Website check and production build | Passed for both zh-Hans and en |
| Public translation lock and UI message consistency | Current after reviewing English CONTRIBUTING; Chinese-first source flow retained |
| Package suites after source, test-comment and schema-annotation translations | 696 market + 99 catalog passed |
| Focused prompts | 351 passed after reviewing translated internal-label expectations |
| Prompts and migration compatibility | 379 passed, including fixed historical style-expansion values |
| Session/memory subset | 42 passed, including zh/en/vi/ru and unknown-locale fallback |
| Infra/script-model/speech/audit subset, earlier candidate | 709 passed |
| Expanded infra and audit-script subset | 362 passed |
| Resume/checkpoint/artifact-currency subset | 85 passed |
| Integration prompt consumers, independently reviewed expectations | 1,060 passed |
| Render/preview/clamping/artifact/session subset | 354 passed |
| Reference-video renderer, execution and preview after wrapper translation | 208 passed; native/soft/silent exact-request fingerprints changed while formal bases remained equal |
| Runtime-document contract subset | 74 passed, including six adversarial force-prohibition examples |
| Grid and product negative guards | 44 passed |
| Media request/timeline/tool subset | 19 passed |
| Gate script after passive-prose translation | 14 passed |
| Chinese-writing skill local tests | 88 passed |
| Synthetic profile upgrade/customization/deletion/collision/rollback matrix | Six cases passed |
| Actual baseline/candidate profile-tree upgrade and rollback | Six disposable-project cases passed, covering all three modes |

The first integrated candidate backend run found 92 failures with 18,665 passes and 13 skips. Its log was used to classify producer/expectation mismatches. Narrowly reviewed expectation adaptations preserved original multilingual inputs, machine markers, compatibility data and assertions. That failed run is not presented as a successful gate. A second full backend pytest run reached 18,760 passes, 13 skips and three failures. Two were literal expectations after restoring an explicit `@` marker and were fixed (33 related tests passed). The third was the event-loop guard observing a `TemporaryDirectory` garbage-collection cleanup during an archive test; that test passed in isolation on both baseline and candidate. Its cause is not established, so the failed-run observation is retained rather than labelled preexisting. The gate stopped before its package/test/convention domains, which subsequently passed separately (market-core, catalog-core, tests and conventions). After the renderer change, the third complete backend gate passed with 18,764 tests and 13 skips, including static checks. The final complete gate passed with 18,768 tests and 13 skips. The earlier intermittent failures remain recorded; passing reruns do not establish their cause. Later overview/scope description changes also received separate structural, semantic and focused checks.

## Later passive-prose checks

The second frontend comment batch reached 1,034 passes and two failures across 69 files. The failures concerned Escape handling in RenderDialog and scroll-target expiry in ReferenceVideoCanvas. Both files then passed all 110 tests in isolation. The cause is not established; isolated success does not clear the failed broad run. A fresh complete frontend check after root comment batches 1–3 and exact-comment reuse passed type checking, ESLint, knip and all 4,048 tests in 303 files. Later batches have separate related-suite evidence and are now covered by the final complete passing gate above. Compiler output with comments removed and functional directive prefixes remain identical for every changed file in these batches; the final global TypeScript proof separately enumerates exactly 17 authorized diagnostic-expectation literal changes in one test file.

The repository/custom-provider/object-storage subset passed 170 tests after its passive documentation changes. After task/usage translations, all repository integration suites passed 145 tests. All 39 changed database modules retain identical executable AST; remaining Han occurs only in documented original code placeholders and a multilingual fixture name. Frontend comment batches four/five passed 2,043 related tests across 147 files; batch six passed 1,031 tests across 75 files; batch seven passed 1,666 across 119; batch eight passed 2,217 across 179; batch nine with the second exact-comment reuse passed 2,285 across 164. The subsequent complete frontend check after API translation passed types, ESLint, knip and all 4,048 tests in 303 files. Its 267 API comment translations preserve inline-code examples and functional JSDoc markers.

The auditor now has eleven passing unit tests, including preservation of existing English quotations. The four translated draft-repair document descriptions passed independent clause review and 27 related tests; machine document identifiers and multilingual input fixtures remain unchanged.

The archive-focused seven-suite candidate run later reached 167 passes and one blocking-I/O guard failure in `test_unprovable_old_audio_claims_still_fail_export[current_audio]`, attributed to `TemporaryDirectory` finalizer cleanup through `subprocess_deadline.run_with_deadline` and media-probe/audio utilities. This resembles the earlier unresolved failure, but its cause is not established. The identical candidate rerun passed 168 tests with two warnings; a matched baseline run with the same suite order and frozen environment also passed 168 with two warnings. Imports were verified inside the immutable archive. These passing comparisons did not reproduce the failure and do not establish it as preexisting; its cause remains undetermined.

Production frontend builds succeeded for baseline and candidate under the same frozen environment. The first comparison detected a removed `line-clamp-1` utility and added `grow` utility from comment tokenization. Literal delimiting and equivalent prose corrected both without changing executable TypeScript. Rebuilt candidate CSS is byte-identical to baseline: 221,969 bytes, SHA-256 `9ccaff6f33408e088c34e47e624c6be8683e5f55884357757c88b29a074db2c7`. [CSS evidence](frontend-production-css-review.json) and [reproduction checks](checks/README.md) distinguish this from AST equality. Task-store translation passed 1,156 related tests across 81 files.

## Structural and independent-review evidence

- [Schema comparison](schema-contract-review.json): 61 tools, 38 static script models 25 dynamic model cases, and the overview/source-scope models retain identical structural payloads. Only schema description metadata is excluded, preserving instance properties named `description`. All exposed tool-schema descriptions have been inspected for remaining Han prose; the absence of Han is a coverage aid, not an acceptance criterion.
- [Package schemas](package-schema-review.json): exactly 98 annotation changes across three schemas; restoring those annotated values makes each complete JSON document identical to baseline. All nonannotation data is unchanged.
- [Formal artifacts](artifact-contract-review.json): six representative formal-content bases remain identical; the exact rendered request fingerprint changes as expected. Resume tests check frozen submitted requests rather than regenerating them.
- [Actual profile roundtrip](profile-roundtrip-review.json): real source trees tested only in disposable projects. Separate synthetic tests cover collisions and customizations after upgrade.
- [Workflow review](runtime-workflow-independent-review.md) and [media-description review](media-independent-review.md) record independent bilingual source coverage and its limits.
- [Infra](infra-passive-review.json) and [ORM models](db-passive-review.json) record executable-AST equality for passive batches. Equivalent checks were run for package source/test comments, frontend comments and each worker's passive source batches.

The Markdown audit intentionally reports translated human placeholders, example prose and added supersession links for individual review. It must not be silenced by deleting assertions or restoring untranslated full prose. Fenced examples and semantic clauses require separate review.

## Compatibility choices

- Preserve the three style-template bodies expanded by historical v13→v14 migration, while translating their metadata. New fixed-value tests prevent an indirect migration-data change.
- Preserve historical Chinese diagnostics, matchers, `图N` / `@图片N` / `@音频N` syntax, pending markers, locale values and source-language defaults.
- Normalize only the exact historical built-in image Avoid declaration at render time, preventing duplicate system trailers without rewriting stored prompts or removing customized restrictions.
- Existing customized profiles may retain Chinese content through the existing three-way synchronization rules. No forced reset distributes this translation.

## Completed scope and release boundary

The [inventory](inventory.json) reconciles every baseline path and separately lists new delivery artifacts in [tracked-files-final.json](tracked-files-final.json). ADR 0121 is visible through an exact repository `.gitignore` exception added during final pre-commit review; the developer's global ignore remains unchanged. Earlier coverage claims from the bare TypeScript scanner were explicitly reopened and superseded by parser-based inventories and independent reviews; see [the frontend reconciliation](frontend-inventory-reconciliation.md). All 75 changed prompt partials now have [exact-path independent semantic review](final-prompt-partials-independent-review.json), and public-English completion has [separate clause-level review](final-public-english-independent-review.json). A changed file or an absence of Han is not an acceptance criterion.

## Additional pre-commit review

The user-requested final review used separate standards and specification reviewers against the actual uncommitted candidate. The bounded behavioral-source inspection found no additional actionable regression in its inspected consumers and prompts; it does not certify every changed clause again. The known ADR delivery gap was corrected with a single-path repository ignore exception, independently reviewed for scope. No runtime code, model instructions, test assertions or global Git settings changed in this review.

After that correction, 322 related tests passed, runtime-profile lint passed, and the test/convention gates passed. A fresh production frontend build succeeded and all 161 emitted paths/bytes remained identical to the original tested build. The complete backend/frontend/browser/documentation gates above remain prior complete-run evidence; they are not presented as newly rerun in this final review. See [precommit-review.json](precommit-review.json) for exact commands, review boundaries and hashes.

The final evidence includes full OpenAPI comparison with only 445 reviewed description-metadata differences, exact structural captures for all 61 tools, the historical-style byte checks, profile roundtrip tests, and the independent Python production-boundary review. Respective reports record their own scope and exclusions rather than claiming universal behavioral equivalence.

Real-model comparisons, compaction/file-reload behavior, token-budget changes and quality variability remain pending under [the evaluation protocol](evaluation.md). Model, environment and spending budget must be selected before paid evaluation. No locally passing suite proves universal model equivalence.
