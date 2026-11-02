---
id: reference_video/unit
category: video
title: 参考生视频
description: 参考生视频单元的完整提示词，按 MiniMax H3 参考生视频（Ref2VA）六段式产出；无参考素材时退为基础三字段。
stage: reference_video
invoked_by:
  kind: generation_task
  name: reference_video
applies_to: {}
slots:
  subject_definitions: 第一段 subject_definitions：逐主体声明（<Subject N>…<Picture N>）、同一角色的形态声明与声音声明（@音频N）；无参考素材时为空
  summary: 第二段 summary：[reference generation] 任务类型 + 参考主体概述；无参考素材时为空
  retention_analysis: 第三段 retention_analysis：逐主体 fully_preserved；无参考素材时为空
  body_label: 正文段字段名：有参考素材为 detailed_description，无参考素材为 integrated_multimodal_description
  body: 正文段：单元正文，mention 已换成主体记号，台词记号已重组，旁白记号已丢弃
  overall_soundscape: 环境 / 物理动作声概述段
  non_diegetic_music: 配乐段，本路径恒为 N/A
  style: 项目画风
  style_description: 项目风格描述
  co_present_characters: 同框的不同角色，本体与衍生合并计一个；不足两个时为空
  products: 随请求发出参考图的商品名，按首现顺序去重；无商品参考图时为空
protected: false
---
{% if subject_definitions %}subject_definitions:
{{ subject_definitions }}

{% endif %}{% if summary %}summary: {{ summary }}

{% endif %}{% if retention_analysis %}retention_analysis:
{{ retention_analysis }}

{% endif %}{{ body_label }}:
{{ body }}

overall_soundscape: {{ overall_soundscape }}

non_diegetic_music: {{ non_diegetic_music }}

{{ partial("shared/media_style") }}
{{ partial("reference_video/unit/avoid") }}
{% if products %}

商品高保真还原（最高优先级，优先于前述文字/Logo 禁止项）：画面中的商品{{ partial("reference_video/unit/lists/products") }}必须与商品参考图完全一致——{{ partial("shared/product_fidelity") }}，不得重新设计或美化商品本身；前述文字/Logo 禁止项仅指画面中不得凭空新增文字或 Logo，商品参考图自带的文字与 Logo须原样保留；项目画风只作用于商品以外的画面元素。
{% endif %}
