---
id: reference_video/unit
category: video
title: 参考生视频
description: >-
  参考生视频单元的完整提示词，按供应商三段论排布：参考来源声明、单元正文、风格与负向约束。
  前两段是请求契约，图片与音频编号由代码按实际随请求发出的参考素材生成。
  第三段不写画质、稳定类措辞，质量词对当前模型近于噪声，只会分走正文的注意力。
  两个及以上不同角色同框时才追加分身或双胞胎排除项；同一角色的本体与衍生同现是一个人，
  追加会与第一段的形态声明对立。
  商品高保真声明放在最后并排出优先级，说明 Avoid 行的文字与 Logo 排除项不针对商品参考图自带的标识。
applies_to: {}
slots:
  declarations: 第一段：主体绑定（<X>@图片N）、同一角色的形态声明与声音声明（@音频N）；无参考素材时为空
  body: 第二段：单元正文，mention 已换成主体记号，台词记号已重组，旁白记号已丢弃
  style: 项目画风
  style_description: 项目风格描述
  co_present_characters: 同框的不同角色，本体与衍生合并计一个；不足两个时为空
  products: 随请求发出参考图的商品名，按首现顺序去重；无商品参考图时为空
protected: false
---
{{ declarations }}

{{ body }}

{{ partial("shared/style") }}
{{ partial("reference_video/unit/avoid") }}
{% if products %}

商品高保真还原（最高优先级，优先于前述文字/Logo 禁止项）：画面中的商品{{ partial("reference_video/unit/lists/products") }}必须与商品参考图完全一致——{{ partial("shared/product_fidelity") }}，不得重新设计或美化商品本身；前述文字/Logo 禁止项仅指画面中不得凭空新增文字或 Logo，商品参考图自带的文字与 Logo须原样保留；项目画风只作用于商品以外的画面元素。
{% endif %}
