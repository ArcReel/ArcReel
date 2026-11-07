# Reproducing schema captures

Use the same frozen Python environment for the baseline archive and candidate, with an immutable archive of commit `7b4f781ab2c57d3d4dd1d0a15bdc5c427262f250`. Run from the candidate repository root:

```bash
PYTHONPATH=/absolute/path/to/baseline-archive uv run python docs/plans/english-translation/checks/capture_contracts.py /tmp/translation-schemas-baseline.json
uv run python docs/plans/english-translation/checks/capture_contracts.py /tmp/translation-schemas-candidate.json
cmp /tmp/translation-schemas-baseline.json /tmp/translation-schemas-candidate.json
```

Do not start application lifespan, connect to real project storage or use generation credentials. Importing declarations is sufficient; no model or media calls are required. Verify that the baseline import paths resolve to the archive, rather than assuming environment configuration did so.

The capture preserves schema properties, types, enums, required fields, defaults, limits, discriminators, tool identities and handlers. It removes description metadata only at known schema nodes, preserving properties *named* `description` and arbitrary instance/default data. Invalid empty-duration cases retain their original exception messages. The additional overview and source-revision scope models are captured separately. Dynamic cases cover all three content modes, drama/reference plans, one/multiple/empty duration tiers and new-asset variants.

Equality does not prove prompt fidelity, model behavior or completeness of the scenario matrix. Compare descriptions independently and retain the real-model release boundary in the parent evaluation protocol.

## Frontend passive-code comparison

With frontend dependencies installed from the existing lockfile, run:

```bash
node docs/plans/english-translation/checks/capture_frontend_passive.cjs 7b4f781 /tmp/translation-frontend-passive.json docs/plans/english-translation/frontend-eslint-diagnostic-review.json
```

This compares every changed tracked frontend TypeScript file with its source using the TypeScript printer with comments removed. It records baseline/candidate SHA-256 values. The optional fourth argument permits exactly 17 independently reviewed diagnostic-expectation string nodes in `src/lint-class-guards.test.ts`, matched by original literal ordinal, value and parent kind, and requires the exact reviewed candidate hash. Every other code node must remain equal. Without that explicit record, any executable string difference fails. The comparison does not certify comment fidelity, directive semantics or localized runtime behavior; per-batch directive, example and clause checks remain separate.

## Production CSS preservation

TypeScript comment removal does not cover Tailwind source-candidate extraction. Build baseline and candidate with the same frozen frontend environment, using separate output directories outside the source trees:

```bash
pnpm exec vite build --outDir /absolute/path/to/baseline-build --emptyOutDir
pnpm exec vite build --outDir /absolute/path/to/candidate-build --emptyOutDir
uv run python docs/plans/english-translation/checks/compare_frontend_css.py /absolute/path/to/baseline-build /absolute/path/to/candidate-build /tmp/translation-css-review.json
```

Run each build from its corresponding frontend tree. The comparison ignores hashed filenames but requires identical CSS content multisets. It catches both removed and added utilities caused by comment token boundaries or translated prose; it does not replace semantic review or tests.

## Full OpenAPI declarations

Capture the original archive and candidate with the same frozen Python environment:

```bash
uv run python docs/plans/english-translation/checks/capture_openapi.py /absolute/path/to/baseline-archive /tmp/translation-openapi-baseline.json
uv run python docs/plans/english-translation/checks/capture_openapi.py /absolute/path/to/candidate /tmp/translation-openapi-candidate.json
```

The capture verifies the imported application path, disables dotenv loading and uses a temporary data-root directory. It does not enter application lifespan, run a server or call a provider. Compare the complete `openapi` values, allowing only individually reviewed description metadata changes. Property names, operation IDs, types, required fields, defaults, enums, constraints and examples must remain equal.

## Frontend inventory coverage

```bash
node --test docs/plans/english-translation/checks/frontend_comment_inventory.test.cjs
node docs/plans/english-translation/checks/frontend_comment_inventory.cjs /tmp/translation-frontend-comments.json
```

This parser-based inventory includes JSX comments, comments after nested template expressions, tests, locales, types and configuration. The earlier bare-scanner coverage flaw and the scopes it reopened are recorded in [the reconciliation](../frontend-inventory-reconciliation.md). Code equality and previously reviewed translated pairs remain valid; they did not prove inventory completeness.

## Emitted asset comparison

```bash
node --test docs/plans/english-translation/checks/compare_emitted_asset_graph.test.cjs
node docs/plans/english-translation/checks/compare_emitted_asset_graph.cjs /absolute/path/to/baseline-build /absolute/path/to/candidate-build /tmp/translation-assets.json
```

Inspect `raw` first. Final acceptance in this task requires identical paths and bytes for all 161 emitted files; no filename normalization was necessary. The optional normalized comparison recognizes complete JavaScript string/template tokens, quoted HTML `src`/`href` values and CSS `url()` references that resolve to emitted files. It compares a unique bijection, the ordered recognized-reference graph and every other byte. It is not a general JavaScript import-semantics or arbitrary HTML/CSS parser. The helper ignores symlinks; the independently rehashed three actual build trees contained none. A rejected raw and normalized comparison exits nonzero. Neither this helper nor raw byte equality proves model behavior or universal runtime equivalence.
