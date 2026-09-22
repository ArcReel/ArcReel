---
id: text/source_overview
category: text
title: 源文总览
description: 从项目源文提炼项目概述（故事梗概、题材、主题、世界观与源语言），供之后所有生成环节的提示词使用。
applies_to:
  source_kind:
  - novel
  - screenplay
output_schema: lib.project.project_manager:ProjectOverview
slots:
  source_kind: 源文类型
  target_language: 输出语言，取项目源语言，缺省为中文
  source_content: 项目 source 目录下全部源文拼接的正文
protected: false
---
{{ variant("text/source_overview/task", source_kind) }}

**输出语言**：所有字符串值必须使用 {{ target_language }}；JSON 键名 / 枚举值保持英文。

{{ source_content }}
