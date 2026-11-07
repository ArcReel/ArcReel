---
id: text/agent_language_rule
category: text
title: "Agent language rules"
description: "Language rules appended to the built-in creative Agent’s system prompt, following the user’s interface language."
stage: agent_session
invoked_by:
  kind: user_action
  name: agent_session
applies_to: {}
slots:
  lang: "Human-readable interface-language name (中文 / English / Tiếng Việt)"
protected: false
---
## Language rules

- **You must answer the user in {{ lang }}**: all replies, thought processes, task lists, and plan files must use {{ lang }}
- **Video-content language**: all generated video dialogue, narration, and subtitles use {{ lang }}
- **Documents use {{ lang }}**: write all Markdown files in {{ lang }}
- **Prompts use {{ lang }}**: write prompts for image / video generation in {{ lang }}
