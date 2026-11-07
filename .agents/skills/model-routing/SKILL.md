---
name: model-routing
description: Choose models when delegating. Use when selecting a Category, model, and effort for a subagent or teammate, or choosing a cross-Harness reviewer for a plan or implementation.
---

# Model routing

Before delegation, choose a Category by task nature, then look up its Harness, model, and effort in [models.md](models.md). If the preferred option is unavailable, use the same row's fallback and report the downgrade.

## Category

| Category | Suitable work |
|---|---|
| `quick` | Trivial tasks and simple changes |
| `unspecified-low` | Ordinary general-purpose tasks |
| `unspecified-high` | Complex general-purpose work, including unclear goals or boundaries |
| `deep-low` | Clear goals and acceptance criteria requiring deep autonomous execution; key decisions can be resolved through evidence |
| `deep-high` | Clear deep-work goals and acceptance criteria, but key decisions cannot be settled by evidence alone |
| `ultrabrain` | Extremely difficult logical reasoning and complex algorithms with clear goals |
| `architect` | System architecture and technical tradeoffs; planning only, no implementation |
| `visual-engineering` | Visual design, UI/UX, frontend, styling, animation, and design systems |
| `artistry` | Creative, unconventional problem solving |
| `writing` | Documentation, technical writing, and other prose |

The `deep-*` and `ultrabrain` categories route to GPT. GPT tends toward overcompliance and, under ambiguous goals, overdesign, overdefense, and overtesting. These three require clear goals. Use `unspecified-high` for complex work with unclear goals or boundaries.

## Review

Plan and implementation reviews use separate Review routing: the reviewer is from the opposite Harness to the implementer, as mapped in the Review table in [models.md](models.md).
