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

Only when writing or editing Chinese passages in interface copy, user documentation, or Agent documentation, invoke `tech-doc-style-chinese` with the Skill tool and read `docs/standards/copy.md`. Afterwards, you can self-check with `uv run python .agents/skills/tech-doc-style-chinese/scripts/lint_copy_rules.py <文件>`.
