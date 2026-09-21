---
id: text/drama_script_plan
category: text
title: 剧情演绎脚本规划
description: >-
  把本集源文拆为结构化分镜内容：分镜边界、出场资产、视觉改编描述、逐字口播 utterances 与原文锚 source_text，
  定稿后由提示词编写只补视觉层。小说是改编：画外音按语境判断产出，场景切换由模型判断；
  成品剧本是提取：台词与画外音逐字照搬，沿用作者的场次，不做再创作。
  字段、枚举与必填项由 response_schema 强制，正文只讲怎么写好每个字段的内容。
  输出语言主句两种源文共用，只把例外从句按源文类型分开：资产引用与说话人须逐字等于登记名，
  翻译会与已登记资产失配；剧本额外逐字保留台词正文。
  时长拆成三条：档位按画面选、口播下界防止选到念不完台词的短档、单集目标决定拆多少个分镜；
  口播下界是单向的，只防过短，不因台词删减内容。群演不进出场角色只在候选清单处讲一次。
applies_to:
  content_mode:
  - drama
  generation_mode:
  - storyboard
  source_kind:
  - novel
  - screenplay
output_schema: lib.script.script_models:DramaNormalizedScript
slots:
  source_kind: 源文类型
  target_language: 输出语言，取项目源语言
  project_overview: 项目概述，键齐全的对象 {synopsis, genre, theme, world_setting}；缺值传 null
  style: 项目画风
  character_names: 可引用的角色名，衍生写作「本体/衍生」
  scene_names: 可引用的场景名
  prop_names: 可引用的道具名
  novel_text: 本集源文
  episode: 集号
  durations: 视频模型支持的秒数档位
  max_duration: 最长档位秒数
  default_duration: 单个分镜的默认秒数；未设置为空
  speech_rate: 口播语速（每秒阅读单位数）
  speech_unit: 阅读单位量词（字或词）
  episode_target_duration: 单集目标时长秒数；未设置为空
  episode_outline: 本集大纲（title、story_beats、hook、next_episode_teaser）；未规划为空
  next_episode_outline: 下集大纲，形状同本集大纲；未规划为空
  instructions: 附加指令；未提供为空
protected: false
---
{{ variant("text/drama_script_plan/task", source_kind) }}

**输出语言**：自然语言字符串值必须使用 {{ target_language }}；JSON 键名 / 枚举值保持英文。{{ variant("text/drama_script_plan/language_rule", source_kind) }}
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

{{ partial("shared/pacing/drama") }}

## 项目信息

<overview>
{{ project_overview.synopsis or "" }}

题材类型：{{ project_overview.genre or "" }}
核心主题：{{ project_overview.theme or "" }}
世界观设定：{{ project_overview.world_setting or "" }}
</overview>

<style>
{{ style }}
</style>

{{ partial("shared/asset_name_blocks") }}

## 源文

<{{ source_kind }}>
{{ novel_text }}
</{{ source_kind }}>

{% if episode_outline %}
<episode_outline>
本集大纲（分集规划设计，剧本内容应覆盖全部故事节点）：
{% if episode_outline.title %}
本集标题：{{ episode_outline.title }}
{% endif %}
{{ partial("shared/lists/episode_outline_lines", outline=episode_outline) }}
</episode_outline>

{% if episode_outline.hook or episode_outline.next_episode_teaser %}
末场（最后一个或几个分镜）的画面与对白须实际呈现集尾钩子的戏剧内容，让悬念定格在画面上；有下集预告语时，用结尾画面或对白自然引出，不要生硬插入「下集预告」字样的旁白。

{% endif %}
{% endif %}
{% if next_episode_outline %}
<next_episode_outline>
下集大纲（仅用于设计本集结尾的衔接，不要把下集情节提前写进本集）：
{% if next_episode_outline.title %}
下集标题：{{ next_episode_outline.title }}
{% endif %}
{{ partial("shared/lists/episode_outline_lines", outline=next_episode_outline) }}
</next_episode_outline>

{% endif %}
# 字段写作指引

把源文拆为有序分镜，逐条产出结构化分镜内容。当前正在生成第 {{ episode }} 集。

## 基础字段

- **scene_id**：`E{{ episode }}S{两位序号}` 格式（如 E{{ episode }}S01），按分镜顺序递增，不得用其他集号前缀。
- **duration_seconds**：
  - 档位：{% if default_duration %}从支持的秒数档位（{{ durations }}）中按画面内容选择：默认 {{ default_duration }} 秒，打斗 / 大场面 / 情绪铺陈等画面可取更长档至 {{ max_duration }} 秒，不要默认选最短档{% else %}从支持的秒数档位（{{ durations }}）中按画面内容复杂度匹配合适时长（最长 {{ max_duration }} 秒），不强制默认值{% endif %}。
  - 口播下界：先估算该场 utterances（台词 + 画外音）念完约需的秒数（口播语速约 {{ speech_rate }} {{ speech_unit }}/秒），在上述档位里取**不低于**这个秒数的最接近档位。这是单向下界，画面 / 情绪留白可在此之上取更长档位；utterances 为空的分镜没有此下界；若口播估算已超过最长 {{ max_duration }} 秒，取最长档即可，不删减台词。
{% if episode_target_duration %}
  - 单集目标：{{ partial("shared/episode_target_duration_rule") }}。
{% endif %}
- **segment_break**：{{ variant("text/drama_script_plan/break_rule", source_kind) }}
- **characters_in_scene** / **scenes** / **props**：从下列候选中列出此分镜实际出现的资产。
  - 候选 characters：[{{ character_names | join(", ") or "（暂无）" }}]
  - 候选 scenes：[{{ scene_names | join(", ") or "（暂无）" }}]
  - 候选 props：[{{ prop_names | join(", ") or "（暂无）" }}]
  - 不要发明候选之外的名称；泛指群演（如「老人甲」「村民若干」）不进 characters_in_scene。
- **scene_description**：{{ variant("text/drama_script_plan/scene_rule", source_kind) }}

## 逐字内容（内容真相源，定稿后原样保留、不再改写）

- **source_text**：逐字摘录本分镜对应的原文片段，尽量与原文一致、宁缺毋造（无把握可留空）。
- **utterances**：{{ variant("text/drama_script_plan/utterances_rule", source_kind) }}

每个分镜应为一个独立的视觉画面、可在指定时长内完成；避免在一个分镜内安排多个动作或画面切换。
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
