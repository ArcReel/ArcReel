# Chinese technical documentation and product-copy guidelines

This is a writing Skill for Chinese technical documentation, product copy, and UI copy. The formal, installable rule entry point is [SKILL.md](SKILL.md); this page explains only the design goals and content structure without duplicating the full rules.

## Goals

- Accuracy before rhetoric
- Clarity before excitement
- Factual fidelity before expression optimization
- Actionability before abstract summaries
- Scannability before accumulated explanations

## Applicable content

- Documentation homepages and product introductions
- API documentation, parameter descriptions, and error codes
- Operating manuals, troubleshooting, and operations runbooks
- FAQs, changelogs, and solution pages
- Buttons, navigation, statuses, and notices

Code literals, JSON keys, URLs, API paths, database fields, commands, and configuration options are outside natural-language rewriting scope.

## The most important boundary

Copy improvements must not change facts. Do not add information absent from the original:

- Dates and years
- Numbers and units
- Processing deadlines and SLAs
- Product capabilities
- Prerequisites and causal relationships
- Definite conclusions

When information is insufficient, retain the original meaning or explicitly mark it pending confirmation.

## Content structure

The core entry point [SKILL.md](SKILL.md) covers:

- Rule priority
- Factual fidelity
- Writing, rewriting, proofreading, and review workflows
- Core meaning, structure, and tone rules
- Reference selection by content type
- Final checks

Read detailed rules as needed:

- [Terminology and typography](references/terminology-and-typography.md)
- [API status and error copy](references/api-status-copy.md)
- [Controlled Chinese technical writing](references/controlled-technical-chinese.md)
- [Project override template](references/project-overrides-example.md)

## Controlled Chinese technical writing

Operating manuals, troubleshooting, security guidance, and operations documentation can use controlled Chinese writing inspired by ASD-STE100, focusing on:

- Consistent terminology
- Conditions before actions
- A clear main action in each step
- Explicit actors, objects, and results
- Unambiguous references, negation scope, and degree of certainty

This module is not a Chinese version of ASD-STE100 and does not imply output complies with ASD-STE100. English controlled-vocabulary, part-of-speech, and sentence-length rules cannot be directly transferred to Chinese.

## Project overrides

The general Skill contains no business terminology effective by default for any project. If a target project has version, brand, terminology, or information-architecture conventions, create an override file in that target project.

The [Project override template](references/project-overrides-example.md) provides structure only, without default business rules.

## Automated checks

The repository includes a lightweight checker with these result categories:

- `error`: highly certain errors that fail checks by default
- `warning`: context-dependent suspicious wording requiring human judgment
- `style`: project style or terminology preferences

A separate zero-dependency script restores paragraphs manually wrapped for source line width to one paragraph per line, handling Chinese/Western spacing at joins. Code blocks, tables, headings, list structures, and explicit line breaks remain unchanged.

Automated checks cannot prove semantic correctness or replace author judgment. Factual fidelity, condition order, reference relationships, and oversimplification still need manual review.

<!-- 作者：Fenng（GitHub：@Fenng） -->
