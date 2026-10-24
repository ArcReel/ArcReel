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

# 中文文案

编写或修改中文界面文案、用户文档与 Agent 文档前，用 Skill 工具调用 `tech-doc-style-chinese`，并读 `docs/standards/copy.md`；改完可用 `uv run python .agents/skills/tech-doc-style-chinese/scripts/lint_copy_rules.py <文件>` 自查。
