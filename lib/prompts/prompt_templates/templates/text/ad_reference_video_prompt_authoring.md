---
id: text/ad_reference_video_prompt_authoring
category: text
title: 广告 / 短片 · 参考生视频单元编写
description: 为已有广告参考生视频脚本里带待编写标记的单元写出引用语法正文，按待编写单元的播放顺序逐条输出。单元时长与顺序已定、不进输出；单元里已有的台词与画外音逐字保留，正文为空时按 brief、商品信息与前后单元补写。其余单元只作前后文。动作描写复用共享写作指导，包含任务类型触发词规避口径。
applies_to:
  content_mode:
  - ad
  generation_mode:
  - reference_video
output_schema: lib.script.script_models:ReferencePromptAuthoringFlatScript
slots:
  target_language: 输出语言
  project_overview: 项目概述，键齐全的对象 {synopsis, genre, theme}；缺值传 null
  style: 项目风格
  style_description: 项目风格描述
  aspect_ratio: 画面比例
  aspect_ratio_label: 比例的文字标签
  brief: 创作 brief；未填写时传 null
  products: 商品信息块（名称 / 品牌 / 描述 / 卖点），由代码按商品数据渲染；无商品时传 null
  product_names: 候选商品名列表
  character_names: 候选角色名列表
  scene_names: 候选场景名列表
  prop_names: 候选道具名列表
  units_content: 整份视频单元的渲染文本（由代码投影：序号 + 待编写标记 + 时长 + 正文）
  pending_count: 待编写单元数
  episode: 集号，广告片恒为单视频
  instructions: 附加指令正文；缺值传 null
protected: false
---
# 角色与任务

你是一位资深短视频编导。下方是一支广告 / 短片已有的参考生视频单元，其中标注「【待编写】」的单元需要你写出正文；其余单元已定稿，只作前后文。

**输出语言**：所有正文使用 {{ target_language }}；JSON 键名保持英文。
**输出形状**：只输出 `{"title": "...", "units": [{"text": "..."}]}`，`units` 恰好 {{ pending_count }} 条，按待编写单元在片中的先后顺序一一对应。
单元时长、unit_id、references、generated_assets、needs_replan 均由系统沿用或派生，不得输出。`title` 不写回脚本，拟一个简短标题即可。

<overview>
{{ project_overview.synopsis or "" }}
题材：{{ project_overview.genre or "" }}
主题：{{ project_overview.theme or "" }}
</overview>

{{ partial("shared/style_block") }}

<brief>
{{ brief or "（未提供，按资产信息与常识自行设计）" }}
</brief>

<products>
{{ products or "（无商品，按通用短片创作）" }}
</products>

资产候选：
- products：[{{ product_names | join(", ") or "（无）" }}]
- characters：[{{ character_names | join(", ") or "（无）" }}]
- scenes：[{{ scene_names | join(", ") or "（无）" }}]
- props：[{{ prop_names | join(", ") or "（无）" }}]

<units>
{{ units_content }}
</units>

# 编写要求

- 待编写单元已有正文时，在它的基础上把画面描述写完整：其中的台词与画外音记号逐字保留，不增删、不改词。
- 待编写单元正文为空时，按 brief、商品信息与前后单元的内容补写，内容量与该单元时长匹配，与前后单元衔接自然。
- 每个单元内只能有一种发声归属：角色台词、无归属画外音或无发声三选一。

# 动作写作指引

{{ partial("shared/action_writing_guide") }}

# 统一引用语法

{{ partial("shared/writing_syntax") }}

商品、角色、场景、道具都使用同一个 `@[名称]` 语法。名称只可逐字取自候选表，不要发明资产。本片恒为第 {{ episode }} 集。
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
