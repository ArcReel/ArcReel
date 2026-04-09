# Structured Prompt Design

**Date**: 2026-01-30
**Status**: Pending confirmation

---

## Overview

Based on the prompt engineering practices from the StoryCraft project, this document restructures the `image_prompt` and `video_prompt` fields in our project and introduces a fixed style option system.

### Improvement Goals

1. **Structured prompt template** — convert free text to structured fields
2. **YAML format** — convert to YAML format before passing to the Gemini/Veo API
3. **Fixed style options** — replace free-form style input with preset options
4. **Unified negative_prompt** — standardize the elements to exclude from generation

---

## 1. Structured Prompt Template

### 1.1 imagePrompt Structure

```json
{
  "image_prompt": {
    "scene": "A dimly lit underground laboratory with flickering monitors and scattered blueprints",
    "composition": {
      "shot_type": "Medium Shot",
      "lighting": "cold blue light from monitors, harsh shadows",
      "ambiance": "tense, mysterious atmosphere with steam rising from equipment"
    }
  }
}
```

#### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `scene` | string | Scene description: environment, objects, atmosphere |
| `composition.shot_type` | enum | Shot type, chosen from preset options |
| `composition.lighting` | string | Lighting description (light source, color temperature, shadows) |
| `composition.ambiance` | string | Atmosphere description (color tone, mood, environmental effects) |

> **Notes:**
> - **Style** is determined at the project level by the `style` field in `project.json` and is not repeated in each segment
> - **Characters and clues** are referenced via the existing `characters_in_segment` / `clues_in_segment` fields and are not repeated in `imagePrompt`

### 1.2 videoPrompt Structure

```json
{
  "video_prompt": {
    "action": "The scientist slowly turns around, eyes widening as alarms begin to flash",
    "camera_motion": "Dolly In",
    "ambiance_audio": "electrical humming, distant alarm beeping, footsteps on metal floor",
    "dialogue": [
      {
        "speaker": "Dr. Chen",
        "line": "It's happening... exactly as I predicted."
      }
    ]
  }
}
```

#### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `action` | string | Action description: explicitly states what the subject is doing |
| `camera_motion` | enum | Camera movement, chosen from preset options |
| `ambiance_audio` | string | Ambient sound description (diegetic sound only; no music) |
| `dialogue` | array | List of dialogue entries, each with `speaker` and `line` |

---

## 2. Preset Option Definitions

### 2.1 Style (Visual Style)

| Option | Description |
|--------|-------------|
| `Photographic` | Realistic photography style |
| `Anime` | Japanese anime style |
| `3D Animation` | 3D animation style |

### 2.2 shot_type (Shot Types)

| Option | Description |
|--------|-------------|
| `Extreme Close-up` | Partial face or object detail |
| `Close-up` | Face or important object |
| `Medium Close-up` | Head to chest |
| `Medium Shot` | Head to waist |
| `Medium Long Shot` | Head to knees |
| `Long Shot` | Full body visible |
| `Extreme Long Shot` | Character is small in the environment |
| `Over-the-shoulder` | Looking at one character from behind another's shoulder |
| `Point-of-view` | From the character's perspective |

### 2.3 camera_motion (Camera Movements)

| Option | Description |
|--------|-------------|
| `Static` | Camera fixed in place |
| `Pan Left` | Camera rotates horizontally to the left |
| `Pan Right` | Camera rotates horizontally to the right |
| `Tilt Up` | Camera rotates vertically upward |
| `Tilt Down` | Camera rotates vertically downward |
| `Zoom In` | Lens zooms in |
| `Zoom Out` | Lens zooms out |
| `Tracking Shot` | Camera follows the subject |

---

## 3. YAML Format Conversion

### 3.1 Conversion Utility Functions

When calling the Gemini/Veo API, convert the structured prompt to a YAML-format string.

#### imagePrompt Conversion Example

**Input JSON:**
```json
{
  "scene": "A dimly lit underground laboratory with flickering monitors",
  "composition": {
    "shot_type": "Medium Shot",
    "lighting": "cold blue light from monitors, harsh shadows",
    "ambiance": "tense, mysterious atmosphere"
  }
}
```

**Output YAML** (Style injected from project config):
```yaml
Style: Anime
Scene: A dimly lit underground laboratory with flickering monitors
Composition:
  shot_type: Medium Shot
  lighting: cold blue light from monitors, harsh shadows
  ambiance: tense, mysterious atmosphere
```

#### videoPrompt Conversion Example

**Input JSON:**
```json
{
  "action": "The scientist slowly turns around, eyes widening",
  "camera_motion": "Dolly In",
  "ambiance_audio": "electrical humming, distant alarm beeping",
  "dialogue": [
    {
      "speaker": "Dr. Chen",
      "line": "It's happening..."
    }
  ]
}
```

**Output YAML:**
```yaml
Action: The scientist slowly turns around, eyes widening
Camera_Motion: Dolly In
Ambiance_Audio: electrical humming, distant alarm beeping
Dialogue:
  - Speaker: Dr. Chen
    Line: It's happening...
```

### 3.2 Python Implementation

```python
import yaml

def image_prompt_to_yaml(image_prompt: dict, project_style: str) -> str:
    """
    Convert an imagePrompt structure to a YAML-format string.

    Args:
        image_prompt: the image_prompt object from a segment
        project_style: project-level style setting (read from project.json)
    """
    ordered = {
        "Style": project_style,
        "Scene": image_prompt["scene"],
        "Composition": {
            "shot_type": image_prompt["composition"]["shot_type"],
            "lighting": image_prompt["composition"]["lighting"],
            "ambiance": image_prompt["composition"]["ambiance"],
        },
    }
    return yaml.dump(ordered, allow_unicode=True, default_flow_style=False)


def video_prompt_to_yaml(video_prompt: dict) -> str:
    """Convert a videoPrompt structure to a YAML-format string."""
    dialogue = [
        {"Speaker": d["speaker"], "Line": d["line"]}
        for d in video_prompt.get("dialogue", [])
    ]

    ordered = {
        "Action": video_prompt["action"],
        "Camera_Motion": video_prompt["camera_motion"],
        "Ambiance_Audio": video_prompt["ambiance_audio"],
        "Dialogue": dialogue,
    }
    return yaml.dump(ordered, allow_unicode=True, default_flow_style=False)
```

---

## 4. Standardized negative_prompt

When calling the Veo API, use the following `negative_prompt` consistently:

```python
negative_prompt = "music, BGM, background music, subtitles, low quality"
```

### Update Location

The `generate_video()` method in `lib/gemini_client.py`:

```python
def generate_video(
    self,
    prompt: str,
    # ... other parameters
    negative_prompt: str = "music, BGM, background music, subtitles, low quality",
    # ...
) -> tuple:
```

---

## 5. Data Structure Changes

### 5.1 Script JSON Structure (Narration Mode)

**Before:**
```json
{
  "segment_id": "E1S01",
  "novel_text": "Original novel text...",
  "image_prompt": "Medium shot, inside the laboratory...",
  "video_prompt": "Camera slowly pushes in...",
  "characters_in_segment": ["Dr. Chen"],
  "clues_in_segment": ["Blueprint"],
  "duration_seconds": 4
}
```

**After:**
```json
{
  "segment_id": "E1S01",
  "novel_text": "Original novel text...",
  "image_prompt": {
    "scene": "A high-tech laboratory with holographic displays and scattered research papers",
    "composition": {
      "shot_type": "Medium Shot",
      "lighting": "cool fluorescent lights with blue accent from holograms",
      "ambiance": "clinical, futuristic atmosphere with soft mechanical hum"
    }
  },
  "video_prompt": {
    "action": "Dr. Chen examines a holographic display, then turns sharply as an alert flashes",
    "camera_motion": "Static",
    "ambiance_audio": "soft mechanical whirring, sudden alert beep, fabric rustling",
    "dialogue": []
  },
  "characters_in_segment": ["Dr. Chen"],
  "clues_in_segment": ["Blueprint"],
  "duration_seconds": 4
}
```

### 5.2 project.json Style Field

**Before:**
```json
{
  "style": "Ancient imperial court style, delicate and beautiful visuals"
}
```

**After:**
```json
{
  "style": "Anime"
}
```

Only preset options are allowed: `Photographic` | `Anime` | `3D Animation`

---

## 6. Agent Generation Instruction Updates

The System Prompts of the `novel-to-narration-script` and `novel-to-storyboard-script` agents must be updated to guide them to output the structured format.

### 6.1 imagePrompt Generation Instruction

```
For each segment, generate an image_prompt object with the following structure:

{
  "scene": "[Describe the environment, objects, and atmosphere in 1-2 sentences]",
  "composition": {
    "shot_type": "[Choose from: Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view]",
    "lighting": "[Describe light sources, color temperature, and shadow characteristics]",
    "ambiance": "[Describe color tones, mood, and environmental effects like fog, dust, etc.]"
  }
}

Note:
- Style is defined at project level (project.json), not per segment
- Characters and clues are referenced via characters_in_segment and clues_in_segment fields
```

### 6.2 videoPrompt Generation Instruction

```
For each segment, generate a video_prompt object with the following structure:

{
  "action": "[Describe what the subject(s) are doing within the clip duration. Be specific about movements, gestures, and expressions]",
  "camera_motion": "[Choose from: Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot]",
  "ambiance_audio": "[Describe diegetic sounds only - environmental sounds, footsteps, object sounds. Do NOT mention music or narration]",
  "dialogue": [
    {
      "speaker": "[Character name from characters_in_segment]",
      "line": "[The spoken dialogue]"
    }
  ]
}
```

---

## 7. Implementation Plan

| Phase | Content | Files Involved |
|-------|---------|----------------|
| Phase 1 | Add YAML conversion utility functions | `lib/prompt_utils.py` (new) |
| Phase 2 | Update negative_prompt default | `lib/gemini_client.py` |
| Phase 3 | Update Agent System Prompts | `.claude/commands/novel-to-narration-script.md`, `.claude/commands/novel-to-storyboard-script.md` |
| Phase 4 | Update storyboard generation scripts to use YAML | `generate_storyboard.py`, `generate_video.py` |
| Phase 5 | Update WebUI style selector | relevant `webui/` files |

---

## 8. Migration Strategy

**Direct refactor**: all projects will use the new structured format; backward compatibility with the old format will not be maintained.

### Migration Steps

1. Update Agent System Prompts to output structured format
2. Update storyboard/video generation scripts to parse the structured prompt and convert to YAML
3. Existing project scripts must be regenerated or manually migrated

---

## 9. References

- [StoryCraft investigation report](/docs/storycraft-investigation.md)
- [Veo Prompt Guide](/docs/google-genai-docs/veo.md)
- [StoryCraft prompt-utils.ts](/docs/storycraft/lib/utils/prompt-utils.ts)
