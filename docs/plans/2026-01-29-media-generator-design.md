# MediaGenerator Intermediate Layer Design

**Date**: 2026-01-29
**Status**: Confirmed

---

## Requirements Overview

Introduce an automatic version management mechanism for image and video generation, transparent to callers. This is achieved by creating a `MediaGenerator` intermediate layer that wraps `GeminiClient` + `VersionManager`.

---

## Core Purpose

`MediaGenerator` is an intermediate layer that wraps `GeminiClient` + `VersionManager`, providing "caller-transparent" version management.

**Core principles:**
- Callers only need to provide `project_path` and `resource_id`
- Version management is fully automatic (backup, record, tracking)
- The existing `GeminiClient` responsibilities remain unchanged

**The 4 resource types covered:**

| Resource Type | Current Call Site | resource_id Format |
|--------------|-------------------|--------------------|
| `storyboards` | generate_storyboard.py, webui | `E1S01` (segment/scene ID) |
| `videos` | generate_video.py, webui | `E1S01` (segment/scene ID) |
| `characters` | generate_character.py, webui | character name |
| `clues` | generate_clue.py, webui | clue name |

---

## API Design

### Class Initialization

```python
class MediaGenerator:
    def __init__(
        self,
        project_path: Path,
        rate_limiter: Optional[RateLimiter] = None
    ):
        self.project_path = Path(project_path)
        self.gemini = GeminiClient(rate_limiter=rate_limiter)
        self.versions = VersionManager(project_path)
```

### Core Methods

| Method | Corresponding GeminiClient Method | New Parameters |
|--------|----------------------------------|---------------|
| `generate_image()` | `generate_image()` | `resource_type`, `resource_id` |
| `generate_image_async()` | `generate_image_async()` | `resource_type`, `resource_id` |
| `generate_video()` | `generate_video()` | `resource_type`, `resource_id` |
| `generate_video_async()` | `generate_video_async()` | `resource_type`, `resource_id` |

### Version Management Logic (automatic internally)

```
1. Check whether output_path already exists
2. If it exists → call ensure_current_tracked() to record the old file
3. Call GeminiClient to generate the new file
4. Call add_version() to record the new version
5. Return result
```

---

## Method Signatures

```python
def generate_image(
    self,
    prompt: str,
    resource_type: str,  # 'storyboards' | 'characters' | 'clues'
    resource_id: str,    # E1S01 | character_name | clue_name
    # The following parameters are forwarded to GeminiClient
    reference_images: Optional[List] = None,
    aspect_ratio: str = "9:16",
    **version_metadata  # Extra metadata: aspect_ratio, duration_seconds, etc.
) -> Tuple[Path, int]:
    """
    Returns:
        (output_path, version_number)
    """
```

### Automatic Output Path Inference

| resource_type | Output Path Pattern |
|--------------|---------------------|
| `storyboards` | `{project}/storyboards/scene_{resource_id}.png` |
| `videos` | `{project}/videos/scene_{resource_id}.mp4` |
| `characters` | `{project}/characters/{resource_id}.png` |
| `clues` | `{project}/clues/{resource_id}.png` |

### Return Value Changes

- Original `GeminiClient.generate_image()` returns `Image`
- New `MediaGenerator.generate_image()` returns `(Path, int)` — path and version number

---

## Caller Migration Example

### Current skill script call (example: generate_character.py)

```python
# Before
client = GeminiClient()
client.generate_image(
    prompt=prompt,
    aspect_ratio="16:9",
    output_path=output_path
)
```

### After migration

```python
# After
from lib.media_generator import MediaGenerator

generator = MediaGenerator(project_dir)
output_path, version = generator.generate_image(
    prompt=prompt,
    resource_type="characters",
    resource_id=character_name,
    aspect_ratio="16:9"
)
```

### Changes

1. Import class changes from `GeminiClient` → `MediaGenerator`
2. Initialization now takes `project_path` (instead of no argument)
3. Call now includes `resource_type` and `resource_id`
4. `output_path` removed (inferred automatically)
5. Return value now includes `version` number

### Changes in the webui router

- Can remove the code that manually calls `VersionManager`
- Directly use `MediaGenerator` for simpler logic

---

## File Structure and Implementation Plan

### New Files

```
lib/media_generator.py    # MediaGenerator class
```

### Files to Modify

| File | Changes |
|------|---------|
| `.claude/skills/generate-storyboard/scripts/generate_storyboard.py` | Replace GeminiClient → MediaGenerator |
| `.claude/skills/generate-video/scripts/generate_video.py` | Replace GeminiClient → MediaGenerator |
| `.claude/skills/generate-characters/scripts/generate_character.py` | Replace GeminiClient → MediaGenerator |
| `.claude/skills/generate-clues/scripts/generate_clue.py` | Replace GeminiClient → MediaGenerator |
| `webui/server/routers/generate.py` | Simplify by removing manual version management code |

### Files NOT to Modify

- `lib/gemini_client.py` — unchanged
- `lib/version_manager.py` — unchanged

### Implementation Priority

| Phase | Content |
|-------|---------|
| Phase 1 | Create `lib/media_generator.py`, implement 4 core methods |
| Phase 2 | Migrate 4 skill scripts |
| Phase 3 | Simplify webui router |

---

## Notes

1. **Thread safety**: `VersionManager` already implements thread-safe locking; `MediaGenerator` can reuse it directly
2. **Async support**: Both synchronous and asynchronous versions of each method must be provided
3. **Backward compatibility**: `GeminiClient` remains unchanged; existing direct calls are not affected
4. **Metadata passing**: Extra information (e.g. `aspect_ratio`, `duration_seconds`) is supported via `**version_metadata`
