---
paths:
  - "frontend/src/i18n/**"
  - "lib/i18n/**"
  - "website/docs/**"
  - "README.md"
  - "README.en.md"
  - "CONTRIBUTING.md"
---

# 中文文案与文档

编写或修改中文界面文案（`frontend/src/i18n/zh`、`lib/i18n/zh`）与用户文档（`website/docs`、README、CONTRIBUTING）前，用 Skill 工具调用 `tech-doc-style-chinese`；改完用 `uv run python .agents/skills/tech-doc-style-chinese/scripts/lint_copy_rules.py <文件>` 自查。中文文案先交维护者校对；其他语言先补齐 key 以通过校验，中文定稿后再更新译文。
