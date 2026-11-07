---
name: update-docs
description: Update user-facing documentation from recent git changes (bilingual READMEs, getting-started guides, deployment, Jianying export, etc.). Invoke manually.
disable-model-invocation: true
---

Update user-facing documentation from recent code changes. Two scans: gap finder reads git history for shipped capabilities missing from docs; fact checker reads docs for claims inconsistent with current code. Edit wording and structure directly; confirm factual changes with the user first.

## Applicability

In-scope documents form two groups:

- **Full (`full`)**: frequently updated, broad documents. Both scans cover these and they participate in baseline calculation.
- **Fact-check only (`fact-check`)**: infrequently updated, narrow documents. Only fact checking applies; they do not participate in the baseline.

A page needs capability-bearing prose where a new feature could form a paragraph to qualify for `full` coverage. Pure navigation/index pages are `fact-check` only even if broad. The baseline is the full-group page whose body changed least recently; including infrequent pages pins scans to old dates and increases candidate-commit noise.

Every `.md`/`.mdx` page under `website/docs/` declares `update_docs` frontmatter as full, fact-check, or `none` (explicit exclusion). New pages must declare it or collection and CI consistency fail. A few non-Docusaurus root files such as `README.md` and `CONTRIBUTING.md` remain enumerated in the collector. CONTRIBUTING's “各页职责” registers all published source pages, including `none` pages; CI checks missing and extra entries both ways.

README.en.md mirrors README.md, with Chinese as source. Do not scan it separately; after changes, compare the complete pair for consistency (step 6).

User-documentation sources live under `website/docs/`, published at docs.arc-reel.com. Exclude provider price tables and unpublished internal documents (ADRs, research, provider SDK docs under `docs/`). New published pages declare `update_docs` using the criteria above and update CONTRIBUTING's page-responsibility table.

## Steps

### 1. Collect

Run `bash .agents/skills/update-docs/scripts/collect-changes.sh` for baseline, all candidate commit titles, full-group documents, and fact-check documents. Missing coverage declarations make it fail. Add the indicated frontmatter and CONTRIBUTING page-responsibility entries, then rerun.

### 2. Gap scan: git history to documentation

Delegate one read-only subagent (`subagent_type: Explore`, prompt `.agents/skills/update-docs/references/gap-finder.md`) with repository root, full-group documents, and complete candidate commit titles from collection. It lists missing capabilities for every full-group document.
Completion: receive the subagent's missing-item list.

### 3. Fact check: documentation to code

Delegate read-only subagents in parallel for every fact-check document (`subagent_type: Explore`, prompt `.agents/skills/update-docs/references/fact-checker.md`).
Completion: every document has a fact-check result.

### 4. Classify

Combine findings, deduplicate by document/location (both scans often find the same issue, such as provider lists), then classify each as wording/structure, factual change, or new capability.
Completion: every item is deduplicated and classified.

### 5. Edit

- **Wording/structure**: edit directly.
- **Facts**: first ask the user to confirm location, current claim, recommendation, and evidence.
- **New capabilities**: decide whether significant enough for core positioning/features. Ask before adding significant ones to the relevant section; omit minor ones from README (release-please changelog covers release history).

Chinese copy stays concise and precise, without translationese, colloquialisms, or unnecessary metaphors.
Completion: all authorized edits are applied.

### 6. Bilingual comparison

After changing README.md, compare the complete README.md and README.en.md section by section. Align English to the Chinese source, including preexisting divergence.
Completion: both documents correspond consistently throughout.

### 7. Summary

Report changed documents/sections, confirmed factual changes, new capabilities added, English alignment including old divergence, and matters left for the user. Do not commit or push.

## Factual changes

These need user confirmation before editing:

- Commands and environment variables: install/start/deployment commands, `.env` settings.
- Providers, models, and capabilities: lists, default models, capability parameters. These are external data; flag doubts without guessing or supplying values.
- Version and dependency requirements.
- External links.
