---
paths:
  - "frontend/src/i18n/zh/**"
  - "lib/i18n/zh/**"
  - "website/docs/**"
  - "README.md"
  - "CONTRIBUTING.md"
  - "docs/**"
  - "**/CLAUDE.md"
---

# Chinese copy

These rules apply to Chinese passages in localized resources, Chinese-first public documentation, and retained Chinese examples. Internal maintenance documentation and Agent instructions follow the [English maintenance-language policy](../../AGENTS.md#maintenance-language). Existing workflow requirements for generated-content languages remain unchanged.

Use the `tech-doc-style-chinese` skill for writing, factual fidelity, and typography. This file contains only ArcReel-specific overrides and additions. Classify copy by paragraph: one file may contain interface, user-documentation, and Agent-documentation copy. `frontend/src/i18n/locale-wording.test.ts` and `tests/unit/lib/i18n/test_locale_wording.py` check i18n-pack punctuation; review punctuation only in document prose outside their coverage.

## General

### Use the glossary's Chinese domain terms; never display synonyms listed under `_Avoid_`

Different names for one concept make users think they are different features and search for nonexistent entry points; translations also become inconsistent. Use the Chinese labels in `GLOSSARY.md`.

### Join numeric ranges with ～; write a shared unit once and percentages at both ends

Examples: `2～10 秒`, `10%～20%`. Hyphens are easily confused with minus or joining signs.

Exception: preserve syntax users must enter literally, such as duration-tier shorthand `3-15`.

### Preserve field names before placeholders and quoted raw values

Examples include `{field}:` in `{field}: 引用路径越界：{path}` and the quotes around `'{value}'`. These are data field names and raw values used to compare with project files; full-width substitutions obscure what is original.

### Use written Chinese for user and Agent documents, avoiding translationese, colloquialisms, and AI filler

- **Translationese**: copied English structures, noun piles, abstract-noun subjects, 对……进行……, consecutive passive constructions, and stacked 的. Use short verb-centered Chinese sentences; for example, replace 保留机器字面量的写法 with 保持原样 and 多占一次注意力 with 增加阅读负担.
- **Colloquialisms**: conversational expressions such as 换着叫 or 不进界面; use 用不同叫法 or 不出现在界面上.
- **AI filler**: polished-sounding patterns without information, such as four-character parallelisms, openings like 随着……的发展, formulaic conclusions, excessive bold, or decorative headings. Remove the pattern and state the information.

Apply writing principles in natural Chinese. Positive phrasing from `writing-for-agents`, for example, remains subject to normal Chinese expression; ordinary instructions such as 不加句号 remain valid.

Exceptions: four-character words, passive sentences, and formal 进行 are not inherently wrong. Clear source wording need not change. Rewriting must preserve facts, conditions, and certainty. Short interface copy may be more conversational and is outside this rule.

## Interface copy

For text skimmed during operation: buttons, labels, hints, states, errors, and events in `frontend/src/i18n/zh` and `lib/i18n/zh`. Good copy makes the next action obvious and preserves its meaning in translation.

### Omit the subject by default; address the user as 你 when an actor must be identified

Write 保存后生效 rather than 你保存后生效. Users are the implied actor; explicit subjects add reading burden. Distinguish users from systems when needed, such as 你取消了任务 or 系统已取消任务. Use 你 consistently, corresponding to English you.

### Use 请 only when requesting an action would otherwise sound abrupt

Placeholders, tooltips, empty states, and status copy state information directly, such as 输入项目名称. Repeating 请 in every sentence makes genuine action requests less visible.

### Buttons, labels, tooltips, and input hints have no final period

These name things or give short instructions; a period adds visual noise. Multi-sentence explanations and errors retain periods.

### Confirmation buttons state the result directly

Use 删除, 发布, or 覆盖 on the primary button, so users understand the outcome from the button alone, especially for dangerous actions.

### Use 无法… when an operation cannot complete; reserve 抱歉 for ArcReel-caused problems

无法连接供应商 states the condition and naturally leads to a next step. For invalid user input, state the requirement directly. Failure-message structure is covered by “Failure summaries state the cause and next step first, with raw text collapsed” in `general.md`.

Exception: task-state labels such as 生成失败 remain valid.

### Ongoing states and menu items requiring more input use one …

Examples: 生成中… and 导出…. Do not use the skill's …… in the interface: a single character cannot break across lines and uses less space.

## User documentation

For paragraphs users consult with a task or question, in `website/docs`, README, and CONTRIBUTING. Good documentation is findable, actionable, verifiable after completion, and does not claim capabilities beyond the product. Address readers as in interface copy.

## Agent documentation

For paragraphs developers or Agents use to make decisions, in `docs/**` and `CLAUDE.md` files at every level. Follow `writing-for-agents`.
