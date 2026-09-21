---
id: asset/sheet
category: asset
title: 资产图
description: 角色、角色衍生、场景、道具与商品资产图。
applies_to:
  asset_type:
  - character
  - character_derivative
  - scene
  - prop
  - product
slots:
  asset_type: 资产类型
  name: 资产名称，衍生为空
  description: 外观描述或相对本体的变化
  style: 项目画风
  style_description: 项目风格描述
protected: false
---
{{ variant("asset/sheet/style", asset_type) }}

{{ variant("asset/sheet/title", asset_type) }}

{{ description }}

{{ variant("asset/sheet/layout", asset_type) }}

{{ variant("asset/sheet/guard", asset_type) }}

Avoid: {{ variant("asset/sheet/exclusions", asset_type) }}{{ partial("shared/image_avoid") }}