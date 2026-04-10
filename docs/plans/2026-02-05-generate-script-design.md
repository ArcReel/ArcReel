# Design Document: Generating JSON Scripts with Gemini

## Overview

Creates a script that uses `gemini-3-flash-preview` to generate JSON scripts, replacing the final step (Step 3) of the existing Agent workflow.

### Background

The existing `novel-to-narration-script` and `novel-to-storyboard-script` Agents use a three-step flow:
1. **Step 1**: Split into segments/scenes (outputs `step1_segments.md`)
2. **Step 2**: Character and clue tables (outputs `step2_character_clue_tables.md`)
3. **Step 3**: Generate JSON script ← **this script replaces this step**

### Goals

- Use Gemini-3-Flash-Preview to generate JSON scripts
- Draw on Storycraft's prompt engineering techniques
- Use Pydantic to ensure output format compliance
- Support narration mode and drama mode

---

## Architecture Design

### File Structure

```
lib/
├── script_generator.py      # Core logic: prompt construction + Gemini call + Pydantic models
├── gemini_client.py         # Existing: has generate_text() method
└── ...

.claude/skills/
└── generate-script/
    ├── SKILL.md             # Skill documentation (created with skill-creator)
    └── scripts/
        └── generate_script.py  # CLI entry point
```

### Data Flow

```
step1_segments.md + step2_character_clue_tables.md + project.json
                            ↓
                    ScriptGenerator
                            ↓
                    Build Prompt
                            ↓
              Gemini API (gemini-3-flash-preview)
                            ↓
                    Pydantic validation
                            ↓
                scripts/episode_N.json
```

---

## Pydantic Model Definitions

### Shared Models

```python
from pydantic import BaseModel, Field
from typing import List, Optional, Literal

class Dialogue(BaseModel):
    """Dialogue entry."""
    speaker: str = Field(description="Speaker name")
    line: str = Field(description="Spoken line")

class Composition(BaseModel):
    """Composition information."""
    shot_type: str = Field(description="Shot type, e.g. Medium Shot, Close-up, Long Shot")
    lighting: str = Field(description="Lighting description: light source, direction, and mood")
    ambiance: str = Field(description="Overall atmosphere, matching the emotional tone")

class ImagePrompt(BaseModel):
    """Storyboard image generation prompt."""
    scene: str = Field(description="Scene description: character positions, expressions, actions, and environmental details")
    composition: Composition = Field(description="Composition information")

class VideoPrompt(BaseModel):
    """Video generation prompt."""
    action: str = Field(description="Action description: specific actions of the subject within the clip")
    camera_motion: str = Field(description="Camera motion: Static, Pan Left/Right, Zoom In/Out, Tracking Shot, etc.")
    ambiance_audio: str = Field(description="Ambient audio: describe only sounds within the scene; no BGM")
    dialogue: List[Dialogue] = Field(default_factory=list, description="Dialogue list; only populate when the source text has quoted dialogue")

class GeneratedAssets(BaseModel):
    """Generated asset status (initially empty)."""
    storyboard_image: Optional[str] = Field(default=None, description="Storyboard image path")
    video_clip: Optional[str] = Field(default=None, description="Video clip path")
    video_uri: Optional[str] = Field(default=None, description="Video URI")
    status: Literal["pending", "storyboard_ready", "completed"] = Field(default="pending", description="Generation status")
```

### Narration Mode

```python
class NarrationSegment(BaseModel):
    """Segment in narration mode."""
    segment_id: str = Field(description="Segment ID, format E{episode}S{number}")
    episode: int = Field(description="Episode number")
    duration_seconds: Literal[4, 6, 8] = Field(description="Segment duration in seconds")
    segment_break: bool = Field(default=False, description="Whether this is a scene transition point")
    novel_text: str = Field(description="Original novel text (must be preserved exactly; used for post-production voiceover)")
    characters_in_segment: List[str] = Field(description="List of characters appearing in this segment")
    clues_in_segment: List[str] = Field(default_factory=list, description="List of clues appearing in this segment")
    image_prompt: ImagePrompt = Field(description="Storyboard image generation prompt")
    video_prompt: VideoPrompt = Field(description="Video generation prompt")
    transition_to_next: Literal["cut", "fade", "dissolve"] = Field(default="cut", description="Transition type")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="Generated asset status")

class NarrationEpisodeScript(BaseModel):
    """Episode script in narration mode."""
    episode: int = Field(description="Episode number")
    title: str = Field(description="Episode title")
    content_mode: Literal["narration"] = Field(default="narration", description="Content mode")
    duration_seconds: int = Field(default=0, description="Total duration in seconds")
    summary: str = Field(description="Episode summary")
    novel: dict = Field(description="Novel source information")
    characters_in_episode: List[str] = Field(description="List of characters appearing in this episode")
    clues_in_episode: List[str] = Field(description="List of clues appearing in this episode")
    segments: List[NarrationSegment] = Field(description="List of segments")
```

### Drama Mode

```python
class DramaScene(BaseModel):
    """Scene in drama mode."""
    scene_id: str = Field(description="Scene ID, format E{episode}S{number}")
    duration_seconds: Literal[4, 6, 8] = Field(default=8, description="Scene duration in seconds")
    segment_break: bool = Field(default=False, description="Whether this is a scene transition point")
    scene_type: str = Field(default="drama", description="Scene type")
    characters_in_scene: List[str] = Field(description="List of characters appearing in this scene")
    clues_in_scene: List[str] = Field(default_factory=list, description="List of clues appearing in this scene")
    image_prompt: ImagePrompt = Field(description="Storyboard image generation prompt (16:9 landscape)")
    video_prompt: VideoPrompt = Field(description="Video generation prompt")
    transition_to_next: Literal["cut", "fade", "dissolve"] = Field(default="cut", description="Transition type")
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets, description="Generated asset status")

class DramaEpisodeScript(BaseModel):
    """Episode script in drama mode."""
    episode: int = Field(description="Episode number")
    title: str = Field(description="Episode title")
    content_mode: Literal["drama"] = Field(default="drama", description="Content mode")
    summary: str = Field(description="Episode summary")
    novel: dict = Field(description="Novel source information")
    characters_in_episode: List[str] = Field(description="List of characters appearing in this episode")
    clues_in_episode: List[str] = Field(description="List of clues appearing in this episode")
    scenes: List[DramaScene] = Field(description="List of scenes")
```

---

## Prompt Design

Draws on Storycraft's prompt engineering techniques:
1. XML tags to separate context sections
2. Clear field descriptions and constraints
3. Enum value lists to constrain output

### Narration Mode Prompt

```python
def build_narration_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    clues: dict,
    segments_md: str,
) -> str:
    character_names = list(characters.keys())
    clue_names = list(clues.keys())

    prompt = f"""
Your task is to generate a storyboard script for a short video. Follow these instructions carefully:

1. You will receive the story overview, visual style, character list, clue list, and pre-split novel segments.

2. For each segment, generate:
   - image_prompt: an image generation prompt for the first frame
   - video_prompt: a video generation prompt for action and audio effects

<overview>
{project_overview.get('synopsis', '')}

Genre: {project_overview.get('genre', '')}
Core theme: {project_overview.get('theme', '')}
World setting: {project_overview.get('world_setting', '')}
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

The segments table lists each segment with: segment ID (format E{{episode}}S{{number}}), novel text (must be copied exactly into novel_text), duration (4, 6, or 8 seconds), whether dialogue is present, and whether it is a segment_break (scene transition point).

3. When generating each segment, follow these rules:

a. **novel_text**: Copy the original novel text exactly without any modifications.

b. **characters_in_segment**: List the character names appearing in this segment.
   - Allowed values: [{', '.join(character_names)}]
   - Include only characters explicitly mentioned or clearly implied

c. **clues_in_segment**: List the clue names involved in this segment.
   - Allowed values: [{', '.join(clue_names)}]
   - Include only clues explicitly mentioned or clearly implied

d. **image_prompt**: Generate an object with these fields:
   - scene: describe the specific scene — character positions, expressions, actions, environmental details. Be concrete and visual. One paragraph.
   - composition:
     - shot_type: shot type (Close-up, Medium Shot, Medium Long Shot, Long Shot, etc.)
     - lighting: describe light sources, direction, and mood
     - ambiance: overall atmosphere matching the emotional tone

e. **video_prompt**: Generate an object with these fields:
   - action: precisely describe the action that occurs during this duration. Be specific about movement details.
   - camera_motion: Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot
   - ambiance_audio: describe only sounds within the scene. No music or BGM.
   - dialogue: {{speaker, line}} array. Only populate when the source text has quoted dialogue.

f. **segment_break**: Set to true if marked as "yes" in the segments table.

g. **duration_seconds**: Use the duration from the segments table (4, 6, or 8).

h. **transition_to_next**: Default to "cut".

4. Output format is a JSON array containing all segments.

Goal: Create vivid, visually consistent storyboard prompts to guide AI image and video generation. Be creative, specific, and faithful to the source text.
"""
    return prompt
```

### Drama Mode Prompt

```python
def build_drama_prompt(
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    clues: dict,
    scenes_md: str,
) -> str:
    character_names = list(characters.keys())
    clue_names = list(clues.keys())

    prompt = f"""
Your task is to generate a storyboard script for an animated drama. Follow these instructions carefully:

1. You will receive the story overview, visual style, character list, clue list, and pre-split scene list.

2. For each scene, generate:
   - image_prompt: an image generation prompt for the first frame
   - video_prompt: a video generation prompt for action and audio effects

<overview>
{project_overview.get('synopsis', '')}

Genre: {project_overview.get('genre', '')}
Core theme: {project_overview.get('theme', '')}
World setting: {project_overview.get('world_setting', '')}
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

The scenes table lists each scene with: scene ID (format E{{episode}}S{{number}}), scene description (adapted from the novel), duration (4, 6, or 8 seconds, default 8), scene type (drama/action/dialogue/etc.), and whether it is a segment_break.

3. When generating each scene, follow these rules:

a. **characters_in_scene**: List the character names appearing in this scene.
   - Allowed values: [{', '.join(character_names)}]
   - Include only characters explicitly mentioned or clearly implied

b. **clues_in_scene**: List the clue names involved in this scene.
   - Allowed values: [{', '.join(clue_names)}]
   - Include only clues explicitly mentioned or clearly implied

c. **image_prompt**: Generate an object with these fields:
   - scene: describe the specific scene — character positions, expressions, actions, environmental details. Be concrete and visual. 16:9 landscape composition.
   - composition:
     - shot_type: shot type (Close-up, Medium Shot, Medium Long Shot, Long Shot, etc.)
     - lighting: describe light sources, direction, and mood
     - ambiance: overall atmosphere matching the emotional tone

d. **video_prompt**: Generate an object with these fields:
   - action: precisely describe the action that occurs during this duration. Be specific about movement details.
   - camera_motion: Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot
   - ambiance_audio: describe only sounds within the scene. No music or BGM.
   - dialogue: {{speaker, line}} array. Include character dialogue.

e. **segment_break**: Set to true if marked as "yes" in the scenes table.

f. **duration_seconds**: Use the duration from the scenes table (4, 6, or 8); default is 8.

g. **scene_type**: Use the scene type from the scenes table; default is "drama".

h. **transition_to_next**: Default to "cut".

4. Output format is a JSON array containing all scenes.

Goal: Create vivid, visually consistent storyboard prompts to guide AI image and video generation. Be creative, specific, and suited to 16:9 landscape animated presentation.
"""
    return prompt
```

---

## ScriptGenerator Class

```python
class ScriptGenerator:
    """
    Script generator.

    Reads the Step 1/2 Markdown intermediate files and calls Gemini to generate the final JSON script.
    """

    MODEL = "gemini-3-flash-preview"

    def __init__(self, project_path: Union[str, Path]):
        """
        Initialize the generator.

        Args:
            project_path: Project directory path, e.g. projects/test0205
        """
        self.project_path = Path(project_path)
        self.client = GeminiClient()
        self.project_json = self._load_project_json()
        self.content_mode = self.project_json.get('content_mode', 'narration')

    def generate(self, episode: int, output_path: Optional[Path] = None) -> Path:
        """
        Generate an episode script.

        Args:
            episode: Episode number
            output_path: Output path; defaults to scripts/episode_{episode}.json

        Returns:
            Path to the generated JSON file
        """
        # 1. Load intermediate files
        step1_md = self._load_step1(episode)
        step2_md = self._load_step2(episode)

        # 2. Extract characters and clues
        characters = self.project_json.get('characters', {})
        clues = self.project_json.get('clues', {})

        # 3. Build prompt
        if self.content_mode == 'narration':
            prompt = build_narration_prompt(...)
            schema = NarrationEpisodeScript.model_json_schema()
        else:
            prompt = build_drama_prompt(...)
            schema = DramaEpisodeScript.model_json_schema()

        # 4. Call Gemini API
        response_text = self.client.generate_text(
            prompt=prompt,
            model=self.MODEL,
            response_schema=schema,
        )

        # 5. Parse and validate response
        script_data = self._parse_response(response_text, episode)

        # 6. Add metadata
        script_data = self._add_metadata(script_data, episode)

        # 7. Save file
        # ...
```

---

## CLI Entry Point

```bash
# Generate a script for a specified episode
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N>

# Specify output path
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N> --output <path>

# Preview prompt (without calling the API)
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N> --dry-run
```

---

## Comparison of Two Modes

| Dimension | Narration Mode | Drama Mode |
|-----------|---------------|------------|
| Data unit | segment | scene |
| Aspect ratio | 9:16 portrait | 16:9 landscape |
| Default duration | 4 seconds | 8 seconds |
| novel_text | Must preserve original text | No such field |
| dialogue | Only when source has quoted dialogue | Includes adapted dialogue |

---

## Implementation Plan

1. **Create Skill**: Use `skill-creator` to create the `generate-script` skill
2. **Implement core logic**: `lib/script_generator.py`
   - Pydantic model definitions
   - Prompt builder functions
   - ScriptGenerator class
3. **Implement CLI entry**: `.claude/skills/generate-script/scripts/generate_script.py`
4. **Test and verify**: Use existing project `test0205` for testing
