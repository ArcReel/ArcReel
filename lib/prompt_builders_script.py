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
_SCENE_WRITING_GUIDE = """用一段连贯的描述说明当前画面中真实可见的元素：角色姿态、面部可观察的状态、环境细节、可见的氛围信号（光线、雾、雨等）。聚焦"此刻这一帧"，不要混入过去/未来事件、抽象情绪词或镜头之外的元素。
   好例：「林清坐在窗边木桌前，左手撑着下巴，目光落在桌上一封拆开的信纸上。窗外细雨打在木格窗棂，半边脸笼在蓝灰色的阴影里。」
   反例：「林清陷入了多年前那个绝望的雨夜，画面基调：忧郁。光影设定：冷调。」"""

# video_prompt.action 写作指导：动态优先 + 正反例。
_ACTION_WRITING_GUIDE = """用一段描述说明该时长内主体的连贯动作（肢体动作、手势、表情过渡），可包含必要的环境互动（衣摆、尘埃、推门带起的气流等）。让画面"活"起来，但不要堆叠不可能在单镜头内完成的动作或蒙太奇切换。
   好例：「林清缓缓抬起头，手指无意识地摩挲信纸边缘，眼角微微收紧；窗外雨势渐大，桌面投下的雨痕影子在缓慢移动。」
   反例：「林清像蝴蝶般飞舞，思绪在过去与现在之间快速切换。」"""

_LIGHTING_WRITING_GUIDE = "描述具体的光源、方向、色温（如「左侧窗户透入的暖黄色晨光」「头顶单点冷白色的吊灯」），避免「光影神秘」「氛围唯美」这类抽象词。"
_AMBIANCE_WRITING_GUIDE = "描述可观察的环境效果（如「薄雾弥漫」「尘埃在光柱里翻飞」），避免抽象情绪词。"
_AMBIANCE_AUDIO_WRITING_GUIDE = (
    "只描写画内音（diegetic sound）：环境声、脚步、物体声响。不要写 BGM、配乐、画外音、旁白。"
)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    segments_md: str,
    supported_durations: list[int],
    default_duration: int | None = None,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """构建说书模式的剧本生成 prompt。"""
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())
    pacing_block = (render_pacing_section("narration") + "\n\n") if is_v2_enabled() else ""

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

segments 表每行是一个待生成的片段，包含：片段 ID（E{{集}}S{{序号}}）、小说原文、{_format_duration_constraint(supported_durations, default_duration)}、是否含对话、是否为 segment_break。

# 字段写作指引

对每个片段，按下列要求填写字段：

a. **novel_text**：原样复制小说原文，不修改、不删改标点。

b. **characters_in_segment** / **scenes** / **props**：仅列出此片段画面或对话中实际出现的资产。
   - 候选 characters：[{", ".join(character_names) or "（无）"}]
   - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
   - 候选 props：[{", ".join(prop_names) or "（无）"}]
   - 不要发明候选之外的名称。

c. **image_prompt.scene**：{_SCENE_WRITING_GUIDE}

d. **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
   **lighting**：{_LIGHTING_WRITING_GUIDE}
   **ambiance**：{_AMBIANCE_WRITING_GUIDE}

e. **video_prompt.action**：{_ACTION_WRITING_GUIDE}

f. **video_prompt.camera_motion**：每个片段只选一种，按画面内容自行选择。

g. **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}

h. **video_prompt.dialogue**：仅当小说原文带引号对话时填写；speaker 必须出现在 characters_in_segment。

i. **segment_break** / **duration_seconds**：与 segments 表保持一致。

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
    default_duration: int | None = None,
    aspect_ratio: str = "16:9",
    target_language: str = "中文",
) -> str:
    """构建剧集动画模式的剧本生成 prompt。"""
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())
    pacing_block = (render_pacing_section("drama") + "\n\n") if is_v2_enabled() else ""

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

shots 表每行是一个分镜，包含：分镜 ID（E{{集}}S{{序号}}）、分镜描述、{_format_duration_constraint(supported_durations, default_duration)}、场景类型、是否为 segment_break。

# 字段写作指引

对每个分镜，按下列要求填写字段：

a. **characters_in_scene** / **scenes** / **props**：仅列出此分镜画面或对话中实际出现的资产。
   - 候选 characters：[{", ".join(character_names) or "（无）"}]
   - 候选 scenes：[{", ".join(scene_names) or "（无）"}]
   - 候选 props：[{", ".join(prop_names) or "（无）"}]
   - 不要发明候选之外的名称。

b. **image_prompt.scene**：{_SCENE_WRITING_GUIDE}

c. **image_prompt.composition.shot_type**：从枚举中按画面内容选择，不强加倾向。
   **lighting**：{_LIGHTING_WRITING_GUIDE}
   **ambiance**：{_AMBIANCE_WRITING_GUIDE}

d. **video_prompt.action**：{_ACTION_WRITING_GUIDE}

e. **video_prompt.camera_motion**：每个分镜只选一种，按画面内容自行选择。

f. **video_prompt.ambiance_audio**：{_AMBIANCE_AUDIO_WRITING_GUIDE}

g. **video_prompt.dialogue**：包含分镜中角色对话；speaker 必须出现在 characters_in_scene。

h. **segment_break** / **duration_seconds** / **scene_type**：与 shots 表保持一致；scene_type 缺省 "剧情"。

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
) -> str:
    """Step-1 normalization prompt: novel text → markdown scene table.

    Consumed by ``normalize_drama_script`` MCP tool. Sibling of
    ``build_drama_prompt`` (step 2 of the drama pipeline).
    """
    char_list = _format_names(characters)
    scene_list = _format_names(scenes)
    prop_list = _format_names(props)

    durations_str = ", ".join(str(d) for d in supported_durations) if supported_durations else "—"
    max_dur = max(supported_durations) if supported_durations else None

    if default_duration is not None and max_dur is not None:
        duration_rules = (
            f"- 时长：只能取 {durations_str} 中的值（该视频模型支持的秒数集合）\n"
            f"- 每场景默认 {default_duration} 秒；打斗、大场面、情绪铺陈等画面可取更长值至上限 {max_dur} 秒，"
            "不要默认挑最短值"
        )
    elif max_dur is not None:
        duration_rules = (
            f"- 时长：只能取 {durations_str} 中的值（该视频模型支持的秒数集合）\n"
            f"- 按画面内容复杂度匹配合适时长（最长 {max_dur} 秒），不强制默认值"
        )
    else:
        duration_rules = f"- 时长：只能取 {durations_str} 中的值"

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
| E{{N}}S01 | 详细的场景描述... | <duration> | 剧情 | 是 |
| E{{N}}S02 | 详细的场景描述... | <duration> | 对话 | 否 |

规则：
- 场景 ID 格式：E{{集数}}S{{两位序号}}（如 E1S01, E1S02）
- 场景描述：改编后的剧本化描述，包含角色动作、对话、环境，适合视觉化呈现
{duration_rules}
- 场景类型：剧情、动作、对话、过渡、空镜
- segment_break：场景切换点标记"是"，同一连续场景标"否"
- 每个场景应为一个独立的视觉画面，可以在指定时长内完成
- 避免一个场景包含多个不同的动作或画面切换

仅输出 Markdown 表格，不要包含其他解释文字。
"""
