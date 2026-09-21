---
id: text/source_overview
category: text
title: 源文总览
description: >-
  从项目源文提炼项目概述（故事梗概、题材、主题、世界观与源语言），结果写入 project.json 的 overview，
  并注入之后所有生成环节的提示词。小说从正文归纳；成品剧本常附作者写下的创作方案，
  形态不固定、没有统一标记，所以只描述它可能的样子，识别到就优先照用作者的设定，缺失才从正文归纳。
  输出语言与其余文本环节同口径，由项目源语言决定。
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
