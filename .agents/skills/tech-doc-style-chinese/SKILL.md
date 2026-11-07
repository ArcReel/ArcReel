---
name: tech-doc-style-chinese
description: Use when writing, rewriting, proofreading, or reviewing Chinese technical documentation, product copy, UI copy, Markdown documents, API descriptions, operating manuals, troubleshooting, or operations documentation. Apply restrained, accurate, scannable Chinese technical writing; preserve original facts, constraints, and machine-readable content; read terminology and typography, API status copy, project overrides, or controlled Chinese technical-writing references as needed.
---

# Chinese technical documentation and product-copy guidelines

<!-- 作者：Fenng（GitHub：@Fenng） -->

## Scope

Use this Skill to write, rewrite, proofread, and review Chinese technical content, including:

- Documentation homepages, product introductions, solution pages, and changelogs
- API documentation, parameter descriptions, error codes, and FAQs
- Operating manuals, troubleshooting, operations runbooks, and security guidance
- UI copy, buttons, navigation, statuses, and notices

Do not rewrite code literals, JSON keys, URLs, API paths, database field names, commands, configuration options, or other machine-readable identifiers.

## Rule priority

Resolve conflicts in this order:

1. Preserve facts, logic, constraints, safety information, and legal meaning.
2. Follow explicit user requirements and target-project conventions.
3. Keep technical terminology and machine-readable content accurate.
4. Improve structure, meaning, tone, and scannability.
5. Handle punctuation, spacing, capitalization, and other typography last.

Never sacrifice higher-priority information for shorter sentences, greater certainty, or more uniform formatting.

## Factual fidelity

- Do not add dates, numbers, time limits, SLAs, capabilities, conditions, causal relationships, or conclusions absent from the original or reliable context.
- Do not remove prerequisites, scope, exceptions, risks, safety warnings, compatibility notes, or failure handling.
- Do not turn uncertainty such as possibility, plans, suggestions, or usual behavior into definite facts.
- When information is insufficient, retain the original meaning or explicitly mark it “pending confirmation”; do not fill gaps yourself.
- When editing quotations, regulations, contracts, original error text, or fixed copy supplied by the user, prefer retaining the original and listing recommendations separately.

Examples:

- `截止 4 月 12 日` -> `截至 4 月 12 日`
- `尽快处理` -> `在约定时限内处理〔具体时限待确认〕`
- Do not invent `截至 2026 年 4 月 12 日` or `30 分钟内处理`.

## Determine the task mode first

### Writing

- Establish audience, content type, factual sources, publication medium, and length first.
- When missing facts would change the conclusion, mark the gaps before organizing confirmed content.

### Rewriting

- Preserve facts, logical relationships, information hierarchy, constraints, and necessary exceptions.
- Deliver a complete rewrite by default; explain major semantic choices and information pending confirmation separately.

### Proofreading

- Handle only typos, punctuation, spacing, capitalization, and terminology consistency within the agreed scope.
- Do not change structure, tone, or factual expression unless requested.

### Reviewing

- List problems before suggestions, ordered by impact and citing specific original text.
- Do not modify files directly without authorization.

## Core writing rules

### Meaning and tone

- Accuracy before rhetoric; clarity before excitement.
- Use restrained, direct, actionable Chinese.
- Prioritize what something is, where it applies, what to do, and where to read next.
- Avoid greeting-style openings, promotional slogans, empty adjectives, and stacked jargon.
- By default, do not address the reader directly; use explicit roles such as “developer” or “implementation staff” when needed.
- Second-person address, exclamation marks, and brand voice are stylistic choices; project conventions or specific UI contexts may override defaults.

### Structure and sentences

- Give each paragraph one main information point.
- Give each sentence one clear grammatical core; do not stack multiple conditions, actions, and exceptions.
- Keep list items consistent in level, sentence form, and information density.
- Make headings reflect purpose rather than only abstract nouns.
- Remove repetition without removing facts, conditions, or exceptions.
- When references may be unclear, replace “该”, “其”, “此”, or “上述” with specific names.

### Terminology and typography

- Use one preferred term per concept; do not rotate synonyms merely for variety.
- Use corner brackets `「」` for quotations in Chinese body text, with `『』` for nested quotations; project, regional, or fixed-quotation conventions may override this.
- In visible body text, insert one half-width space between Chinese and English, Arabic numerals, or version numbers, for example `使用 API 获取数据`, `等待 3 秒`, and `版本 2.0 已发布`.
- Insert one half-width space between Chinese and inline code; determine link spacing from displayed text. Do not add spaces around full-width Chinese punctuation or modify code, link targets, official names, or user-specified literals.
- Keep each Chinese paragraph on one source line rather than hard-wrapping for line width; preserve code blocks, tables, headings, and list structures.
- Prefer project or official spellings for terms, product names, and abbreviations; do not replace mechanically.
- Do not run bulk typography replacements directly; protect Markdown, code, and machine-readable content first.

See [Terminology and typography](references/terminology-and-typography.md) for detailed rules.

## Handle by content type

### Entry and introduction pages

The first paragraph should prioritize:

- What the content covers
- Who it is for
- Where to start reading

Avoid repeating the same information in headings, body text, and action buttons.

### API documentation

- Protect request methods, paths, fields, and values with code formatting.
- Give each parameter-description column one meaning, and state type, unit, default, limits, and whether required.
- Translate statuses and errors according to actual meaning, not mechanically from individual English words.
- State prerequisites, successful results, failure results, and recovery methods.

See [API status and error copy](references/api-status-copy.md) for detailed rules.

### UI copy

- Buttons state an action and target rather than repeat the page title.
- Error messages explain what happened, its impact, and how to recover.
- Dangerous actions state the object, consequences, and reversibility.
- Distinguish empty states for no data, not yet created, no permission, and loading failure.

### Operations and troubleshooting

For operating manuals, troubleshooting, operations runbooks, security guidance, and multistep API workflows, read and apply [Controlled Chinese technical writing](references/controlled-technical-chinese.md). Do not mechanically apply these rules to brand copy, narrative text, or fixed quotations.

## Project overrides

First check the target project's own `AGENTS.md`, glossary, brand guidelines, existing documentation, and user instructions. Do not treat sample conventions in this Skill repository as target-project conventions.

When creating project overrides, consult the [Project override template](references/project-overrides-example.md) and place the actual file in the target project.

## Editing workflow

Select steps according to task mode; proofreading performs only agreed checks and modifications.

1. Confirm task mode, content type, audience, and project conventions.
2. Mark facts, numbers, constraints, quotations, and machine-readable content to establish immutable boundaries.
3. Fix semantic errors, ambiguities, omissions, and inconsistencies.
4. Adjust information order, paragraphs, headings, and lists.
5. Handle tone, terminology, punctuation, spacing, and capitalization.
6. Compare with the original to recheck facts, conditions, negation scope, causality, and degree of certainty.
7. Run applicable lightweight checkers and manually judge warnings and style hints.

## Final checks

- Were unsupported dates, numbers, time limits, capabilities, or conclusions added?
- Were conditions, exceptions, risks, units, defaults, or recovery methods omitted?
- Are subjects, objects, negation scope, causality, and certainty still accurate?
- Does each concept use one consistent term?
- Do headings, body text, cards, and buttons repeat information?
- Are code, paths, fields, commands, and quotations unchanged?
- Can the target reader scan and execute the content easily?
- Do hard wraps remain in paragraphs, and is Chinese/Western spacing correct at joins?
- Do project overrides come from the target project rather than the sample file?

## Reference routing

- Terminology, jargon, wrong words, capitalization, punctuation, Chinese/Western spacing, and paragraph wrapping: read [Terminology and typography](references/terminology-and-typography.md).
- API statuses, error codes, and common English status terms: read [API status and error copy](references/api-status-copy.md).
- Operating steps, troubleshooting, security guidance, and operations documentation: read [Controlled Chinese technical writing](references/controlled-technical-chinese.md).
- Establishing target-project conventions: read the [Project override template](references/project-overrides-example.md).
