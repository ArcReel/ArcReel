---
id: storyboard/image
category: storyboard
title: 分镜图
description: 分镜图的完整提示词。风格与资产图共用同一口径，参考图声明紧随风格且位于场景之前，编号与商品保真声明由实际参考图列表派生。风格、参考图与 Avoid 块均独占一行引用，保证预览转为纯文本后不叠加；结构化 YAML 段连续，纯文本正文用空行分隔。
applies_to: {}
slots:
  style: 项目画风
  style_description: 项目风格描述
  reference_images: 已序列化的参考图类型声明，无参考图为空
  structured_body: 已序列化的 Scene 与 Composition，纯文本形态为空
  text_body: 纯文本正文，结构化形态为空
protected: false
idempotent: true
---
{{ partial("shared/style") }}
{% if reference_images %}
{{ partial("storyboard/image/references") }}
{% endif %}
{% if text_body %}

{{ text_body }}

{% else %}
{{ structured_body }}
{% endif %}
{{ partial("storyboard/image/avoid") }}