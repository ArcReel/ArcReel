"""
prompt_builders_script.py - 剧本生成 Prompt 构建器

1. XML 标签分隔上下文
2. 明确的字段描述和约束
3. 可选值列表约束输出
"""


def _format_character_names(characters: dict) -> str:
    """格式化角色列表"""
    lines = []
    for name in characters.keys():
        lines.append(f"- {name}")
    return "\n".join(lines)


def _format_clue_names(clues: dict) -> str:
    """格式化线索列表"""
    lines = []
    for name in clues.keys():
        lines.append(f"- {name}")
    return "\n".join(lines)


def _format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
    """根据参数生成时长约束描述。"""
    durations_str = ", ".join(str(d) for d in supported_durations)
    if default_duration is not None:
        return f"时长：从 [{durations_str}] 秒中选择，默认使用 {default_duration} 秒"
    return f"时长：从 [{durations_str}] 秒中选择，根据内容节奏自行决定"


def _format_aspect_ratio_desc(aspect_ratio: str) -> str:
    """根据宽高比返回构图描述。"""
    if aspect_ratio == "9:16":
        return "竖屏构图"
    elif aspect_ratio == "16:9":
        return "横屏构图"
    return f"{aspect_ratio} 构图"


def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    clues: dict,
    segments_md: str,
    supported_durations: list[int] | None = None,
    default_duration: int | None = None,
    aspect_ratio: str = "9:16",
) -> str:
    """
    构建说书模式的 Prompt

    Args:
        project_overview: 项目概述（synopsis, genre, theme, world_setting）
        style: 视觉风格标签
        style_description: 风格描述
        characters: 角色字典（仅用于提取名称列表）
        clues: 线索字典（仅用于提取名称列表）
        segments_md: Step 1 的 Markdown 内容

    Returns:
        构建好的 Prompt 字符串
    """
    character_names = list(characters.keys())
    clue_names = list(clues.keys())

    prompt = f"""你的任务是为短视频生成分镜剧本。请仔细遵循以下指示：

**重要：所有输出内容必须使用中文。仅 JSON 键名和枚举值使用英文。**

1. 你将获得故事概述、视觉风格、角色列表、线索列表，以及已拆分的小说片段。

2. 为每个片段生成：
   - image_prompt：第一帧的图像生成提示词（中文描述）
   - video_prompt：动作和音效的视频生成提示词（中文描述）

<overview>
{project_overview.get("synopsis", "")}

题材类型：{project_overview.get("genre", "")}
核心主题：{project_overview.get("theme", "")}
世界观设定：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
</style>

<characters>
{_format_character_names(characters)}
</characters>

<clues>
{_format_clue_names(clues)}
</clues>

<segments>
{segments_md}
</segments>

segments 为片段拆分表，每行是一个片段，包含：
- 片段 ID：格式为 E{{集数}}S{{序号}}
- 小说原文：必须原样保留到 novel_text 字段
- {_format_duration_constraint(supported_durations or [4, 6, 8], default_duration)}
- 是否有对话：用于判断是否需要填写 video_prompt.dialogue
- 是否为 segment_break：场景切换点，需设置 segment_break 为 true

3. 为每个片段生成时，遵循以下规则：

a. **novel_text**：原样复制小说原文，不做任何修改。

b. **characters_in_segment**：列出本片段中出场的角色名称。
   - 可选值：[{", ".join(character_names)}]
   - 仅包含明确提及或明显暗示的角色

c. **clues_in_segment**：列出本片段中涉及的线索名称。
   - 可选值：[{", ".join(clue_names)}]
   - 仅包含明确提及或明显暗示的线索

d. **image_prompt**：生成包含以下字段的对象：
   - scene：用中文描述此刻画面中的具体场景——角色位置、姿态、表情、服装细节，以及可见的环境元素和物品。
     聚焦当下瞬间的可见画面。仅描述摄像机能够捕捉到的具体视觉元素。
     确保描述避免超出此刻画面的元素。排除比喻、隐喻、抽象情绪词、主观评价、多场景切换等无法直接渲染的描述。
     画面应自包含，不暗示过去事件或未来发展。
   - composition：
     - shot_type：镜头类型（Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view）
     - lighting：用中文描述具体的光源类型、方向和色温（如"左侧窗户透入的暖黄色晨光"）
     - ambiance：用中文描述可见的环境效果（如"薄雾弥漫"、"尘埃飞扬"），避免抽象情绪词

e. **video_prompt**：生成包含以下字段的对象：
   - action：用中文精确描述该时长内主体的具体动作——身体移动、手势变化、表情转换。
     聚焦单一连贯动作，确保在指定时长内可完成。
     排除多场景切换、蒙太奇、快速剪辑等单次生成无法实现的效果。
     排除比喻性动作描述（如"像蝴蝶般飞舞"）。
   - camera_motion：镜头运动（Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot）
     每个片段仅选择一种镜头运动。
   - ambiance_audio：用中文描述画内音（diegetic sound）——环境声、脚步声、物体声音。
     仅描述场景内真实存在的声音。排除音乐、BGM、旁白、画外音。
   - dialogue：{{speaker, line}} 数组。仅当原文有引号对话时填写。speaker 必须来自 characters_in_segment。

f. **segment_break**：如果在片段表中标记为"是"，则设为 true。

g. **duration_seconds**：使用片段表中的时长。

h. **transition_to_next**：默认为 "cut"。

目标：创建生动、视觉一致的分镜提示词，用于指导 AI 图像和视频生成。保持创意、具体，并忠于原文。
"""
    return prompt


def build_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    clues: dict,
    scenes_md: str,
    supported_durations: list[int] | None = None,
    default_duration: int | None = None,
    aspect_ratio: str = "16:9",
) -> str:
    """
    构建剧集动画模式的 Prompt

    Args:
        project_overview: 项目概述
        style: 视觉风格标签
        style_description: 风格描述
        characters: 角色字典
        clues: 线索字典
        scenes_md: Step 1 的 Markdown 内容

    Returns:
        构建好的 Prompt 字符串
    """
    character_names = list(characters.keys())
    clue_names = list(clues.keys())

    prompt = f"""你的任务是为剧集动画生成分镜剧本。请仔细遵循以下指示：

**重要：所有输出内容必须使用中文。仅 JSON 键名和枚举值使用英文。**

1. 你将获得故事概述、视觉风格、角色列表、线索列表，以及已拆分的场景列表。

2. 为每个场景生成：
   - image_prompt：第一帧的图像生成提示词（中文描述）
   - video_prompt：动作和音效的视频生成提示词（中文描述）

<overview>
{project_overview.get("synopsis", "")}

题材类型：{project_overview.get("genre", "")}
核心主题：{project_overview.get("theme", "")}
世界观设定：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
</style>

<characters>
{_format_character_names(characters)}
</characters>

<clues>
{_format_clue_names(clues)}
</clues>

<scenes>
{scenes_md}
</scenes>

scenes 为场景拆分表，每行是一个场景，包含：
- 场景 ID：格式为 E{{集数}}S{{序号}}
- 场景描述：剧本改编后的场景内容
- {_format_duration_constraint(supported_durations or [4, 6, 8], default_duration)}
- 场景类型：剧情、动作、对话等
- 是否为 segment_break：场景切换点，需设置 segment_break 为 true

3. 为每个场景生成时，遵循以下规则：

a. **characters_in_scene**：列出本场景中出场的角色名称。
   - 可选值：[{", ".join(character_names)}]
   - 仅包含明确提及或明显暗示的角色

b. **clues_in_scene**：列出本场景中涉及的线索名称。
   - 可选值：[{", ".join(clue_names)}]
   - 仅包含明确提及或明显暗示的线索

c. **image_prompt**：生成包含以下字段的对象：
   - scene：用中文描述此刻画面中的具体场景——角色位置、姿态、表情、服装细节，以及可见的环境元素和物品。{_format_aspect_ratio_desc(aspect_ratio)}。
     聚焦当下瞬间的可见画面。仅描述摄像机能够捕捉到的具体视觉元素。
     确保描述避免超出此刻画面的元素。排除比喻、隐喻、抽象情绪词、主观评价、多场景切换等无法直接渲染的描述。
     画面应自包含，不暗示过去事件或未来发展。
   - composition：
     - shot_type：镜头类型（Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view）
     - lighting：用中文描述具体的光源类型、方向和色温（如"左侧窗户透入的暖黄色晨光"）
     - ambiance：用中文描述可见的环境效果（如"薄雾弥漫"、"尘埃飞扬"），避免抽象情绪词

d. **video_prompt**：生成包含以下字段的对象：
   - action：用中文精确描述该时长内主体的具体动作——身体移动、手势变化、表情转换。
     聚焦单一连贯动作，确保在指定时长内可完成。
     排除多场景切换、蒙太奇、快速剪辑等单次生成无法实现的效果。
     排除比喻性动作描述（如"像蝴蝶般飞舞"）。
   - camera_motion：镜头运动（Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot）
     每个片段仅选择一种镜头运动。
   - ambiance_audio：用中文描述画内音（diegetic sound）——环境声、脚步声、物体声音。
     仅描述场景内真实存在的声音。排除音乐、BGM、旁白、画外音。
   - dialogue：{{speaker, line}} 数组。包含角色对话。speaker 必须来自 characters_in_scene。

e. **segment_break**：如果在场景表中标记为"是"，则设为 true。

f. **duration_seconds**：使用场景表中的时长。

g. **scene_type**：使用场景表中的场景类型，默认为"剧情"。

h. **transition_to_next**：默认为 "cut"。

目标：创建生动、视觉一致的分镜提示词，用于指导 AI 图像和视频生成。保持创意、具体，适合{_format_aspect_ratio_desc(aspect_ratio)}动画呈现。
"""
    return prompt
