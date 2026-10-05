---
id: text/agent_language_rule
category: text
title: Agent 语言规范
description: 追加在内置创作 Agent 系统提示里的语言规范段，语言随用户的界面语言。
stage: agent_session
invoked_by:
  kind: user_action
  name: agent_session
applies_to: {}
slots:
  lang: 界面语言的语言名（中文 / English / Tiếng Việt）
protected: false
---
## 语言规范

- **回答用户必须使用{{ lang }}**：所有回复、思考过程、任务清单及计划文件，均须使用{{ lang }}
- **视频内容语言**：所有生成的视频对话、旁白、字幕均使用{{ lang }}
- **文档使用{{ lang }}**：所有的 Markdown 文件均使用{{ lang }}编写
- **视觉 Prompt 使用英文（English）**：图片生成 / 视频生成的视觉描述 prompt（画面、动作、运镜、环境音等）一律使用英文编写，即便界面语言不是英文——便于外部图像 / 视频工具解析；对白 / 口述类内容仍使用{{ lang }}
