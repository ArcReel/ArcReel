"""剧本生成 Prompt 构建器（drama / narration 两种 content_mode）。

设计原则：
- 不重复 schema 已声明的枚举（shot_type / camera_motion 等）；让 response_schema 直接约束。
- 多选枚举字段不在 prompt 里写"如何选"判据，避免把人的镜头审美灌给 LLM；
  让模型按画面内容自行决定。
- 不写无法被 LLM 自检的字数硬限制（"≤200 字"）；用示例隐性表达节奏。
- 字段说明给 1-2 个正例（必要时配一个反例），不堆"必须 / 禁止"清单。
- 节奏建议由 lib.prompt_rules.episode_pacing 注入，跨 subagent 与 builder 共享。
"""

from lib.prompt_rules import is_v2_enabled
from lib.prompt_rules.episode_pacing import render_pacing_section


def _format_names(items: dict) -> str:
    if not items:
        return "（暂无）"
    return "\n".join(f"- {name}" for name in items.keys())


def _format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
    """生成时长约束描述。连续整数集 ≥5 用区间表达，否则枚举。"""
    if not supported_durations:
        raise ValueError("supported_durations 不能为空：调用方必须提供 model 的合法时长列表")

    sorted_d = sorted(set(supported_durations))
    is_continuous = len(sorted_d) >= 5 and all(sorted_d[i] == sorted_d[i - 1] + 1 for i in range(1, len(sorted_d)))
    if is_continuous:
        body = f"{sorted_d[0]} 到 {sorted_d[-1]} 秒间整数任选"
    else:
        durations_str = ", ".join(str(d) for d in sorted_d)
        body = f"从 [{durations_str}] 秒中选择"

    if default_duration is not None:
        if default_duration not in sorted_d:
            raise ValueError(
                f"default_duration={default_duration} 不在 supported_durations={sorted_d} 内，"
                "调用方必须保证默认值合法（否则 prompt 会自相矛盾）"
            )
        return f"时长：{body}，默认 {default_duration} 秒"
    return f"时长：{body}，按内容节奏自行决定"


def _format_aspect_ratio_desc(aspect_ratio: str) -> str:
    if aspect_ratio == "9:16":
        return "竖屏构图"
    if aspect_ratio == "16:9":
        return "横屏构图"
    return f"{aspect_ratio} 构图"


# ---------------------------------------------------------------------------
# 字段写作指导（drama / narration 共用）
# ---------------------------------------------------------------------------

# image_prompt.scene 写作指导：原则 + 正反例。LLM 对示例的泛化优于对清单的执行。
# 好例用方括号小标注隐性传达"主体 / 环境 / 光线 / 氛围"四层覆盖。
_SCENE_WRITING_GUIDE = """用一段连贯的描述说明当前画面中真实可见的元素：角色姿态、面部可观察的状态、环境细节、可见的氛围信号（光线、雾、雨等）。聚焦"此刻这一帧"，不要混入过去/未来事件、抽象情绪词或镜头之外的元素。画面元素（材质、装束、道具质感、环境年代特征）须贴合上方 `<style>` 块定义的风格基调，避免与风格相冲的元素混入（例如赛博朋克风下不出现榻榻米，国风水墨下不出现霓虹屏）。
   好例：「[主体] 林清坐在窗边木桌前，左手撑着下巴，目光落在桌上一封拆开的信纸上。[环境] 桌面摊着信封与一只褪色的怀表。[光线] 半边脸笼在右侧落地窗逆光的蓝灰色阴影里。[氛围] 雨丝拍在木格窗棂，玻璃凝着细小水珠。」
   反例（跑偏）：「林清陷入了多年前那个绝望的雨夜，画面基调：忧郁。光影设定：冷调。」
   反例（过短）：「林清坐在窗边发呆。」——缺少环境元素、光线方向、氛围细节，至少应覆盖主体 / 环境 / 光线 / 氛围中三层。
   反例里这类词族也要避免：陷入 / 回忆 / 思绪 / 意识到 / 画外音 / BGM / 精致 / 震撼。"""

# video_prompt.action 写作指导：动态优先 + 正反例。
# 好例用方括号小标注隐性传达"主体动作 / 物件互动 / 环境动态"三层。
_ACTION_WRITING_GUIDE = """用一段描述说明该时长内主体的连贯动作（肢体动作、手势、表情过渡），可包含必要的环境互动（衣摆、尘埃、推门带起的气流等）。让画面"活"起来，但不要堆叠不可能在单镜头内完成的动作或蒙太奇切换。动词应描述物理可观察动作（伸手 / 转身 / 摩挲 / 投向 / 收紧），避免内心动词。动作幅度应与该 segment 的 duration 匹配：5 秒级镜头通常完成一个连贯动作 + 一个细节互动；8 秒级可承载一次动作过渡（如「抬头—对视—开口」），不要把三组以上独立动作塞进同一 action。
   好例：「[主体动作] 林清缓缓抬起头，眼角微微收紧。[物件互动] 手指无意识地摩挲信纸边缘。[环境动态] 窗外雨势渐大，桌面投下的雨痕影子在缓慢移动。」
   反例：「林清像蝴蝶般飞舞，思绪在过去与现在之间快速切换。」
   反例里这类词族也要避免：思绪飞舞 / 回忆翻涌 / 突然意识到 / 决心 / 仿佛 / 像蝴蝶般。"""

_LIGHTING_WRITING_GUIDE = (
    "描述具体的光源、方向、色温（如「左侧窗户透入的暖黄色晨光（约 3500K）」「头顶单点冷白色的吊灯」）。"
    "可附加摄影质感术语（如「浅景深」「逆光剪影」「丁达尔光柱」「轮廓光勾边」「35mm 胶片颗粒感」），"
    "让画面具备可观察的镜头语言而非抽象修辞；避免「光影神秘」「氛围唯美」这类抽象词。"
)
_AMBIANCE_WRITING_GUIDE = "描述可观察的环境效果（如「薄雾弥漫」「尘埃在光柱里翻飞」），避免抽象情绪词。"
_AMBIANCE_AUDIO_WRITING_GUIDE = (
    "只描写画内音（diegetic sound）：环境声、脚步、物体声响。不要写 BGM、配乐、画外音、旁白。"
)


# few-shot 范例：让 LLM 看到所有字段同时填好的"形"。
# 角色名 / 场景题材 / 风格刻意与 builder 其他指令文字里的"林清"区分，避免被当成 default style。
_NARRATION_FEW_SHOT = """## 字段填写样式示例

以下示例仅作字段填写样式参考，ID 字段请用 step1 中间文件提供的真实 ID，duration_seconds 取自 step1 表。

```json
{
  "segment_id": "<参考 step1 提供的 segment_id>",
  "duration_seconds": 5,
  "segment_break": false,
  "novel_text": "<step1 中该片段对应的原文>",
  "characters_in_segment": ["苏婉"],
  "scenes": ["老茶馆"],
  "props": ["瓷茶碗"],
  "image_prompt": {
    "scene": "[主体] 苏婉斜坐茶馆角落的方木桌前，右手指尖搭在桌沿，左手停在敞开的瓷茶碗上方。[环境] 桌面摊着一本卷边的账册，灶台旁悬着的铜壶正冒出细白蒸汽。[光线] 头顶单点的暖黄色灯笼把她的右脸照亮，左半边脸落在木柱的阴影里。[氛围] 木板墙纹理浮着淡淡油烟感，窗外飘来零星雪粒。",
    "composition": {
      "shot_type": "Medium Shot",
      "lighting": "头顶单点暖黄色灯笼为主光（约 3000K），右上方斜下打光，左侧因木柱遮挡形成深棕色阴影",
      "ambiance": "茶馆室内烟火气浓厚，铜壶蒸汽与窗外雪粒形成冷暖对照"
    }
  },
  "video_prompt": {
    "action": "[主体动作] 苏婉缓缓抬眸望向门口，指尖微微一颤。[物件互动] 左手轻轻覆上茶碗碗沿，拇指顺着碗口画了半圈。[环境动态] 铜壶冒出的白汽随气流向左侧飘散，门帘被推开时带起一阵纸屑轻飞。",
    "camera_motion": "Static",
    "ambiance_audio": "铜壶低沸声、远处脚步落在木板上、门帘掀开的布料摩擦声",
    "dialogue": []
  },
  "transition_to_next": "cut"
}
```
"""

_DRAMA_FEW_SHOT = """## 字段填写样式示例

以下示例仅作字段填写样式参考，ID 字段请用 step1 中间文件提供的真实 ID，duration_seconds 取自 step1 表。

```json
{
  "scene_id": "<参考 step1 提供的 scene_id>",
  "duration_seconds": 8,
  "segment_break": true,
  "scene_type": "对话",
  "characters_in_scene": ["秦舟", "白幼"],
  "scenes": ["江畔栈桥"],
  "props": ["铜灯笼"],
  "image_prompt": {
    "scene": "[主体] 秦舟立在栈桥右侧，左手提一只铜灯笼，灯笼略低；白幼立于桥中央三步外，双手垂在裙摆侧。[环境] 桥下江面起雾，桥板缝隙泛潮，远处岸上几点橘黄渔灯。[光线] 铜灯笼是唯一明显光源（暖橙色，约 2000K），把两人的脸照亮一半。[氛围] 江面雾气贴着水面缓慢移动，木桩上挂着潮湿苔藓。",
    "composition": {
      "shot_type": "Medium Long Shot",
      "lighting": "唯一光源为前景铜灯笼（暖橙 2000K），仅照亮二人面部右侧；远景渔灯为冷调补光",
      "ambiance": "夜间江面薄雾贴水流动，木栈桥潮湿光泽明显"
    }
  },
  "video_prompt": {
    "action": "[主体动作] 秦舟微微抬起左手，把铜灯笼递近半步。[物件互动] 白幼的右手抬起，迟疑半秒后扣住灯笼边缘。[环境动态] 桥下江雾被两人之间气流轻轻搅动，灯笼内火苗左右晃动。",
    "camera_motion": "Static",
    "ambiance_audio": "江水拍木桩的低沉水声、灯笼内火苗的细微噼啪声、远处舟桨破水声",
    "dialogue": [
      {"speaker": "秦舟", "line": "你跟我走。"},
      {"speaker": "白幼", "line": "我自己走。"}
    ]
  },
  "transition_to_next": "cut"
}
```
"""


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


# 中文 target_language 别名集合：实际调用方主要传 "中文"，i18n 三语适配可能传 zh / zh-CN。
# 严格匹配避免误把 "Chinese (Simplified)" 等未声明字符串当中文。
_CHINESE_TARGET_LANGUAGES = frozenset({"中文", "zh", "zh-CN"})


def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    segments_md: str,
    supported_durations: list[int],
    episode: int,
    default_duration: int | None = None,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """构建说书模式的剧本生成 prompt。"""
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())
    pacing_block = (render_pacing_section("narration") + "\n\n") if is_v2_enabled() else ""
    # few-shot 仅在中文 target_language 下注入：示例值是硬编码中文，
    # 与"所有字符串值必须使用 {target_language}"指令对抗会让 LLM 输出语言漂移。
    # 非中文场景退回到 schema description + 写作指引的纯文字引导。
    few_shot_block = _NARRATION_FEW_SHOT if target_language in _CHINESE_TARGET_LANGUAGES else ""

    return f"""# 角色与任务

你是一位资深的短视频分镜编剧，专精把小说片段改写为可直接驱动 AI 图像 / 视频生成的结构化分镜剧本。
你的任务：基于下方"小说片段拆分表"，逐条产出符合 schema 的 JSON 剧本。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

{pacing_block}# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}（{_format_aspect_ratio_desc(aspect_ratio)}）
</style>

<characters>
{_format_names(characters)}
</characters>

<scenes>
{_format_names(scenes)}
</scenes>

<props>
{_format_names(props)}
</props>

<segments>
{segments_md}
</segments>

segments 表每行是一个待生成的片段，包含：片段 ID（E{episode}S{{序号}}，当前为第 {episode} 集）、小说原文、{_format_duration_constraint(supported_durations, default_duration)}、是否含对话、是否为 segment_break。

<episode_constraints>
当前正在生成第 {episode} 集。本集所有 segment_id 必须严格使用 `E{episode}S{{两位序号}}` 格式（如 E{episode}S01、E{episode}S02），不得使用其他集号前缀。
若 segments 表里出现非 `E{episode}` 前缀（如 E1S..），视为脏数据，请按当前集号 `E{episode}` 重写。
</episode_constraints>

# 字段写作指引

对每个片段，按下列章节填写字段。

## 基础字段

- **novel_text**：原样复制小说原文，不修改、不删改标点。
- **characters_in_segment** / **scenes** / **props**：仅列出此片段画面或对话中实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 不要发明候选之外的名称。
- **segment_break** / **duration_seconds**：与 segments 表保持一致。

## 图片提示词（image_prompt）——切换到「摄影师」视角

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

## 视频提示词（video_prompt）——切换到「动作设计师」视角

- **video_prompt.action**：{_ACTION_WRITING_GUIDE}
- **video_prompt.camera_motion**：每个片段只选一种，按画面内容自行选择。
- **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}
- **video_prompt.dialogue**：仅当小说原文带引号对话时填写；speaker 必须出现在 characters_in_segment。

{few_shot_block}

# 创作目标

输出可直接驱动 AI 生成的、视觉一致、节奏紧凑的分镜剧本。忠于原文叙事、保留情绪张力。
"""


def build_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    scenes_md: str,
    supported_durations: list[int],
    episode: int,
    default_duration: int | None = None,
    aspect_ratio: str = "16:9",
    target_language: str = "中文",
) -> str:
    """构建剧集动画模式的剧本生成 prompt。"""
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())
    pacing_block = (render_pacing_section("drama") + "\n\n") if is_v2_enabled() else ""
    # 见 build_narration_prompt 同名变量说明。
    few_shot_block = _DRAMA_FEW_SHOT if target_language in _CHINESE_TARGET_LANGUAGES else ""

    return f"""# 角色与任务

你是一位资深的短剧分镜编剧，精通把改编后的剧本场景表转写为可直接驱动 AI 图像 / 视频生成的结构化分镜。
你的任务：基于下方"分镜拆分表"，逐条产出符合 schema 的 JSON 剧本。

**输出语言**：所有字符串值必须使用 {target_language}；JSON 键名 / 枚举值保持英文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

{pacing_block}# 上下文

<overview>
{project_overview.get("synopsis", "")}

题材：{project_overview.get("genre", "")}
主题：{project_overview.get("theme", "")}
世界观：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}（{_format_aspect_ratio_desc(aspect_ratio)}）
</style>

<characters>
{_format_names(characters)}
</characters>

<project_scenes>
{_format_names(scenes)}
</project_scenes>

<props>
{_format_names(props)}
</props>

<shots>
{scenes_md}
</shots>

shots 表每行是一个分镜，包含：分镜 ID（E{episode}S{{序号}}，当前为第 {episode} 集）、分镜描述、{_format_duration_constraint(supported_durations, default_duration)}、场景类型、是否为 segment_break。

<episode_constraints>
当前正在生成第 {episode} 集。本集所有 scene_id 必须严格使用 `E{episode}S{{两位序号}}` 格式（如 E{episode}S01、E{episode}S02），不得使用其他集号前缀。
若 shots 表里出现非 `E{episode}` 前缀（如 E1S..），视为脏数据，请按当前集号 `E{episode}` 重写。
</episode_constraints>

# 字段写作指引

对每个分镜，按下列章节填写字段。

## 基础字段

- **characters_in_scene** / **scenes** / **props**：仅列出此分镜画面或对话中实际出现的资产。
  - 候选 characters：[{", ".join(character_names) or "（无）"}]
  - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
  - 候选 props：[{", ".join(prop_names) or "（无）"}]
  - 不要发明候选之外的名称。
- **segment_break** / **duration_seconds** / **scene_type**：与 shots 表保持一致；scene_type 缺省 "剧情"。

## 图片提示词（image_prompt）——切换到「摄影师」视角

- **image_prompt.scene**：{_SCENE_WRITING_GUIDE}
- **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
- **image_prompt.composition.lighting**：{_LIGHTING_WRITING_GUIDE}
- **image_prompt.composition.ambiance**：{_AMBIANCE_WRITING_GUIDE}

## 视频提示词（video_prompt）——切换到「动作设计师」视角

- **video_prompt.action**：{_ACTION_WRITING_GUIDE}
- **video_prompt.camera_motion**：每个分镜只选一种，按画面内容自行选择。
- **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}
- **video_prompt.dialogue**：包含分镜中角色对话；speaker 必须出现在 characters_in_scene。

{few_shot_block}

# 创作目标

输出可直接驱动 AI 生成的、视觉一致、节奏紧凑的分镜剧本。忠于原创设定、保留戏剧张力。
"""


def build_normalize_prompt(
    novel_text: str,
    project_overview: dict,
    style: str,
    characters: dict,
    scenes: dict,
    props: dict,
    default_duration: int | None,
    supported_durations: list[int],
    episode: int,
) -> str:
    """Step-1 normalization prompt: novel text → markdown scene table.

    Consumed by ``normalize_drama_script`` MCP tool. Sibling of
    ``build_drama_prompt`` (step 2 of the drama pipeline).
    """
    char_list = _format_names(characters)
    scene_list = _format_names(scenes)
    prop_list = _format_names(props)

    # 规范化 + 校验：空集合或 default 不在集合内都会产出自相矛盾的提示词，
    # 让生成阶段失败比让 LLM 见到"只能取 — 中的值"更便于诊断（PR #528 review）。
    normalized_durations = sorted({int(d) for d in supported_durations})
    if not normalized_durations:
        raise ValueError("supported_durations 不能为空：必须提供模型支持的秒数集合")
    if default_duration is not None and int(default_duration) not in normalized_durations:
        raise ValueError(f"default_duration={default_duration} 不在 supported_durations={normalized_durations} 内")

    durations_str = ", ".join(str(d) for d in normalized_durations)
    max_dur = normalized_durations[-1]

    if default_duration is not None:
        duration_rules = (
            f"- 时长：只能取 {durations_str} 中的值（该视频模型支持的秒数集合）\n"
            f"- 每场景默认 {default_duration} 秒；打斗、大场面、情绪铺陈等画面可取更长值至上限 {max_dur} 秒，"
            "不要默认挑最短值"
        )
    else:
        duration_rules = (
            f"- 时长：只能取 {durations_str} 中的值（该视频模型支持的秒数集合）\n"
            f"- 按画面内容复杂度匹配合适时长（最长 {max_dur} 秒），不强制默认值"
        )

    return f"""你的任务是将小说原文改编为结构化的分镜场景表（Markdown 格式），用于后续 AI 视频生成。

## 项目信息

<overview>
{project_overview.get("synopsis", "")}

题材类型：{project_overview.get("genre", "")}
核心主题：{project_overview.get("theme", "")}
世界观设定：{project_overview.get("world_setting", "")}
</overview>

<style>
{style}
</style>

<characters>
{char_list}
</characters>

<scenes>
{scene_list}
</scenes>

<props>
{prop_list}
</props>

## 小说原文

<novel>
{novel_text}
</novel>

## 输出要求

将小说改编为场景列表，使用 Markdown 表格格式：

| 场景 ID | 场景描述 | 时长 | 场景类型 | segment_break |
|---------|---------|------|---------|---------------|
| E{episode}S01 | 详细的场景描述... | <duration> | 剧情 | 是 |
| E{episode}S02 | 详细的场景描述... | <duration> | 对话 | 否 |

规则：
- 当前正在生成第 {episode} 集；所有场景 ID 必须使用 `E{episode}S{{两位序号}}` 格式，不得使用其他集号前缀
- 场景描述：改编后的剧本化描述，包含角色动作、对话、环境，适合视觉化呈现
{duration_rules}
- 场景类型：剧情、动作、对话、过渡、空镜
- segment_break：场景切换点标记"是"，同一连续场景标"否"
- 每个场景应为一个独立的视觉画面，可以在指定时长内完成
- 避免一个场景包含多个不同的动作或画面切换

仅输出 Markdown 表格，不要包含其他解释文字。
"""
