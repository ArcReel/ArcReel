---
id: text/episode_plan
category: text
title: 分集规划
description: 把一个窗口的源文切分为若干集，每集给出标题、集尾钩子与逐字摘抄的结尾锚点，剧情演绎另给故事节点与下集预告语（输出结构为剧情演绎版本，旁白 / 解说不含这两个字段）。成品剧本要求照用作者自带的分集，没有分集线索时才按剧情弧切，绝不按字数硬凑。附加指令的遵循强度由指令正文自己表达，这里的分节标题保持中性、不加强度限定词；全局进度只随附加指令出现，供模型把总集数、按章对齐这类全局约束换算到本批，无附加指令时提示词保持可复现。
applies_to:
  content_mode: [drama, narration]
  source_kind: [novel, screenplay]
output_schema: lib.episode_planner:DramaPlanDraft
slots:
  content_mode: 创作类型轴值（drama / narration），用于解析命名变体片段
  source_kind: 源文类型轴值（novel / screenplay），用于解析命名变体片段
  synopsis: 故事概述；无时传 null
  genre: 题材；无时传 null
  unit_noun: 阅读单位量词（字 / 词），随源文语言
  target_volume: 每集目标体量，键齐全的对象 {units, seconds, units_per_second}；seconds 与 units_per_second 仅按单集目标时长折算时有值，语速已格式化为文本；未设置时传 null
  max_episodes: 本批最多规划的集数；无上限时传 null
  context_entries: 已规划末尾若干集，键齐全的对象列表 {episode, title, hook}，标题缺失时 title 为 null；无时传空列表
  instructions: 附加指令正文；无时传 null
  progress: 全局进度，键齐全的对象 {planned_count, remaining_units, window_units}；仅在有附加指令时传入，否则传 null
  window_is_final: 本窗口是否已含全文结尾
  failure: 上一轮输出未通过校验的原因列表；首轮传 null
  window: 本批源文窗口正文
protected: false
---
{{ variant("text/episode_plan/intro", source_kind) }}

# 项目信息
- 创作类型：{{ variant("text/episode_plan/content_mode_label", content_mode) }}
{% if synopsis %}
- 故事概述：{{ synopsis }}
{% endif %}
{% if genre %}
- 题材：{{ genre }}
{% endif %}
{% if target_volume %}
- 每集目标体量：约 {{ target_volume.units }} {{ unit_noun }}（{% if target_volume.seconds %}按单集目标时长 {{ target_volume.seconds }} 秒、口播语速约 {{ target_volume.units_per_second }} {{ unit_noun }}/秒粗略折算，{% endif %}允许为剧情完整性上下浮动）
{% else %}
- 每集目标体量：未设置，请按短视频节奏自行把握（以剧情弧完整优先）
{% endif %}
{% if max_episodes %}
- 本批最多规划 {{ max_episodes }} 集
{% endif %}
{% if context_entries %}

# 已规划的前情（已定上下文，不可改动，续着它往下规划）
{{ partial("text/episode_plan/lists/context_entries") }}
{% endif %}
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
{% if progress %}

# 全局进度
- 已规划 {{ progress.planned_count }} 集
- 未规划余量约 {{ progress.remaining_units }} {{ unit_noun }}（含本窗口，含后续源文件）
- 本窗口为其中前 {{ progress.window_units }} {{ unit_noun }}
- 若附加指令含总集数、按章节对齐等全局约束，请结合以上进度与余量换算本批的切分节奏与集数。
{% endif %}

# 切分规则
- 每一集给出 title（吸引人的短标题）、hook（集尾钩子说明：这一刀为什么切在这、给观众留了什么悬念）、
  end_anchor（本集结尾处的原文片段，10~30 个字符，必须从下方原文中逐字摘抄、含标点，且在整段原文中唯一出现；
  本集内容 = 上一集结尾之后到该片段末尾为止的全部原文）。
{{ variant("text/episode_plan/author_division_rule", source_kind) }}
{{ variant("text/episode_plan/outline_rule", content_mode) }}
- 各集按顺序排列，end_anchor 位置必须严格递增（范围连续、不重叠、不留空洞）。
{% if window_is_final %}
- 这段原文已包含全文结尾：请规划到结尾，最后一集的 end_anchor 取全文结尾处的片段，不要留尾巴。
{% else %}
- 这段原文只是全文的一个窗口：窗口尾部剧情弧不完整的内容不要硬凑成集，留给下一批规划即可。
{% endif %}
- 只输出符合 schema 的 JSON，不要输出其他内容。
{% if failure %}

# 上一轮输出未通过校验，请针对性修正后重新输出
{{ partial("text/episode_plan/lists/failure") }}
{% endif %}

{{ variant("text/episode_plan/source_heading", source_kind) }}
---
{{ window }}
---
