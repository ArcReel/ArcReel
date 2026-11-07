# When to involve the user

Read this document when deciding whether to decide independently, open an issue, or ask the user. It applies to every agent: interactive sessions, AFK roles, review loops, and lesson sweeps.

## Decision order

Apply these checks in order and stop at the first match:

1. **A factual question** (existence, correctness, conflicting evidence): verify it yourself, reach a conclusion, and continue.
2. **The answer follows from existing sources of truth**: follow that answer. Sources include specs, ADRs, standards, `GLOSSARY.md`, the design system, product positioning (`PRODUCT.md`), and precedents in existing code.
3. **No answer follows, and a wrong choice has low cost**: decide according to the product positioning and leave one line of reasoning in the PR for the user to inspect.
4. **No answer follows, and a wrong choice has high cost**: ask the user.

A wrong choice has high cost if any of these applies:

- It is hard to reverse.
- It has external effects: public APIs, published content, actions that incur real costs, licenses, or security boundaries.
- It changes behavior or data that existing users rely on: removing features, changing defaults, or migrating data.
- It establishes an ongoing direction: the scope of a new capability or a new interaction pattern.
- It expands beyond the authorized scope.

Judge by those costs alone, regardless of whether the decision is technical or product-related.

## How to ask

Combine matters needing the user into one message at closing. Number each item, give a recommended option, and phrase it so “yes” accepts the recommendation. At built-in workflow pauses (such as the pause boundaries in `afk-team-workflow`), ask in the same format.

## Handling matters that do not need the user

- **A real defect outside scope**: complete these three steps in order, then open a `ready-for-agent` issue.
  1. Qualification: reproduce it or provide code evidence that it affects correctness or user experience. Do not open issues for “could be better” suggestions.
  2. Deduplication: if a matching open issue exists, add an evidence comment there.
  3. Consolidation: combine small defects found in the same module during one batch into one issue.

  If the fix meets the high-cost criteria, open a `needs-triage` issue instead, with options and a recommendation.
- **Suggestions of uncertain value**: if a mistake led to the suggestion and that mistake meets the [lessons.md](lessons.md) “Closing self-check” criteria, record a lesson candidate; otherwise discard it.
