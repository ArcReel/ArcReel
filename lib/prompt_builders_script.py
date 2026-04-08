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

    prompt = f"""Your task is to generate a storyboard script for an animated drama series. Follow the instructions below carefully.

**Important: All descriptive content in image_prompt and video_prompt fields must be written in Chinese. Only JSON key names and enum values use English.**

1. You will be given the story overview, visual style, character list, clue list, and pre-split scene list.

2. For each scene generate:
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

<scenes>
{scenes_md}
</scenes>

The scenes table lists each scene per row, containing:
- Scene ID: format E{{episode}}S{{number}}
- Scene description: the scene content as adapted from the script
- {_format_duration_constraint(supported_durations or [4, 6, 8], default_duration)}
- Scene type: drama, action, dialogue, etc.
- Is segment_break: scene transition point — set segment_break to true

3. When generating for each scene, follow these rules:

a. **characters_in_scene**: List the names of characters appearing in this scene.
   - Allowed values: [{", ".join(character_names)}]
   - Include only characters explicitly mentioned or clearly implied

b. **clues_in_scene**: List the names of clues relevant to this scene.
   - Allowed values: [{", ".join(clue_names)}]
   - Include only clues explicitly mentioned or clearly implied

c. **image_prompt**: Generate an object with the following fields:
   - scene: describe in Chinese the specific scene visible at this moment — character positions, postures, expressions, costume details, and visible environmental elements and objects. {_format_aspect_ratio_desc(aspect_ratio)}.
     Focus on the visible instant. Describe only specific visual elements the camera can capture.
     Ensure the description avoids elements beyond the current frame. Exclude metaphors, similes, abstract emotional words, subjective evaluations, and multi-scene cuts that cannot be rendered directly.
     The frame should be self-contained, implying no past events or future developments.
   - composition:
     - shot_type: shot type (Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view)
     - lighting: describe in Chinese the specific light source type, direction, and colour temperature (e.g. "warm golden morning light streaming in from the left window")
     - ambiance: describe in Chinese visible environmental effects (e.g. "thin mist", "dust in the air"); avoid abstract emotional words

d. **video_prompt**: Generate an object with the following fields:
   - action: describe in Chinese the precise actions of the subject within this duration — body movement, gesture changes, expression transitions.
     Focus on a single continuous action completable within the specified duration.
     Exclude multi-scene cuts, montage, rapid editing, and other effects unachievable in a single generation.
     Exclude metaphorical action descriptions (e.g. "dancing like a butterfly").
   - camera_motion: camera motion (Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot)
     Choose only one camera motion per segment.
   - ambiance_audio: describe in Chinese diegetic sounds — ambient noise, footsteps, object sounds.
     Describe only sounds that actually exist within the scene. Exclude music, BGM, narration, and off-screen audio.
   - dialogue: {{speaker, line}} array. Include character dialogue. speaker must come from characters_in_scene.

e. **segment_break**: Set to true if marked as a break point in the scene table.

f. **duration_seconds**: Use the duration from the scene table.

g. **scene_type**: Use the scene type from the scene table; default is "drama".

h. **transition_to_next**: Default to "cut".

Goal: Create vivid, visually consistent storyboard prompts to guide AI image and video generation. Be creative, specific, and suitable for {_format_aspect_ratio_desc(aspect_ratio)} animated presentation.
"""
    return prompt
