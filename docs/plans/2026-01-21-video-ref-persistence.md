# Video Reference Persistence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement persistent storage of Veo video references so that previously generated videos can be extended in multi-step tasks.

**Architecture:** Save the `video.uri` returned after video generation to a checkpoint JSON file. When resuming, reconstruct the Video object using `types.Video(uri=saved_uri)` and continue calling the extend API.

**Tech Stack:** Python, google-genai SDK, JSON file storage

**Key Findings:**
- The `types.Video` object has a `uri` field containing the video URI on Gemini servers
- Videos are retained on the server for 2 days; each extend call resets the timer
- Video objects can be reconstructed via `types.Video(uri=saved_uri)`

**Important Limitations:**
- Veo extend currently only supports 16:9 landscape videos (the API returns an error for 9:16)
- Need to decide whether to switch to 16:9 format or wait for an API update

---

## Task 1: Update the Checkpoint Data Structure

**Files:**
- Modify: `.claude/skills/generate-video/scripts/generate_video.py:105-127`

**Step 1: Modify the checkpoint structure to add the video_uri field**

Update the `save_checkpoint()` function to add a `video_uri` parameter:

```python
def save_checkpoint(
    project_dir: Path,
    episode: int,
    current_segment: int,
    current_scene_index: int,
    completed_segments: list,
    started_at: str,
    video_uri: Optional[str] = None  # New: video URI for resume
):
    """Save checkpoint, including the video reference URI."""
    checkpoint_path = get_checkpoint_path(project_dir, episode)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        "episode": episode,
        "current_segment": current_segment,
        "current_scene_index": current_scene_index,
        "completed_segments": completed_segments,
        "started_at": started_at,
        "updated_at": datetime.now().isoformat(),
        "video_uri": video_uri,  # New: save video URI
        "video_uri_expires_at": (datetime.now() + timedelta(days=2)).isoformat() if video_uri else None
    }

    with open(checkpoint_path, 'w', encoding='utf-8') as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)
```

**Step 2: Add timedelta import**

Add at the top of the file:

```python
from datetime import datetime, timedelta
```

**Step 3: Verify syntax**

Run: `python -m py_compile .claude/skills/generate-video/scripts/generate_video.py`
Expected: no output (success)

**Step 4: Commit**

```bash
git add .claude/skills/generate-video/scripts/generate_video.py
git commit -m "feat: add video_uri field to checkpoint for resume support"
```

---

## Task 2: Add Video URI Restore Method to GeminiClient

**Files:**
- Modify: `lib/gemini_client.py` (add new method after `extend_video`)

**Step 1: Add restore_video_ref() method**

After `extend_video()`, add:

```python
def restore_video_ref(self, video_uri: str):
    """
    Restore a video reference object from a saved URI.

    Args:
        video_uri: Previously saved video URI
                   (e.g. "https://generativelanguage.googleapis.com/...")

    Returns:
        A types.Video object that can be passed to extend_video()

    Note:
        - Videos are retained on the server for 2 days
        - Each extend call resets the 2-day timer
        - If the video has expired, an exception will be raised
    """
    if not video_uri:
        raise ValueError("video_uri cannot be empty")

    return self.types.Video(uri=video_uri)
```

**Step 2: Verify syntax**

Run: `python -m py_compile lib/gemini_client.py`
Expected: no output (success)

**Step 3: Test import**

Run: `PYTHONPATH=. python -c "from lib.gemini_client import GeminiClient; c = GeminiClient(); print('restore_video_ref exists:', hasattr(c, 'restore_video_ref'))"`
Expected: `restore_video_ref exists: True`

**Step 4: Commit**

```bash
git add lib/gemini_client.py
git commit -m "feat: add restore_video_ref() method for resuming video extensions"
```

---

## Task 3: Update generate_video_with_ref to Return the Video URI

**Files:**
- Modify: `lib/gemini_client.py:275-353` (`generate_video_with_ref` method)

**Step 1: Modify return value to include video_uri**

Change the return statement from:

```python
return output_path, video_ref
```

to:

```python
return output_path, video_ref, video_ref.uri
```

Also update the docstring return type:

```python
def generate_video_with_ref(
    ...
) -> tuple:
    """
    Generate a video and return the video reference for subsequent extension.

    ...

    Returns:
        A (output_path, video_ref, video_uri) triple:
        - output_path: path to the video file
        - video_ref: Video object for use with extend_video() in the current session
        - video_uri: string URI that can be saved for cross-session resumption
    """
```

**Step 2: Verify syntax**

Run: `python -m py_compile lib/gemini_client.py`
Expected: no output (success)

**Step 3: Commit**

```bash
git add lib/gemini_client.py
git commit -m "feat: return video_uri from generate_video_with_ref for persistence"
```

---

## Task 4: Update extend_video to Return the Video URI

**Files:**
- Modify: `lib/gemini_client.py:355-432` (`extend_video` method)

**Step 1: Modify return value to include video_uri**

Change the return statement from:

```python
return output_path, new_video_ref
```

to:

```python
return output_path, new_video_ref, new_video_ref.uri
```

Also update the docstring return type:

```python
def extend_video(
    ...
) -> tuple:
    """
    Extend an existing video (+7 seconds per call, up to 20 extensions).

    ...

    Returns:
        A (output_path, new_video_ref, new_video_uri) triple:
        - output_path: path to the extended video file
        - new_video_ref: new Video object for continuing to extend
        - new_video_uri: string URI that can be saved for cross-session resumption
    """
```

**Step 2: Verify syntax**

Run: `python -m py_compile lib/gemini_client.py`
Expected: no output (success)

**Step 3: Commit**

```bash
git add lib/gemini_client.py
git commit -m "feat: return video_uri from extend_video for persistence"
```

---

## Task 5: Update generate_continuous_video to Save and Restore Video URIs

**Files:**
- Modify: `.claude/skills/generate-video/scripts/generate_video.py:218-369`

**Step 1: Update video generation logic to save URI**

In the `generate_continuous_video()` function, modify the video generation section:

```python
# Inside the for scene_idx, scene in enumerate(segment): loop

try:
    if video_ref is None:
        # First scene: use image-to-video
        print(f"    Generating initial video ({duration}s)...")
        output_path, video_ref, video_uri = client.generate_video_with_ref(
            prompt=prompt,
            start_image=storyboard_path,
            aspect_ratio="16:9",  # Note: extend only supports 16:9
            duration_seconds=str(duration),
            resolution="720p",
            output_path=segment_output
        )
    else:
        # Subsequent scenes: use extend
        print(f"    Extending video (+7s)...")
        output_path, video_ref, video_uri = client.extend_video(
            video_ref=video_ref,
            prompt=prompt,
            output_path=segment_output
        )

    # Save checkpoint (including video_uri)
    save_checkpoint(
        project_dir, episode,
        seg_idx, scene_idx + 1,
        segment_videos, started_at,
        video_uri=video_uri  # Save URI for resume
    )
```

**Step 2: Add restore logic**

After loading the checkpoint, add restore logic:

```python
# Inside the if resume: block, after checkpoint is loaded
if resume:
    checkpoint = load_checkpoint(project_dir, episode)
    if checkpoint:
        start_segment = checkpoint.get('current_segment', 0)
        completed_segments = checkpoint.get('completed_segments', [])
        started_at = checkpoint.get('started_at', started_at)

        # Restore video reference
        saved_uri = checkpoint.get('video_uri')
        if saved_uri:
            expires_at = checkpoint.get('video_uri_expires_at')
            if expires_at:
                expires = datetime.fromisoformat(expires_at)
                if datetime.now() < expires:
                    video_ref = client.restore_video_ref(saved_uri)
                    print("Restored video reference from checkpoint")
                else:
                    print("Video reference has expired; will regenerate from this segment")
                    video_ref = None

        print(f"Resuming from segment {start_segment + 1}")
    else:
        print("No checkpoint found; starting from the beginning")
```

**Step 3: Verify syntax**

Run: `python -m py_compile .claude/skills/generate-video/scripts/generate_video.py`
Expected: no output (success)

**Step 4: Commit**

```bash
git add .claude/skills/generate-video/scripts/generate_video.py
git commit -m "feat: save and restore video_uri in continuous video generation"
```

---

## Task 6: Update Documentation for Video Reference Persistence

**Files:**
- Modify: `.claude/skills/generate-video/SKILL.md`
- Modify: `CLAUDE.md`

**Step 1: Update SKILL.md to add persistence notes**

After the "Resume from checkpoint" section, add:

```markdown
### Video Reference Persistence

Continuous video mode automatically saves the video reference (URI) to the checkpoint file:

- Location: `projects/{project_name}/videos/.checkpoint_ep{N}.json`
- Videos are retained on Gemini servers for 2 days
- Each extend call resets the 2-day timer
- When using `--resume`, the video reference is automatically restored

**Notes:**
- If more than 2 days pass without resuming, the video reference will expire
- After expiry, that segment must be regenerated from scratch
- It is recommended to complete the entire episode as soon as generation starts
```

**Step 2: Update CLAUDE.md to add related notes**

In the "Resume from checkpoint" section, add:

```markdown
### Saving Video References

Checkpoint files save the video reference URI, valid for 2 days:

```json
{
  "episode": 1,
  "current_segment": 0,
  "current_scene_index": 3,
  "video_uri": "https://generativelanguage.googleapis.com/...",
  "video_uri_expires_at": "2026-01-23T12:00:00"
}
```
```

**Step 3: Commit**

```bash
git add .claude/skills/generate-video/SKILL.md CLAUDE.md
git commit -m "docs: add video reference persistence documentation"
```

---

## Task 7: Add 16:9 Format Support Notes

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.claude/skills/generate-video/SKILL.md`

**Step 1: Update CLAUDE.md video spec notes**

Modify the "Video Specs" section:

```markdown
### Video Specs
- **Aspect ratio**: 16:9 landscape (Veo extend limitation)
- **Single scene duration**: 8 seconds by default
- **Extension duration**: +7 seconds per call
- **Maximum continuous video duration**: 148 seconds (~2.5 minutes)
- **Resolution**: 720p (extend mode limitation)
- **Storyboard format**: multi-panel grid (16:9 landscape, adaptive 2x2 or 2x3 layout)

> **Important**: The Veo extend API currently only supports 16:9 landscape videos.
> 9:16 portrait videos cannot be extended.
> To produce 9:16 portrait output, use ffmpeg to crop/convert in post-processing.
```

**Step 2: Update SKILL.md to add format restriction notes**

In the "Veo 3.1 Extension Limits" table, add:

```markdown
| Aspect ratio restriction | 16:9 landscape only |
```

And add the note:

```markdown
> **API Limitation**: Although the documentation says both 9:16 and 16:9 are supported, testing has shown that the extend API only accepts 16:9 landscape videos.
> 9:16 portrait videos return the error: `Aspect ratio of the input video must be 16:9`
```

**Step 3: Commit**

```bash
git add CLAUDE.md .claude/skills/generate-video/SKILL.md
git commit -m "docs: clarify 16:9 aspect ratio requirement for Veo extend"
```

---

## Task 8: Verify the Full Flow

**Files:** No modifications — verification only

**Step 1: Verify script syntax**

Run:
```bash
python -m py_compile lib/gemini_client.py
python -m py_compile .claude/skills/generate-video/scripts/generate_video.py
```
Expected: no output (success)

**Step 2: Verify CLI help**

Run: `PYTHONPATH=. python .claude/skills/generate-video/scripts/generate_video.py --help`
Expected: help text shown, including `--continuous`, `--episode`, `--resume` options

**Step 3: Verify segment grouping**

Run:
```bash
PYTHONPATH=. python -c "
import json
from pathlib import Path

script = json.load(open('projects/shanyang_renlei/scripts/episode_01.json'))
scenes = [s for s in script['scenes'] if s.get('episode', 1) == 1]

segments = []
current = []
for s in scenes:
    if s.get('segment_break') and current:
        segments.append(current)
        current = []
    current.append(s)
if current:
    segments.append(current)

print(f'Scenes: {len(scenes)}')
print(f'Segments: {len(segments)}')
for i, seg in enumerate(segments):
    print(f'  Segment {i+1}: {len(seg)} scenes')
"
```
Expected: 22 scenes, 4 segments

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete video reference persistence implementation"
```

---

## Optional: Task 9: Live API Test

**Note: This task consumes API quota and is optional.**

**Step 1: Generate first video and save checkpoint**

```bash
PYTHONPATH=. python -c "
from lib.gemini_client import GeminiClient
from pathlib import Path
import json

client = GeminiClient()
project_dir = Path('projects/shanyang_renlei')

# Generate video
path, ref, uri = client.generate_video_with_ref(
    prompt='A 6-second landscape video (16:9). Night cityscape, exterior of a five-star hotel.',
    start_image=project_dir / 'storyboards/grid_001.png',
    aspect_ratio='16:9',
    duration_seconds='6',
    resolution='720p',
    output_path=project_dir / 'videos/test_persist.mp4'
)

print(f'Video generated: {path}')
print(f'URI: {uri}')

# Save URI
(project_dir / 'videos/test_uri.txt').write_text(uri)
print('URI saved')
"
```

**Step 2: Restore and extend video**

```bash
PYTHONPATH=. python -c "
from lib.gemini_client import GeminiClient
from pathlib import Path

client = GeminiClient()
project_dir = Path('projects/shanyang_renlei')

# Read saved URI
uri = (project_dir / 'videos/test_uri.txt').read_text().strip()
print(f'Read URI: {uri[:50]}...')

# Restore video reference
video_ref = client.restore_video_ref(uri)
print('Video reference restored')

# Extend video
path, ref, new_uri = client.extend_video(
    video_ref=video_ref,
    prompt='Continued: hotel lobby interior, crystal chandelier, crimson carpet, a man in a black leather jacket walks in.',
    output_path=project_dir / 'videos/test_persist_extended.mp4'
)

print(f'Extension successful: {path}')
"
```

Expected: two video files generated successfully; the extended video is approximately 13 seconds long

---

## Summary

After implementation, the video reference persistence workflow is:

1. **First generation**: `generate_video_with_ref()` returns `(path, video_ref, video_uri)`
2. **Save URI**: `save_checkpoint(..., video_uri=video_uri)`
3. **After interruption**: `load_checkpoint()` reads `video_uri`
4. **Rebuild reference**: `restore_video_ref(video_uri)` returns `video_ref`
5. **Continue extending**: `extend_video(video_ref, ...)`

Validity period: 2 days (reset by each extend call)
