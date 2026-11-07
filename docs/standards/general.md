---
paths:
  - "**"
---

# General

## Code and documentation

### Comments describe only current behavior and constraints

Readers have only the code in front of them. Reasons for changes, issue/PR numbers, and temporal wording such as “recently,” “this change,” or “tested” belong in commit messages and PR descriptions. In comments they become false when the code changes, and no one updates them. When modifying a file, rewrite existing references of this kind as statements about current behavior.

Exception: this rule applies to comments in code and tests; topical documents under `docs/` (ADRs, runbooks, etc.) may cross-reference spec numbers.

### Defend only against normally reachable scenarios

Normally reachable means possible during regular operation: normal product UI and Agent flows; supported deployments, including concurrent requests and restarts after graceful shutdown; predictable external failures such as network errors, provider errors/rate limits, and full disks. Do not add checks, fallbacks, or compensation for paths reachable only through arbitrary force-kill, power loss, user edits to project files or permissions, or hardware failures. When rejecting a review finding on this basis, state the abnormal condition it depends on. Every defensive branch requires maintenance and tests, and review loops repeatedly revisit the same class of finding.

Exception: guarantees at an existing single boundary without adding branches, such as fsync on atomic writes.

### Fix gate findings in code; exempt only confirmed tool false positives

This rule covers gate tools: lint, type checking, dependency checks, and audit scripts. Fix code for their findings rather than adding baselines, count thresholds, or disabling entire rules; those approaches also hide future real problems. Exemptions are only for confirmed false positives. `SUPPRESSION-REASON` in `scripts/audit_conventions.py` checks for a reason on the same line as an inline suppression; review judges whether it is valid. Style or grammar suggestions from review bots (such as LanguageTool) are not gate tools and are outside this rule.

When inline syntax is unavailable, put a one-line reason beside each configuration entry:

- actionlint: exact error-message regexes under `paths.<文件>.ignore` in `.github/actionlint.yaml`.
- deptry `DEP002` / `DEP004`: per-package entries in `[tool.deptry.per_rule_ignores]`.
- ESLint file-wide disable: a `files` override in `eslint.config.js`.

For policy-threshold findings, such as Dependabot cooldown days, adjust configuration as the tool requires rather than adding an exemption.

Reasons explain why the tool is wrong here or why this form is necessary. “Too much trouble,” “temporarily,” and “later fix” are invalid; “React setter references are stable,” “mount-only initialization,” and “third-party package has no types” are valid.

Exception: import-linter `ignore_imports` (`[tool.importlinter]`) is a baseline for existing violations. Each ignore disables the layer contract there and encourages same-direction dependencies. Try removing the dependency edge before adding an entry; if impossible, add a comment above it explaining why or citing an ADR.

### Feature changes and their documentation belong in the same PR

Update corresponding pages in `website/docs/` for these changes (page responsibilities are in `CONTRIBUTING.md`'s “各页职责”). Users following stale documentation fail while it lags behind code.

- New content modes or generation modes.
- New providers or media capabilities.
- Deployment directories, ports, or environment variables.
- Data directories, backup procedures, or migration behavior.
- Public APIs, licenses, or commercial-use terms.
- External Agent installation: also update `public/agent-installation-guide.md`.

### Prefills and defaults use valid values from their effective context

If a default is valid only for one provider, model, or type (a voice belonging to one speech model, a media type available only on some providers), derive and validate it in the context where it actually applies: the final saved model or currently selected provider, not a global default or fixed set. The parent choice may be inferred or changed by the user. A fixed default then becomes invalid: creating a project saves a combination guaranteed to fail, or a form succeeds initially and fails at the backend's second step, leaving an orphan entry.

### When changing a capability dimension's source or handling, list and update every read site

One capability dimension is read in multiple places: backend request construction, execution gates, connectivity tests, pre-submission cost estimates (both built-in and custom-provider paths), and settlement metering. When switching its source to effective capabilities with user overrides, or changing its handling (such as turning silent omission into an error), first search for every read site, list them in the PR, and update them together. Changing only visible sites leaves others on old rules: missing first frames or references are skipped while paid generation proceeds, user-restricted capabilities pass a trial run and incur charges, or silent-video estimates admit an over-budget sound request.

Exception: if a PR intentionally changes only some read sites, explain why the others retain old behavior and identify the responsible issue.

### Define a value's format or range once; producers, validators, and consumers reference it

A value is often interpreted by several parties: allocation/output functions, schemas and violation checks, request construction, capability resolution, comparisons, and UI. Independent definitions (regex widths, integer types, algorithm prefixes, entry kinds) drift when one changes: a server rejects its own IDs; validation accepts `1.0` while resolution accepts integers only; a read endpoint adds a prefix while writes compare bare values and report conflicts; installation dialogs fail to recognize newly shareable entry types. For new or changed values, reference one definition (constant, type, or parser). If impossible, list every read/write site in the PR and add a round-trip test driving every validator and consumer with actual producer boundary output.

### Name/path guards test every spelling that reaches the same target

Lint rules and guards such as `scripts/audit_conventions.py` identify forbidden modules or functions from source text, but targets have multiple spellings: `@/` aliases, relative paths, `..` segments, and re-exports; deletion functions such as `from os import remove as rm` and `import os as fs`, plus `aiofiles.os`. Recognizing only one spelling lets others bypass the guard while giving false confidence. When adding or changing a guard, list every spelling reaching the target and test that each is reported.

## Copy

### User-visible text uses translation keys in every supported language

Frontend UI text belongs in `frontend/src/i18n/`; backend errors, events, and other user messages belong in `lib/i18n/`. Language directories define the supported languages. Hardcoded text appears unchanged in other locales, mixing languages. Type checking and `tests/unit/lib/i18n/` enforce key-set consistency; review judges whether the text should use a translation key.

Exceptions: developer-only logs and exceptions; data-driven names such as model display names come from catalog entries, without translation keys.

### Failure summaries state the cause and next step first, with raw text collapsed

Use localized summaries explaining the cause and way forward, without problem codes, internal IDs, or raw server text. Put raw text in expandable details. If no cause is known, the raw text is the summary.
