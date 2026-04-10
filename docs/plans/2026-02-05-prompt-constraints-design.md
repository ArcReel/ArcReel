# Prompt Constraint Rules Design

## Background

When reviewing `image_prompt` and `video_prompt` in `episode_1.json`, the generated prompts were found to contain large amounts of abstract, subjective, and metaphorical descriptions, causing poor AI image/video generation results.

### Problem Examples

| Segment | Problem Description | Problem Type |
|---------|--------------------| -------------|
| E1S06 | "The frame quickly flashes a rustic book cover" | Abstract concept ("transmigration") |
| E1S13 | "Like an immovable mountain of terror" | Metaphor/simile |
| E1S21 | "Multi-scene quick cuts" | Multi-scene switching (technically infeasible) |
| E1S23 | "A conversation between a modern soul and an ancient body" | Abstract psychological activity |
| E1S25 | "A splash of crimson dye" as metaphor for punishment | Metaphorical expression |
| E1S34 | "Cognitive dissonance" | Abstract emotional term |

### Reference Approach

Draws on StoryCraft's prompt constraint style:

```
"Scene": "Describe the specific scene being depicted - what is happening in this moment, 
the action or situation being shown, and how it fits into the overall narrative flow. 

Focus on the immediate action and situation. 
Describe the scene : characters (short description only) and objects positions, actions, and interactions. 

Ensure the depiction avoids showing elements beyond this specific moment. 
Exclude any details that suggest a broader story or character arcs. 
The scene should be self-contained, not implying past events or future developments."
```

**Core Principles**:
1. Focus on the immediate action (Focus on the immediate action)
2. Concrete visible elements (characters and objects positions, actions, and interactions)
3. Exclude abstract extensions (Exclude any details that suggest a broader story)
4. Self-contained (self-contained, not implying past events or future developments)

---

## Design

### Files to Modify

| File | Changes |
|------|---------|
| `lib/prompt_builders_script.py` | Update image_prompt and video_prompt constraints in `build_narration_prompt()` and `build_drama_prompt()` |

### 1. image_prompt Constraint Optimization

**Current Version**:
```python
d. **image_prompt**: Generate an object with these fields:
   - scene: Describe the specific scene in Chinese — character positions, expressions, actions, environmental details. Be concrete and visual. One paragraph.
   - composition:
     - shot_type: Shot type (Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view)
     - lighting: Describe light source, direction, and mood in Chinese
     - ambiance: Describe overall atmosphere in Chinese, matching the emotional tone
```

**Optimized Version**:
```python
d. **image_prompt**: Generate an object with these fields:
   - scene: Describe the specific scene visible at this moment — character positions, postures, expressions, clothing details, and visible environmental elements and objects.
     Focus on the visually present elements at this instant. Only describe concrete visual elements that a camera can capture.
     Ensure the description avoids elements beyond this moment. Exclude metaphors, similes, abstract emotional terms, subjective evaluations, multi-scene cuts, and other non-renderable descriptions.
     The image should be self-contained, not implying past events or future developments.
   - composition:
     - shot_type: Shot type (Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view)
     - lighting: Describe the specific light source type, direction, and color temperature (e.g., "warm yellow morning light entering from the left window")
     - ambiance: Describe visible environmental effects (e.g., "thin mist", "dust floating in the air"), avoid abstract emotional terms
```

### 2. video_prompt Constraint Optimization

**Current Version**:
```python
e. **video_prompt**: Generate an object with these fields:
   - action: Precisely describe the action happening during this duration in Chinese. Be specific about movement details.
   - camera_motion: Camera motion (Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot)
   - ambiance_audio: Describe sounds within the scene in Chinese. No music or BGM.
   - dialogue: {speaker, line} array. Only populate when the source text has quoted dialogue.
```

**Optimized Version**:
```python
e. **video_prompt**: Generate an object with these fields:
   - action: Precisely describe the subject's specific actions during this duration — body movement, gesture changes, expression transitions.
     Focus on a single coherent action that can be completed within the specified duration (4/6/8 seconds).
     Exclude multi-scene cuts, montage, rapid editing, and other effects that cannot be achieved in a single generation.
     Exclude metaphorical action descriptions (e.g., "dancing like a butterfly").
   - camera_motion: Camera motion (Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot)
     Choose only one camera motion per segment.
   - ambiance_audio: Describe diegetic sound — ambient sounds, footsteps, object sounds.
     Only describe sounds that actually exist within the scene. Exclude music, BGM, narration, voice-over.
   - dialogue: {speaker, line} array. Only populate when the source text has quoted dialogue. speaker must come from characters_in_segment.
```

### 3. Synchronize drama Mode

The `build_drama_prompt()` function must apply the same constraint rules:

**image_prompt** (same as narration mode, retaining 16:9 landscape note):
```python
c. **image_prompt**: Generate an object with these fields:
   - scene: Describe the specific scene visible at this moment — character positions, postures, expressions, clothing details, and visible environmental elements and objects. 16:9 landscape composition.
     Focus on the visually present elements at this instant. Only describe concrete visual elements that a camera can capture.
     Ensure the description avoids elements beyond this moment. Exclude metaphors, similes, abstract emotional terms, subjective evaluations, multi-scene cuts, and other non-renderable descriptions.
     The image should be self-contained, not implying past events or future developments.
   - composition:
     - shot_type: Shot type (Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view)
     - lighting: Describe the specific light source type, direction, and color temperature (e.g., "warm yellow morning light entering from the left window")
     - ambiance: Describe visible environmental effects (e.g., "thin mist", "dust floating in the air"), avoid abstract emotional terms
```

**video_prompt** (same as narration mode, dialogue field description slightly different):
```python
d. **video_prompt**: Generate an object with these fields:
   - action: Precisely describe the subject's specific actions during this duration — body movement, gesture changes, expression transitions.
     Focus on a single coherent action that can be completed within the specified duration (4/6/8 seconds).
     Exclude multi-scene cuts, montage, rapid editing, and other effects that cannot be achieved in a single generation.
     Exclude metaphorical action descriptions (e.g., "dancing like a butterfly").
   - camera_motion: Camera motion (Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot)
     Choose only one camera motion per segment.
   - ambiance_audio: Describe diegetic sound — ambient sounds, footsteps, object sounds.
     Only describe sounds that actually exist within the scene. Exclude music, BGM, narration, voice-over.
   - dialogue: {speaker, line} array. Include character dialogue. speaker must come from characters_in_scene.
```

---

## Expected Results

### Before (Problem Examples)

```json
{
  "image_prompt": {
    "scene": "Warm perspective: Xie Yuan's silhouette fills the great hall, like an immovable mountain of terror.",
    "composition": {
      "ambiance": "Oppressive and despairing"
    }
  },
  "video_prompt": {
    "action": "The camera quickly cuts between several scenes: a memorial tablet being pushed aside, swaying branches on the garden rockery, disheveled bedding in the side hall."
  }
}
```

### After (Expected Results)

```json
{
  "image_prompt": {
    "scene": "Low-angle warm perspective. Xie Yuan, dressed in a black dragon robe, stands before a golden throne with hands clasped behind his back, gazing down. Red columns line the great hall, human silhouettes reflected on the floor.",
    "composition": {
      "ambiance": "Candlelight flickers inside the hall, light and shadow interplay"
    }
  },
  "video_prompt": {
    "action": "Xie Yuan slowly raises his right hand, sleeve swaying gently with the motion, fingertip pointing toward the foreground."
  }
}
```

---

## Implementation Steps

1. Modify the `build_narration_prompt()` function in `lib/prompt_builders_script.py`
2. Modify the `build_drama_prompt()` function in `lib/prompt_builders_script.py`
3. Re-generate the script for an existing test project to verify the results

---

## Verification

1. Use `/generate-script` to re-generate the test project's script
2. Check whether the generated `image_prompt` and `video_prompt` comply with the constraints
3. Sample image/video generation tests to evaluate results
