"""
prompt_builders_script.py - Script generation prompt builder

1. XML tags to separate context sections
2. Explicit field descriptions and constraints
3. Allowable-value lists to constrain output
"""


def _format_character_names(characters: dict) -> str:
    """Format the character name list."""
    lines = []
    for name in characters.keys():
        lines.append(f"- {name}")
    return "\n".join(lines)


def _format_clue_names(clues: dict) -> str:
    """Format the clue name list."""
    lines = []
    for name in clues.keys():
        lines.append(f"- {name}")
    return "\n".join(lines)


def _format_duration_constraint(supported_durations: list[int], default_duration: int | None) -> str:
    """Generate a duration constraint description from the given parameters."""
    durations_str = ", ".join(str(d) for d in supported_durations)
    if default_duration is not None:
        return f"Duration: choose from [{durations_str}] seconds; default is {default_duration} seconds"
    return f"Duration: choose from [{durations_str}] seconds; decide based on content pacing"


def _format_aspect_ratio_desc(aspect_ratio: str) -> str:
    """Return a composition description string for the given aspect ratio."""
    if aspect_ratio == "9:16":
        return "portrait composition"
    elif aspect_ratio == "16:9":
        return "landscape composition"
    return f"{aspect_ratio} composition"


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
    Build the narration-mode prompt.

    Args:
        project_overview: Project overview (synopsis, genre, theme, world_setting)
        style: Visual style tag
        style_description: Style description
        characters: Character dictionary (used only to extract name lists)
        clues: Clue dictionary (used only to extract name lists)
        segments_md: Step 1 Markdown content

    Returns:
        Constructed prompt string
    """
    character_names = list(characters.keys())
    clue_names = list(clues.keys())

    prompt = f"""Your task is to generate a storyboard script for a short video. Follow the instructions below carefully.

**Important: All descriptive content in image_prompt and video_prompt fields must be written in Chinese. Only JSON key names and enum values use English.**

1. You will be given the story overview, visual style, character list, clue list, and pre-split novel segments.

2. For each segment generate:
   - image_prompt: image generation prompt for the first frame (Chinese description)
   - video_prompt: video generation prompt for actions and audio (Chinese description)

<overview>
{project_overview.get("synopsis", "")}

Genre: {project_overview.get("genre", "")}
Core theme: {project_overview.get("theme", "")}
World setting: {project_overview.get("world_setting", "")}
</overview>

<style>
Style: {style}
Description: {style_description}
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

The segments table lists each segment per row, containing:
- Segment ID: format E{{episode}}S{{number}}
- Original novel text: must be copied verbatim into the novel_text field
- {_format_duration_constraint(supported_durations or [4, 6, 8], default_duration)}
- Has dialogue: used to decide whether to fill in video_prompt.dialogue
- Is segment_break: scene transition point — set segment_break to true

3. When generating for each segment, follow these rules:

a. **novel_text**: Copy the original novel text verbatim without any modification.

b. **characters_in_segment**: List the names of characters appearing in this segment.
   - Allowed values: [{", ".join(character_names)}]
   - Include only characters explicitly mentioned or clearly implied

c. **clues_in_segment**: List the names of clues relevant to this segment.
   - Allowed values: [{", ".join(clue_names)}]
   - Include only clues explicitly mentioned or clearly implied

d. **image_prompt**: Generate an object with the following fields:
   - scene: describe in Chinese the specific scene visible at this moment — character positions, postures, expressions, costume details, and visible environmental elements and objects.
     Focus on the visible instant. Describe only specific visual elements the camera can capture.
     Ensure the description avoids elements beyond the current frame. Exclude metaphors, similes, abstract emotional words, subjective evaluations, and multi-scene cuts that cannot be rendered directly.
     The frame should be self-contained, implying no past events or future developments.
   - composition:
     - shot_type: shot type (Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view)
     - lighting: describe in Chinese the specific light source type, direction, and colour temperature (e.g. "warm golden morning light streaming in from the left window")
     - ambiance: describe in Chinese visible environmental effects (e.g. "thin mist", "dust in the air"); avoid abstract emotional words

e. **video_prompt**: Generate an object with the following fields:
   - action: describe in Chinese the precise actions of the subject within this duration — body movement, gesture changes, expression transitions.
     Focus on a single continuous action completable within the specified duration.
     Exclude multi-scene cuts, montage, rapid editing, and other effects unachievable in a single generation.
     Exclude metaphorical action descriptions (e.g. "dancing like a butterfly").
   - camera_motion: camera motion (Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot)
     Choose only one camera motion per segment.
   - ambiance_audio: describe in Chinese diegetic sounds — ambient noise, footsteps, object sounds.
     Describe only sounds that actually exist within the scene. Exclude music, BGM, narration, and off-screen audio.
   - dialogue: array of {{speaker, line}}. Fill in only when the source text contains quoted dialogue. speaker must come from characters_in_segment.

f. **segment_break**: Set to true if marked as a break point in the segment table.

g. **duration_seconds**: Use the duration from the segment table.

h. **transition_to_next**: Default to "cut".

Goal: Create vivid, visually consistent storyboard prompts to guide AI image and video generation. Be creative, specific, and faithful to the source text.
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
    Build the drama animation mode prompt.

    Args:
        project_overview: Project overview
        style: Visual style tag
        style_description: Style description
        characters: Character dictionary
        clues: Clue dictionary
        scenes_md: Step 1 Markdown content

    Returns:
        Constructed prompt string
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
