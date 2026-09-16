---
id: storyboard/grid
category: storyboard
title: 宫格联合图
description: >-
  把一组分镜按首尾帧链画进一张宫格联合图，生成后按画格切回各分镜。
  画格等大、无边框、无间隙、不合并不遗漏已由布局要求正面写明，Avoid 行只追加布局要求没覆盖到的
  反面形态（边框、间隙或留白、合并 / 缺失 / 错位、连续全景），不再重复质量词、拼贴感、
  纯色背景条与画格大小比例。
  末块分镜不足一档时由空占位格补齐，占位格画成纯灰，切格后丢弃。
applies_to: {}
slots:
  reference_images: 参考图类型声明，按随请求发出的参考图序位编号为「图N」；无参考图为空
  rows: 宫格行数
  cols: 宫格列数
  cell_count: 画格总数
  grid_aspect_ratio: 整张联合图的比例
  panel_aspect_ratio: 单个画格的比例，由整图比例与行列数算出
  last_chain_cell: 帧链最后一格的序号
  opening: 格0 的开场分镜（scene_id、description）
  transitions: 过渡格列表（index、row、col、from_id、to_id、action、description）
  placeholders: 空占位格列表（index、row、col）
  style: 项目画风
  style_description: 项目风格描述
protected: false
---
{% if reference_images %}
Reference_Images: {{ reference_images }}

{% endif %}
你是一位专业的分镜画师。请严格按照 {{ rows }}×{{ cols }} 宫格布局生成一张包含恰好 {{ cell_count }} 个等大画格的联合图。

【布局要求】
- 恰好 {{ rows }} 行 {{ cols }} 列，共 {{ cell_count }} 个画格，阅读顺序：从左到右，从上到下
- 整体图片比例：{{ grid_aspect_ratio }}
- 每个画格比例：{{ panel_aspect_ratio }}，所有画格大小完全相同
- 画格之间无边框、无间隙、无留白，紧密排列
- 不得合并画格、不得遗漏画格、不得错位排列
- 所有画格保持一致的角色外观、光线和色彩风格

【帧链节奏】
本宫格采用首尾帧链式结构：
- 格0 是第一个场景的开场画面
- 格1~格{{ last_chain_cell }} 是相邻场景的过渡帧（前一场景的结束 = 后一场景的开始）
- 相邻格之间应体现画面的自然过渡和动作延续

【各格内容】
格0（row1 col1）— {{ opening.scene_id }}开场：
  {{ opening.description }}
{{ partial("storyboard/grid/lists/cells") }}

{{ partial("shared/style") }}

{{ partial("storyboard/grid/avoid") }}
