# Generate Script Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Use Gemini-3-Flash-Preview to generate JSON scripts, replacing Step 3 of the existing Agent workflow.

**Architecture:** Core logic in `lib/script_generator.py`; CLI entry in `.claude/skills/generate-script/scripts/generate_script.py`. Uses Pydantic to define data models and validate output, drawing on Storycraft's prompt engineering techniques.

**Tech Stack:** Python 3.10+, Pydantic, google-genai SDK

**Design Doc:** `docs/plans/2026-02-05-generate-script-design.md`

---

## Task 1: Pydantic Model Definitions

**Files:**
- Create: `lib/script_models.py`

**Step 1: Create the shared model file**

```python
"""
script_models.py - Script data models

Defines the data structures for scripts using Pydantic, used for:
1. Gemini API response_schema (Structured Outputs)
2. Output validation
"""

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


# ============ Narration Mode ============

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


class NovelInfo(BaseModel):
    """Novel source information."""
    title: str = Field(description="Novel title")
    chapter: str = Field(description="Chapter name")
    source_file: str = Field(description="Source file path")


class NarrationEpisodeScript(BaseModel):
    """Episode script in narration mode."""
    episode: int = Field(description="Episode number")
    title: str = Field(description="Episode title")
    content_mode: Literal["narration"] = Field(default="narration", description="Content mode")
    duration_seconds: int = Field(default=0, description="Total duration in seconds")
    summary: str = Field(description="Episode summary")
    novel: NovelInfo = Field(description="Novel source information")
    characters_in_episode: List[str] = Field(description="List of characters appearing in this episode")
    clues_in_episode: List[str] = Field(description="List of clues appearing in this episode")
    segments: List[NarrationSegment] = Field(description="List of segments")


# ============ Drama Mode ============

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
    duration_seconds: int = Field(default=0, description="Total duration in seconds")
    summary: str = Field(description="Episode summary")
    novel: NovelInfo = Field(description="Novel source information")
    characters_in_episode: List[str] = Field(description="List of characters appearing in this episode")
    clues_in_episode: List[str] = Field(description="List of clues appearing in this episode")
    scenes: List[DramaScene] = Field(description="List of scenes")
```

**Step 2: Verify the model can generate a JSON Schema**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python -c "from lib.script_models import NarrationEpisodeScript; print(NarrationEpisodeScript.model_json_schema())"`

Expected: Outputs JSON Schema with no errors

**Step 3: Commit**

```bash
git add lib/script_models.py
git commit -m "feat: add Pydantic models for script generation"
```

---

## Task 2: Prompt Builder Functions

**Files:**
- Create: `lib/prompt_builders_script.py`

**Step 1: Create the prompt builder module**

```python
"""
prompt_builders_script.py - Script generation prompt builder

Draws on Storycraft's prompt engineering techniques:
1. XML tags to separate context sections
2. Clear field descriptions and constraints
3. Enum value lists to constrain output
"""

from typing import Dict, List


def _format_character_names(characters: Dict) -> str:
    """Format character name list."""
    lines = []
    for name in characters.keys():
        lines.append(f"- {name}")
    return "\n".join(lines)


def _format_clue_names(clues: Dict) -> str:
    """Format clue name list."""
    lines = []
    for name in clues.keys():
        lines.append(f"- {name}")
    return "\n".join(lines)


def build_narration_prompt(
    project_overview: Dict,
    style: str,
    style_description: str,
    characters: Dict,
    clues: Dict,
    segments_md: str,
) -> str:
    """
    Build the prompt for narration mode.
    
    Args:
        project_overview: Project overview (synopsis, genre, theme, world_setting)
        style: Visual style tag
        style_description: Style description
        characters: Character dictionary (used only to extract name list)
        clues: Clue dictionary (used only to extract name list)
        segments_md: Markdown content from Step 1
        
    Returns:
        Built prompt string
    """
    character_names = list(characters.keys())
    clue_names = list(clues.keys())
    
    prompt = f"""Your task is to generate a storyboard script for a short video. Follow these instructions carefully:

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

The segments table lists each segment with: segment ID (format E{{episode}}S{{number}}), novel text (must be copied exactly into novel_text), duration (4, 6, or 8 seconds), whether dialogue is present, and whether it is a segment_break.

3. When generating each segment, follow these rules:

a. **novel_text**: Copy the original novel text exactly without modifications.

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
   - action: precisely describe the action during this duration. Be specific about movement details.
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


def build_drama_prompt(
    project_overview: Dict,
    style: str,
    style_description: str,
    characters: Dict,
    clues: Dict,
    scenes_md: str,
) -> str:
    """
    Build the prompt for drama mode.
    
    Args:
        project_overview: Project overview
        style: Visual style tag
        style_description: Style description
        characters: Character dictionary
        clues: Clue dictionary
        scenes_md: Markdown content from Step 1
        
    Returns:
        Built prompt string
    """
    character_names = list(characters.keys())
    clue_names = list(clues.keys())
    
    prompt = f"""Your task is to generate a storyboard script for an animated drama. Follow these instructions carefully:

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

The scenes table lists each scene with: scene ID (format E{{episode}}S{{number}}), scene description (adapted script content), duration (4, 6, or 8 seconds, default 8), scene type (drama/action/dialogue/etc.), and whether it is a segment_break.

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
   - action: precisely describe the action during this duration. Be specific about movement details.
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

**Step 2: Verify the prompt builder functions**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python -c "from lib.prompt_builders_script import build_narration_prompt; print(build_narration_prompt({}, 'test', 'test', {'CharA': {}}, {'ClueA': {}}, 'test')[:200])"`

Expected: Outputs first 200 characters of the prompt with no errors

**Step 3: Commit**

```bash
git add lib/prompt_builders_script.py
git commit -m "feat: add prompt builders for script generation"
```

---

## Task 3: ScriptGenerator Core Class

**Files:**
- Create: `lib/script_generator.py`

**Step 1: Create the ScriptGenerator class**

```python
"""
script_generator.py - Script generator

Reads the Step 1/2 Markdown intermediate files and calls Gemini to generate the final JSON script
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from pydantic import ValidationError

from lib.gemini_client import GeminiClient
from lib.script_models import (
    NarrationEpisodeScript,
    DramaEpisodeScript,
)
from lib.prompt_builders_script import (
    build_narration_prompt,
    build_drama_prompt,
)


class ScriptGenerator:
    """
    Script generator.
    
    Reads the Step 1/2 Markdown intermediate files and calls Gemini to generate the final JSON script.
    """
    
    MODEL = "gemini-2.5-flash-preview-05-20"
    
    def __init__(self, project_path: Union[str, Path]):
        """
        Initialize the generator.
        
        Args:
            project_path: Project directory path, e.g. projects/test0205
        """
        self.project_path = Path(project_path)
        self.client = GeminiClient()
        
        # Load project.json
        self.project_json = self._load_project_json()
        self.content_mode = self.project_json.get('content_mode', 'narration')
    
    def generate(
        self,
        episode: int,
        output_path: Optional[Path] = None,
    ) -> Path:
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
        
        # 2. Extract characters and clues (from project.json)
        characters = self.project_json.get('characters', {})
        clues = self.project_json.get('clues', {})
        
        # 3. Build prompt
        if self.content_mode == 'narration':
            prompt = build_narration_prompt(
                project_overview=self.project_json.get('overview', {}),
                style=self.project_json.get('style', ''),
                style_description=self.project_json.get('style_description', ''),
                characters=characters,
                clues=clues,
                segments_md=step1_md,
            )
            schema = NarrationEpisodeScript.model_json_schema()
        else:
            prompt = build_drama_prompt(
                project_overview=self.project_json.get('overview', {}),
                style=self.project_json.get('style', ''),
                style_description=self.project_json.get('style_description', ''),
                characters=characters,
                clues=clues,
                scenes_md=step1_md,
            )
            schema = DramaEpisodeScript.model_json_schema()
        
        # 4. Call Gemini API
        print(f"Generating episode {episode} script...")
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
        if output_path is None:
            output_path = self.project_path / 'scripts' / f'episode_{episode}.json'
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)
        
        print(f"Script saved to {output_path}")
        return output_path
    
    def build_prompt(self, episode: int) -> str:
        """
        Build the prompt (for dry-run mode).
        
        Args:
            episode: Episode number
            
        Returns:
            Built prompt string
        """
        step1_md = self._load_step1(episode)
        characters = self.project_json.get('characters', {})
        clues = self.project_json.get('clues', {})
        
        if self.content_mode == 'narration':
            return build_narration_prompt(
                project_overview=self.project_json.get('overview', {}),
                style=self.project_json.get('style', ''),
                style_description=self.project_json.get('style_description', ''),
                characters=characters,
                clues=clues,
                segments_md=step1_md,
            )
        else:
            return build_drama_prompt(
                project_overview=self.project_json.get('overview', {}),
                style=self.project_json.get('style', ''),
                style_description=self.project_json.get('style_description', ''),
                characters=characters,
                clues=clues,
                scenes_md=step1_md,
            )
    
    def _load_project_json(self) -> dict:
        """Load project.json."""
        path = self.project_path / 'project.json'
        if not path.exists():
            raise FileNotFoundError(f"project.json not found: {path}")
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _load_step1(self, episode: int) -> str:
        """Load the Step 1 Markdown file."""
        path = self.project_path / 'drafts' / f'episode_{episode}' / 'step1_segments.md'
        if not path.exists():
            raise FileNotFoundError(f"Step 1 file not found: {path}")
        
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    
    def _parse_response(self, response_text: str, episode: int) -> dict:
        """
        Parse and validate the Gemini response.
        
        Args:
            response_text: JSON text returned by the API
            episode: Episode number
            
        Returns:
            Validated script data dictionary
        """
        # Strip possible markdown wrapper
        text = response_text.strip()
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()
        
        # Parse JSON
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON parse failed: {e}")
        
        # Pydantic validation
        try:
            if self.content_mode == 'narration':
                validated = NarrationEpisodeScript.model_validate(data)
            else:
                validated = DramaEpisodeScript.model_validate(data)
            return validated.model_dump()
        except ValidationError as e:
            print(f"Data validation warning: {e}")
            # Return raw data; allow partial schema mismatch
            return data
    
    def _add_metadata(self, script_data: dict, episode: int) -> dict:
        """
        Add metadata to the script.
        
        Args:
            script_data: Script data
            episode: Episode number
            
        Returns:
            Script data with metadata added
        """
        # Ensure basic fields exist
        script_data.setdefault('episode', episode)
        script_data.setdefault('content_mode', self.content_mode)
        
        # Add novel information
        if 'novel' not in script_data:
            script_data['novel'] = {
                'title': self.project_json.get('title', ''),
                'chapter': f'Episode {episode}',
                'source_file': '',
            }
        
        # Add timestamp
        now = datetime.now().isoformat()
        script_data.setdefault('metadata', {})
        script_data['metadata']['created_at'] = now
        script_data['metadata']['updated_at'] = now
        script_data['metadata']['generator'] = self.MODEL
        
        # Compute statistics
        if self.content_mode == 'narration':
            segments = script_data.get('segments', [])
            script_data['metadata']['total_segments'] = len(segments)
            script_data['duration_seconds'] = sum(
                s.get('duration_seconds', 4) for s in segments
            )
        else:
            scenes = script_data.get('scenes', [])
            script_data['metadata']['total_scenes'] = len(scenes)
            script_data['duration_seconds'] = sum(
                s.get('duration_seconds', 8) for s in scenes
            )
        
        return script_data
```

**Step 2: Verify the ScriptGenerator initializes**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python -c "from lib.script_generator import ScriptGenerator; g = ScriptGenerator('projects/test0205'); print(g.content_mode)"`

Expected: outputs `narration`

**Step 3: Commit**

```bash
git add lib/script_generator.py
git commit -m "feat: add ScriptGenerator class"
```

---

## Task 4: CLI Entry Point Script

**Files:**
- Create: `.claude/skills/generate-script/scripts/generate_script.py`

**Step 1: Create directory structure**

Run: `mkdir -p /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script/.claude/skills/generate-script/scripts`

**Step 2: Create the CLI script**

```python
#!/usr/bin/env python3
"""
generate_script.py - Generate JSON scripts using Gemini

Usage:
    python generate_script.py <project_name> --episode <N>
    python generate_script.py <project_name> --episode <N> --output <path>
    python generate_script.py <project_name> --episode <N> --dry-run
    
Example:
    python generate_script.py test0205 --episode 1
    python generate_script.py support-humans --episode 1 --output scripts/ep1.json
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[4]  # .claude/skills/generate-script/scripts -> root
sys.path.insert(0, str(PROJECT_ROOT))

from lib.script_generator import ScriptGenerator


def main():
    parser = argparse.ArgumentParser(
        description='Generate JSON scripts using Gemini',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example:
    %(prog)s test0205 --episode 1
    %(prog)s support-humans --episode 1 --output scripts/ep1.json
    %(prog)s test0205 --episode 1 --dry-run
        """
    )
    
    parser.add_argument(
        'project',
        type=str,
        help='Project name (directory under projects/)'
    )
    
    parser.add_argument(
        '--episode', '-e',
        type=int,
        required=True,
        help='Episode number'
    )
    
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help='Output file path (default: scripts/episode_N.json)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show prompt only; do not actually call the API'
    )
    
    args = parser.parse_args()
    
    # Build project path
    project_path = PROJECT_ROOT / 'projects' / args.project
    
    if not project_path.exists():
        print(f"Project does not exist: {project_path}")
        sys.exit(1)
    
    # Check whether intermediate files exist
    drafts_path = project_path / 'drafts' / f'episode_{args.episode}'
    step1_path = drafts_path / 'step1_segments.md'
    
    if not step1_path.exists():
        print(f"Step 1 file not found: {step1_path}")
        print("   Please complete the segment split (Step 1) first")
        sys.exit(1)
    
    try:
        generator = ScriptGenerator(project_path)
        
        if args.dry_run:
            # Show prompt only
            print("=" * 60)
            print("DRY RUN - Prompt that would be sent to Gemini:")
            print("=" * 60)
            prompt = generator.build_prompt(args.episode)
            print(prompt)
            print("=" * 60)
            return
        
        # Actual generation
        output_path = Path(args.output) if args.output else None
        result_path = generator.generate(
            episode=args.episode,
            output_path=output_path,
        )
        
        print(f"\nScript generation complete: {result_path}")
        
    except FileNotFoundError as e:
        print(f"File error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Generation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
```

**Step 3: Add execute permission**

Run: `chmod +x /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script/.claude/skills/generate-script/scripts/generate_script.py`

**Step 4: Verify CLI help information**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python .claude/skills/generate-script/scripts/generate_script.py --help`

Expected: displays help information

**Step 5: Commit**

```bash
git add .claude/skills/generate-script/scripts/generate_script.py
git commit -m "feat: add CLI entry point for script generation"
```

---

## Task 5: SKILL.md

**Files:**
- Create: `.claude/skills/generate-script/SKILL.md`

**Step 1: Create SKILL.md**

```markdown
---
name: generate-script
description: Generate JSON scripts using the Gemini API. Use when: (1) user runs /generate-script, (2) Step 1/2 is complete and the final script needs to be generated, (3) user wants to use Gemini instead of Claude to generate the script. Reads step1_segments.md and project.json, calls gemini-2.5-flash-preview-05-20 to generate a JSON script conforming to the Pydantic models.
---

# generate-script

Generate JSON scripts using the Gemini API, replacing Step 3 of the existing Agent workflow.

## Prerequisites

1. The project directory contains `project.json` (with style, overview, characters, clues)
2. Step 1 is complete: `drafts/episode_N/step1_segments.md`
3. Step 2 is complete: characters and clues are written to `project.json`

## Usage

```bash
# Generate a script for a specified episode
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N>

# Specify output path
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N> --output <path>

# Preview prompt (without calling the API)
python .claude/skills/generate-script/scripts/generate_script.py <project> --episode <N> --dry-run
```

## Examples

```bash
# Generate episode 1 script for the test0205 project
python .claude/skills/generate-script/scripts/generate_script.py test0205 --episode 1

# Preview the prompt that would be sent to Gemini
python .claude/skills/generate-script/scripts/generate_script.py test0205 --episode 1 --dry-run
```

## Output

The generated JSON file is saved to `projects/<project>/scripts/episode_N.json`

## Supported Modes

- **narration**: 9:16 portrait; original text preserved in novel_text
- **drama**: 16:9 landscape; scene-adapted content
```

**Step 2: Commit**

```bash
git add .claude/skills/generate-script/SKILL.md
git commit -m "feat: add SKILL.md for generate-script"
```

---

## Task 6: Integration Testing

**Files:**
- Test: `projects/test0205`

**Step 1: Run dry-run test**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python .claude/skills/generate-script/scripts/generate_script.py test0205 --episode 1 --dry-run`

Expected: displays the full prompt, including overview, style, characters, clues, segments

**Step 2: Run actual generation test**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python .claude/skills/generate-script/scripts/generate_script.py test0205 --episode 1`

Expected: generates `projects/test0205/scripts/episode_1.json` containing all segments

**Step 3: Verify the generated JSON structure**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/feature-generate-script && python -c "import json; d=json.load(open('projects/test0205/scripts/episode_1.json')); print(f'segments: {len(d.get(\"segments\", []))}'); print(f'mode: {d.get(\"content_mode\")}')"` 

Expected: displays segment count and mode

**Step 4: Final commit**

```bash
git add -A
git commit -m "test: verify script generation with test0205"
```

---

## Completion Checklist

- [ ] Task 1: Pydantic model definitions (`lib/script_models.py`)
- [ ] Task 2: Prompt builder functions (`lib/prompt_builders_script.py`)
- [ ] Task 3: ScriptGenerator core class (`lib/script_generator.py`)
- [ ] Task 4: CLI entry point (`.claude/skills/generate-script/scripts/generate_script.py`)
- [ ] Task 5: SKILL.md (`.claude/skills/generate-script/SKILL.md`)
- [ ] Task 6: Integration testing
