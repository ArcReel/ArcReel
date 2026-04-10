# Prompt Constraint Rules Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add constraint rules to `prompt_builders_script.py` to prohibit abstract, subjective, and metaphorical descriptions, ensuring that `image_prompt` and `video_prompt` output directly renderable visual language.

**Architecture:** Modify prompt templates in `build_narration_prompt()` and `build_drama_prompt()` to add StoryCraft-style constraint rules in the `image_prompt` and `video_prompt` field descriptions.

**Tech Stack:** Python, Prompt Engineering

**Design Doc:** `docs/plans/2026-02-05-prompt-constraints-design.md`

---

### Task 1: Update image_prompt constraints in build_narration_prompt

**Files:**
- Modify: `lib/prompt_builders_script.py:95-101`

**Step 1: Modify the image_prompt section**

Replace the image_prompt description at lines 95-101 with:

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

**Step 2: Verify the change**

Run: `python -c "from lib.prompt_builders_script import build_narration_prompt; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add lib/prompt_builders_script.py
git commit -m "feat(prompt): add image_prompt constraints to narration mode"
```

---

### Task 2: Update video_prompt constraints in build_narration_prompt

**Files:**
- Modify: `lib/prompt_builders_script.py:103-108`

**Step 1: Modify the video_prompt section**

Replace the video_prompt description at lines 103-108 with:

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
   - dialogue: {{speaker, line}} array. Only populate when the source text has quoted dialogue. speaker must come from characters_in_segment.
```

**Step 2: Verify the change**

Run: `python -c "from lib.prompt_builders_script import build_narration_prompt; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add lib/prompt_builders_script.py
git commit -m "feat(prompt): add video_prompt constraints to narration mode"
```

---

### Task 3: Update image_prompt constraints in build_drama_prompt

**Files:**
- Modify: `lib/prompt_builders_script.py:178-184`

**Step 1: Modify the image_prompt section**

Replace the image_prompt description at lines 178-184 with:

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

**Step 2: Verify the change**

Run: `python -c "from lib.prompt_builders_script import build_drama_prompt; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add lib/prompt_builders_script.py
git commit -m "feat(prompt): add image_prompt constraints to drama mode"
```

---

### Task 4: Update video_prompt constraints in build_drama_prompt

**Files:**
- Modify: `lib/prompt_builders_script.py:186-191`

**Step 1: Modify the video_prompt section**

Replace the video_prompt description at lines 186-191 with:

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
   - dialogue: {{speaker, line}} array. Include character dialogue. speaker must come from characters_in_scene.
```

**Step 2: Verify the change**

Run: `python -c "from lib.prompt_builders_script import build_drama_prompt; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add lib/prompt_builders_script.py
git commit -m "feat(prompt): add video_prompt constraints to drama mode"
```

---

### Task 5: Integration Verification

**Files:**
- Test: `projects/test0205/` (existing test project)

**Step 1: Verify prompt builder functions work correctly**

Run:
```bash
python -c "
from lib.prompt_builders_script import build_narration_prompt, build_drama_prompt

# Test narration mode
prompt = build_narration_prompt(
    project_overview={'synopsis': 'test', 'genre': 'historical', 'theme': 'revenge', 'world_setting': 'ancient'},
    style='Photographic',
    style_description='realistic style',
    characters={'CharA': {'description': 'test'}},
    clues={'ClueA': {'description': 'test'}},
    segments_md='| E1S01 | test | 4s | no | no |'
)
assert 'Exclude' in prompt
assert 'Focus' in prompt
print('narration mode: OK')

# Test drama mode
prompt = build_drama_prompt(
    project_overview={'synopsis': 'test', 'genre': 'historical', 'theme': 'revenge', 'world_setting': 'ancient'},
    style='Anime',
    style_description='anime style',
    characters={'CharA': {'description': 'test'}},
    clues={'ClueA': {'description': 'test'}},
    scenes_md='| E1S01 | test | 8s | drama | no |'
)
assert 'Exclude' in prompt
assert 'single coherent action' in prompt
print('drama mode: OK')

print('All constraints verified!')
"
```

Expected:
```
narration mode: OK
drama mode: OK
All constraints verified!
```

**Step 2: Commit after verification**

```bash
git add -A
git commit -m "feat(prompt): complete prompt constraints implementation"
```

---

## Verification Checklist

After completing all Tasks, use the following commands to verify:

```bash
# 1. Confirm file was modified
git diff HEAD~4 lib/prompt_builders_script.py | head -100

# 2. Confirm key constraints are present
grep -n "Exclude metaphors" lib/prompt_builders_script.py
grep -n "Focus on the visually" lib/prompt_builders_script.py
grep -n "single coherent action" lib/prompt_builders_script.py

# 3. Confirm syntax is correct
python -c "from lib.prompt_builders_script import build_narration_prompt, build_drama_prompt; print('Import OK')"
```

---

## Follow-up Steps (Optional)

After implementation, use `/generate-script` to re-generate the test project's script and verify the constraint effects:

```bash
# Use generate-script skill to regenerate
# Then check image_prompt and video_prompt in the generated episode_1.json
```
