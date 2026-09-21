---
id: text/reference_video_prompt_authoring
category: text
title: 参考生视频 · 提示词编写
description: 把已确认的视频单元正文按同一份引用语法扩写出景别 / 构图 / 运镜与画面细节，对全部创作类型共用一份。只做保结构扩写：单元数与顺序不变、台词逐字不变；时长由单元拆分定稿后机械沿用，不进输出。静态外观由参考图承担，正文只写动作、姿态、互动与环境动态，因此不注入资产外观取材说明。参考图上限点明台词记号的说话人不计入，与机械派生参考图的口径一致。
applies_to:
  content_mode: [drama, narration]
  generation_mode: [reference_video]
output_schema: lib.script.script_models:ReferencePromptAuthoringFlatScript
slots:
  target_language: 输出语言
  project_overview: 项目概述，键齐全的对象 {synopsis, genre, theme, world_setting}；缺值传 null
  style: 项目风格值
  style_description: 项目风格描述
  aspect_ratio: 画面比例（如 9:16）
  assets: 资产外观，对象 {characters, scenes, props}，每项是 [{name, appearance}] 列表（尖括号已中和）
  character_names: 角色候选引用名列表（含 本体/衍生）
  scene_names: 场景候选引用名列表
  prop_names: 道具候选引用名列表
  units_content: 已确认视频单元的渲染文本（由代码投影：序号 + 时长 + 正文）
  episode: 当前集号
  max_refs: 单个视频单元的参考图上限；不限时传 null
  instructions: 附加指令正文；缺值传 null
protected: false
---
# 角色与任务

你是一位资深的短视频分镜编剧，本任务是为采用「参考生视频」的第 {{ episode }} 集做**提示词编写**。
下方 script_plan_units 表给出的是已经用户确认的内容契约；你的任务是逐 unit 把正文扩写出景别 / 构图 / 运镜与画面细节。

**输出语言**：所有字符串值必须使用 {{ target_language }}；JSON 键名保持英文。
**结构约束**：字段 / 必填项由 response_schema 强制；本提示只解释**如何写好正文**。

# 保结构要求（违反即整份产出被拒）

- `units` 数组与 script_plan_units **等长、同序**：不合并、不拆分、不增删 unit。
- 每个 unit 的**台词与画外音逐字保留**：不改词、不增删、不重排、不换说话人。
  台词配不上你想要的画面时，请按台词写画面——**不要**改台词。
- 正文里新出现的 `@[名称]` 必须是候选表中的登记名（script_plan 没引用过的资产也可以引用，但必须已登记）。
{% if max_refs %}
- 单个 unit 的**画面描述里** `@` 引用的资产名（去重后）不超过 {{ max_refs }} 个（模型上限）；台词记号 `@[角色]{台词}` 的说话人不计入——它不生成参考图，只驱动音色声明。超出时把次要角色合并到背景描述，不用 `@` 引用。
{% endif %}

# 上下文

{{ partial("shared/overview_block") }}

<style>
风格：{{ style }}
描述：{{ style_description }}
画面比例：{{ aspect_ratio }}
</style>

{{ partial("shared/asset_appearance_blocks") }}

<script_plan_units>
{{ units_content }}
</script_plan_units>

# 正文书写语法

{{ partial("shared/writing_syntax") }}

# 提示词编写写作指引

正文将直接驱动该 unit 的视频生成，按「景别 → 构图 → 运镜 → 画面内容」四要素依次组织，写足画面信息、宁详勿略：

- 景别：大全景 / 全景 / 中景 / 近景 / 特写，及拍摄角度（俯拍 / 仰拍 / 平视）。
- 构图：主体在画面中的位置、前景与背景的关系（如中心构图、对角线构图、以公路 / 廊柱作引导线）。
- 运镜：机位与镜头运动（固定机位 / 跟随 / 推近 / 拉远 / 摇移），含焦点主体的变更。
- 画面内容：占篇幅大头——unit 时长内发生的全部可见运动：每个出场主体各自的动作链（肢体 / 手势 / 神态过渡）、
  物件互动、背景与环境动态（人群、天气、衣摆、光影移动），可带运动质感（如动态模糊），末尾用一句点明氛围基调。
  动作量与 unit 时长匹配：时长越长，动作段数随之递增。
- 角色 / 场景 / 道具仅用 `@[名称]` 引用，候选：
  - character: {{ partial("shared/asset_name_candidates", names=character_names) }}
  - scene: {{ partial("shared/asset_name_candidates", names=scene_names) }}
  - prop: {{ partial("shared/asset_name_candidates", names=prop_names) }}
  外貌、服装、场景陈设等静态外观由参考图承担，**不要**在文本里描写；动作、姿态、互动与环境动态则写得越具体越好。
  动词应描述物理可观察动作（伸手 / 转身 / 摩挲 / 投向 / 收紧），避免「陷入 / 回忆 / 意识到 / 决定」等内心动词。
- 正例：「景别：中景，轻微仰拍。构图：@[角色A] 居画面中心，@[场景A] 的窗棂与案几为前景。运镜：固定机位，缓慢推近。
  画面内容：@[角色A] 在 @[场景A] 中缓步走向窗前，抬手推开木窗，衣摆随穿堂风轻扬。@[角色A]{这风比去年冷。}
  随后低头凝视手中的 @[道具A]，
  指尖缓缓收紧，呼吸放缓，目光从 @[道具A] 缓慢抬起投向窗外；烛焰随风明灭，光影在面部缓慢移动，渲染压抑而克制的氛围。」
- 反例（过短）：「@[角色A] 站在 @[场景A] 里。」——没有景别 / 构图 / 运镜，也没有动作过程与环境动态，生成的视频会近乎静止。
- 反例（写外貌）：「身穿某色服装的角色A 站在某色场景A 前」——外貌 / 服装 / 颜色应由参考图承担，且未用 `@[名称]` 引用。
- 场景引用逐 unit 保留：script_plan 正文里该 unit 引用的场景资产必须在展开后的正文中照常 `@` 引用，
  不因为上一个 unit 已经引用过同一场景就省略；script_plan 未引用场景而候选表里有匹配该地点的场景时，补上引用。

`title` 给本集拟一个简短标题。请按 script_plan_units 顺序逐 unit 产出。
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
