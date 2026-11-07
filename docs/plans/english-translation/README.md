# English translation implementation record

## Baseline and boundaries

- Source commit: `7b4f781ab2c57d3d4dd1d0a15bdc5c427262f250`.
- Initial checkout: clean, on `main`; implementation branch: `refactor/english-maintenance-base`.
- A separate immutable source archive is used for baseline checks. Dependency installation uses the existing lockfiles; no dependency upgrades are part of this work.
- Full baseline tree: [tracked-files-baseline.json](tracked-files-baseline.json), covering all 4,423 tracked entries, including hidden paths, binary files and symlinks.
- Translation routing inventory: [inventory.json](inventory.json). Classifications route review; they do not certify individual strings. Baseline line numbers identify candidate spans. Final reconciliation records review references and intentionally preserved content separately from changed-file status.

Translate maintenance prose and internal model instructions into English. Preserve supported locales, defaults, output-language rules, APIs, data formats, tool identities, historical data and user customizations. Public documentation retains its Chinese-first source pipeline, locale routes and existing editions. Do not translate Chinese locale values or multilingual test inputs to make a character scan pass.

## Ownership and review

The coordinator owns classification, baseline evidence, integration, source generators, compatibility checks and final reconciliation. Separate agents own internal documents, behavioral instructions and passive source comments. File ownership is exclusive during editing. A translator cannot be the sole semantic reviewer of model-facing instructions.

Every reviewed batch records translated files, preserved literals, semantic review and actual checks. Remaining work is reported explicitly. No publication, deployment, paid media generation or changes to real projects are authorized by this implementation.

## Acceptance evidence

The final integration record is [validation.md](validation.md). The principal independent checks are:

- [Final profile and tool-contract verification](final-profile-contract-verification.json): exact structural declarations and runtime-profile lint, separate from translated model-facing descriptions.
- [OpenAPI structure](openapi-final-structural-review.json) and [independent description review](openapi-final-independent-review.json): only reviewed description metadata may differ.
- [Production Python boundary review](final-python-production-boundary-independent-review.json): executable changes are explicitly enumerated, including the exact legacy image-line compatibility rule.
- [Frontend parser reconciliation](frontend-inventory-reconciliation.md), [final code comparison](frontend-global-passive-review.json), and [official browser results](frontend-official-e2e-validation.json): source comments, generated assets and actual browser behavior are checked separately.
- [Profile update and rollback matrix](profile-roundtrip-review.json), [artifact validity](artifact-contract-review.json), and [request fingerprints](reference-renderer-fingerprint-review.json): source reversal, materialized-profile synchronization and request reuse have distinct evidence.
- [Preserved content](preserved-content.md), [final residual review](final-residual-independent-review.json), and [Markdown exceptions](markdown-audit-reconciliation.json): multilingual data, historical compatibility and individually reviewed human placeholders remain explicit.

Completed gate logs are retained in [the log manifest](final-log-manifest.json). Intermediate failed runs remain documented. Test counts from overlapping runs must not be added together.

The local delivery includes [ADR 0121](../../adr/0121-english-maintenance-language-preserves-localization-and-contracts.md). The final pre-commit review corrected a delivery gap: an exact repository `.gitignore` exception now keeps this required policy visible even when a developer's global ignore excludes new ADR files. It appears in normal untracked-file discovery, so the `AGENTS.md` link can be delivered with its target. The global ignore configuration is unchanged; no files have been staged or committed.

The additional pre-commit review and its checks are recorded in [precommit-review.json](precommit-review.json). It adds a bounded standards/spec review to the original batch reviews rather than claiming another complete clause-level review of every changed file.

Check Markdown literals and metadata against the source commit with:

```bash
uv run python scripts/audit_translation.py --base 7b4f781ab2c57d3d4dd1d0a15bdc5c427262f250
```

This reports lost inline-code formatting, link changes and functional frontmatter changes. It also detects added invalid single-line JSON tool-call examples and changed/dequoted existing English sentence quotations, including Chinese quotation delimiters. It is a review aid, not proof of fidelity: translated human placeholders and intentional anchor updates require individual review, fenced examples require separate inspection, and paragraph/list coverage and semantic conditions still need comparison with the original.

## Literal contracts identified before translation

- Episode diagnostics accept specific Chinese and English ID forms in `server/services/project/episode_display.py`; producers must continue producing a recognized form.
- Historical migrations match Chinese prefixes such as `^画风[：:]`. These matchers and historical fixtures are compatibility syntax.
- Source-language defaults such as `中文`, language codes and missing-language behavior must not change when surrounding instructions become English.
- Reading-unit counters and speech-rate defaults retain their existing word/CJK classifications, including unknown and missing languages.
- Pending-authoring markers such as `【待编写】` and reference-image markers are preserved unless a separately reviewed producer/consumer change is necessary.
- Historical migration v13→v14 expands `Photographic`, `Anime` and `3D Animation` through the current style-template resolver. Preserve the baseline bodies of `style/live_premium_drama`, `style/anim_kyoto` and `style/anim_3d_cg` as compatibility data; translate their metadata only. Fixed-value migration tests protect this indirect dependency without changing migration code. The other style bodies may translate because existing projects retain expanded snapshots.
- Template slots, variant paths, tool names, schema keys, defaults and enums remain stable. Model-facing descriptions may translate, but require semantic review.
- Generated standards indexes must be changed through their generator and regenerated, not manually patched.

## Delivery states

**Current delivery: state 2, locally validated candidate, completed 2026-11-07.** Translation, explicit preservation exceptions, independent review, the complete affected local gates, browser checks and documentary builds are complete. [Validation](validation.md) retains failures and limits; [final-local-validation.json](final-local-validation.json) fixes the acceptance boundary.

1. **In progress:** inventory or translation/review is incomplete.
2. **Locally validated candidate:** all scoped items are reconciled, independent review is complete and required local checks pass.
3. **Behaviorally evaluated candidate:** approved real-model comparisons also pass. Finite evaluation does not prove universal equivalence.

State 2 follows reconciled scope and all required local checks, rather than a character scan or a selection of passing tests. State 3 remains pending until model, environment and budget are explicitly approved and the real-model comparisons pass. No paid evaluation was run.

See [evaluation.md](evaluation.md) for the frozen comparison criteria and [rollout.md](rollout.md) for update and rollback checks.

Validation results and unresolved failures are recorded in [validation.md](validation.md). The schema-capture script and reproduction limits are in [checks/README.md](checks/README.md).
