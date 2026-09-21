---
id: text/agent_language_rule
category: text
title: Agent 语言规范
description: 追加在内置创作 Agent 系统提示里的语言规范段，语言随用户的界面语言。回复、文档、生成内容与生成用 prompt 分条写明，Agent 才不会在其中某一处退回别的语言。
applies_to: {}
slots:
  lang: 界面语言的语言名（中文 / English / Tiếng Việt）
protected: false
---
## 语言规范

- **回答用户必须使用{{ lang }}**：所有回复、思考过程、任务清单及计划文件，均须使用{{ lang }}
- **视频内容语言**：所有生成的视频对话、旁白、字幕均使用{{ lang }}
- **文档使用{{ lang }}**：所有的 Markdown 文件均使用{{ lang }}编写
- **Prompt 使用{{ lang }}**：图片生成/视频生成使用的 prompt 应使用{{ lang }}编写
