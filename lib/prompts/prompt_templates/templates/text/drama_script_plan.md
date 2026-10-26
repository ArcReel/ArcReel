---
id: text/drama_script_plan
category: text
title: 剧情演绎脚本规划
description: 把本集源文拆为剧情演绎的结构化分镜内容：分镜边界、出场资产、画面描述、逐字口播与原文锚。
stage: script_plan
invoked_by:
  kind: agent_tool
  name: generate_script_plan
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
  assets: 已登记资产，对象 {characters, scenes, props}，每项是 [{name, aliases, appearance}] 列表（尖括号已中和；衍生写作「本体/衍生」）
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

{{ partial("shared/lists/asset_registry_blocks") }}

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

## Direct the sequence before dividing it into shots

Write `sequence_direction` first. This is the shared filmmaking plan for the episode, not a list of unrelated pictures:

- `dramatic_intent`: what the audience should understand or feel by the end, preserving the source story.
- `action_arc`: the causal progression from setup through complication to consequence and reaction. A cut must advance this progression, not repeat a pose or action.
- `geography`: the few spatial relationships and movement directions needed to understand the scene. Keep pedestrians on the sidewalk and vehicles in their lane. Avoid invented landmarks, exact step counts, pixel placement, and incidental left/right accessory rules.
- `identity_anchors`: reference the registered characters, wardrobe, proportions and relative creature scale. Do not redesign them or repeat the entire reference sheet.
- `audio_continuity`: identify the continuous narration/dialogue performance, the scene's musical development and sound bridges across cuts. Individual clips must not restart the score or invent a new voice. Describe the intended mix; do not claim audio controls the renderer does not have.
- `allowed_adaptations`: filming choices that may change to avoid generation failures while retaining the story's cause and effect, dialogue and canon. Prefer a simpler motivated angle or separate readable action to overloaded choreography; do not silently remove the payoff.

Give every scene a `continuity` object: its narrative `purpose`, observable `entry_state`, observable `exit_state`, and `cut_motivation` (why the audience should look at the next shot; for the last shot, how the ending resolves). Carry ongoing action and prop ownership across a cut. Use normal movement speed and enough time for speech; never plan slow-motion filler to meet the episode duration. One clear primary action per render does not mean one static pose per shot. These are editable directing decisions, not immutable canon or rigid exact-pose quality gates.

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
  - 候选里没有的资产写原文称呼，并列进 `new_assets`（见下文）；泛指群演（如「老人甲」「村民若干」）不进 characters_in_scene。
- **scene_description**：{{ variant("text/drama_script_plan/scene_rule", source_kind) }}

## 逐字内容（内容真相源，定稿后原样保留、不再改写）

- **source_text**：逐字摘录本分镜对应的原文片段，尽量与原文一致、宁缺毋造（无把握可留空）。
- **utterances**：{{ variant("text/drama_script_plan/utterances_rule", source_kind) }}

每个分镜应为一个独立的视觉画面、可在指定时长内完成；避免在一个分镜内安排多个动作或画面切换。

{{ partial("shared/new_assets_rule") }}
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
