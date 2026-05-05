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


def _format_asset_names(assets: dict) -> str:
    """格式化场景或道具列表"""
    lines = []
    for name in assets.keys():
        lines.append(f"- {name}")
    return "\n".join(lines)


def _format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
    """根据参数生成时长约束描述。

    长度 ≥5 且为连续整数集时，输出 "min 到 max 秒间整数任选"；否则按列表逐个列出。
    """
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
        return f"时长：{body}，默认使用 {default_duration} 秒"
    return f"时长：{body}，根据内容节奏自行决定"


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
    scenes: dict,
    props: dict,
    segments_md: str,
    supported_durations: list[int],
    default_duration: int | None = None,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """
    构建说书模式的 Prompt

    Args:
        project_overview: 项目概述（synopsis, genre, theme, world_setting）
        style: 视觉风格标签
        style_description: 风格描述
        characters: 角色字典（仅用于提取名称列表）
        scenes: 场景字典（仅用于提取名称列表）
        props: 道具字典（仅用于提取名称列表）
        segments_md: Step 1 的 Markdown 内容
        target_language: 输出的目标语言

    Returns:
        构建好的 Prompt 字符串
    """
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    prompt = f"""你的任务是为短视频生成分镜剧本。请仔细遵循以下指示：

**重要：所有输出内容必须使用{target_language}。仅 JSON 键名和枚举值使用英文。**

1. 你将获得故事概述、视觉风格、角色列表、场景列表、道具列表，以及已拆分的小说片段。

2. 为每个片段生成：
   - image_prompt：第一帧的图像生成提示词（{target_language}描述）
   - video_prompt：动作和音效的视频生成提示词（{target_language}描述）

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

<scenes>
{_format_asset_names(scenes)}
</scenes>

<props>
{_format_asset_names(props)}
</props>

<segments>
{segments_md}
</segments>

segments 为片段拆分表，每行是一个片段，包含：
- 片段 ID：格式为 E{{集数}}S{{序号}}
- 小说原文：必须原样保留到 novel_text 字段
- {_format_duration_constraint(supported_durations, default_duration)}
- 是否有对话：用于判断是否需要填写 video_prompt.dialogue
- 是否为 segment_break：场景切换点，需设置 segment_break 为 true

3. 为每个片段生成时，遵循以下规则：

a. **novel_text**：原样复制小说原文，不做任何修改。

b. **characters_in_segment**：列出本片段中出场的角色名称。
   - 可选值：[{", ".join(character_names)}]
   - 仅包含明确提及或明显暗示的角色

c. **scenes**：列出本片段中涉及的场景名称（空间位置）。
   - 可选值：[{", ".join(scene_names)}]
   - 仅包含明确提及或明显暗示的场景

d. **props**：列出本片段中涉及的道具名称（物品）。
   - 可选值：[{", ".join(prop_names)}]
   - 仅包含明确提及或明显暗示的道具

e. **image_prompt**：生成包含以下字段的对象：
   - scene：用中文描述此刻画面中的具体场景——角色位置、姿态、表情、服装细节，以及可见的环境元素和物品。
     聚焦当下瞬间的可见画面。仅描述摄像机能够捕捉到的具体视觉元素。
     确保描述避免超出此刻画面的元素。排除比喻、隐喻、抽象情绪词、主观评价、多场景切换等无法直接渲染的描述。
     画面应自包含，不暗示过去事件或未来发展。
   - composition：
     - shot_type：镜头类型（Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view）
     - lighting：用中文描述具体的光源类型、方向和色温（如"左侧窗户透入的暖黄色晨光"）
     - ambiance：用中文描述可见的环境效果（如"薄雾弥漫"、"尘埃飞扬"），避免抽象情绪词

f. **video_prompt**：生成包含以下字段的对象：
   - action：用中文精确描述该时长内主体的具体动作——身体移动、手势变化、表情转换。
     聚焦单一连贯动作，确保在指定时长内可完成。
     排除多场景切换、蒙太奇、快速剪辑等单次生成无法实现的效果。
     排除比喻性动作描述（如"像蝴蝶般飞舞"）。
   - camera_motion：镜头运动（Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot）
     每个片段仅选择一种镜头运动。
   - ambiance_audio：用中文描述画内音（diegetic sound）——环境声、脚步声、物体声音。
     仅描述场景内真实存在的声音。排除音乐、BGM、旁白、画外音。
   - dialogue：{{speaker, line}} 数组。仅当原文有引号对话时填写。speaker 必须来自 characters_in_segment。

g. **segment_break**：如果在片段表中标记为"是"，则设为 true。

h. **duration_seconds**：使用片段表中的时长。

i. **transition_to_next**：默认为 "cut"。

目标：创建生动、视觉一致的分镜提示词，用于指导 AI 图像和视频生成。保持创意、具体，并忠于原文。
"""
    return prompt


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
    """
    构建剧集动画模式的 Prompt

    Args:
        project_overview: 项目概述
        style: 视觉风格标签
        style_description: 风格描述
        characters: 角色字典
        scenes: 场景字典（project 级场景资源列表）
        props: 道具字典
        scenes_md: Step 1 的 Markdown 分镜拆分内容
        target_language: 输出的目标语言

    Returns:
        构建好的 Prompt 字符串
    """
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    prompt = f"""你的任务是为剧集动画生成分镜剧本。请仔细遵循以下指示：

**重要：所有输出内容必须使用{target_language}。仅 JSON 键名和枚举值使用英文。**

1. 你将获得故事概述、视觉风格、角色列表、场景列表、道具列表，以及已拆分的分镜列表。

2. 为每个分镜生成：
   - image_prompt：第一帧的图像生成提示词（{target_language}描述）
   - video_prompt：动作和音效的视频生成提示词（{target_language}描述）

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

<project_scenes>
{_format_asset_names(scenes)}
</project_scenes>

<props>
{_format_asset_names(props)}
</props>

<shots>
{scenes_md}
</shots>

shots 为分镜拆分表，每行是一个分镜，包含：
- 分镜 ID：格式为 E{{集数}}S{{序号}}
- 分镜描述：剧本改编后的分镜内容
- {_format_duration_constraint(supported_durations, default_duration)}
- 场景类型：剧情、动作、对话等
- 是否为 segment_break：场景切换点，需设置 segment_break 为 true

3. 为每个分镜生成时，遵循以下规则：

a. **characters_in_scene**：列出本分镜中出场的角色名称。
   - 可选值：[{", ".join(character_names)}]
   - 仅包含明确提及或明显暗示的角色

b. **scenes**：列出本分镜所处的场景名称（空间位置）。
   - 可选值：[{", ".join(scene_names)}]
   - 仅包含明确提及或明显暗示的场景

c. **props**：列出本分镜中涉及的道具名称（物品）。
   - 可选值：[{", ".join(prop_names)}]
   - 仅包含明确提及或明显暗示的道具

d. **image_prompt**：生成包含以下字段的对象：
   - scene：用中文描述此刻画面中的具体场景——角色位置、姿态、表情、服装细节，以及可见的环境元素和物品。{_format_aspect_ratio_desc(aspect_ratio)}。
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
   - dialogue：{{speaker, line}} 数组。包含角色对话。speaker 必须来自 characters_in_scene。

f. **segment_break**：如果在分镜表中标记为"是"，则设为 true。

g. **duration_seconds**：使用分镜表中的时长。

h. **scene_type**：使用分镜表中的场景类型，默认为"剧情"。

i. **transition_to_next**：默认为 "cut"。

目标：创建生动、视觉一致的分镜提示词，用于指导 AI 图像和视频生成。保持创意、具体，适合{_format_aspect_ratio_desc(aspect_ratio)}动画呈现。
"""
    return prompt
