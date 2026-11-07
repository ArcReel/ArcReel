---
status: accepted
---

# Migrate domain terminology in one pass without compatibility aliases

Rewrite all of `GLOSSARY.md` into the flat, short-entry format defined by `CONTEXT-FORMAT.md`: use the canonical Chinese product name as the heading, with the machine identifier in parentheses; definitions use 1–2 sentences to say only what it is; `_Avoid_` lists only synonyms and ambiguous terms. Remove statement/mechanism entries and generic technical-concept entries; place cross-module invariants from removed content that cannot be inferred from code in the docstring of the sole source-of-truth function or module.

## Decisions

- **Rewrite everything in one pass without compatibility aliases.** Replace old entry wording directly with canonical product names (for example, “隔离草稿” → “待修复草稿”, “内容待确认” → “内容确认”, and “设计图” → “资产图”), without retaining redirect entries from old to new names in the new glossary. Repository-wide code behavior remains unchanged: the glossary standardizes only human wording (documents, commits, PRs, agent prompts), rather than renaming code symbols.
- **Put machine identifiers in headings, not in _Avoid_.** Glossary readers need to know the code symbol corresponding to a product concept; heading parentheses provide the shortest cross-reference. `_Avoid_` specifically lists synonyms and ambiguous terms that should be avoided; machine identifiers do not belong there.
- **Remove statement/mechanism/generic technical-concept entries.** The glossary answers only "what is it"; "how it works," "the relationship between two things," and "the three meanings of a field" belong to implementation documentation, carried by ADRs and code docstrings. Write cross-module invariants from removed entries that cannot be inferred from code into the docstrings of their sole source-of-truth code.
- **`GLOSSARY.md` owns canonical product names.** Spec #1840 is the input to this migration; after migration, maintain current canonical product names and synonyms to avoid only in `GLOSSARY.md`. ADRs do not duplicate vocabulary snapshots that evolve with product language.

## Explicitly rejected

- **Keeping alias entries from old to new names**: aliases prevent convergence, because someone will continue writing the old name and cite the alias entry to justify it. A one-time switch costs little (the glossary is not an API contract); maintaining aliases continuously costs more.
- **Migrating in batches or replacing sections gradually**: glossary entries cross-reference one another, so partial replacement creates an intermediate state with cross-references between old and new wording that is more error-prone than complete replacement.
- **Renaming code symbols along with product names**: the glossary standardizes human wording, rather than code identifiers. Context disambiguates code symbols with the same name and different meanings (for example, `scene` is both an asset type and a scene entry in a script skeleton); this is not resolved at the glossary level.

## Consequences

- New documents, commit titles, PR descriptions, and agent-prompt templates throughout the repository uniformly use the product names established by this migration; old names in new text are wording errors.
- Do not mechanically replace old names in existing code comments and documents in bulk. Update them when modifying the containing file, avoiding broad diffs without substantive changes.
