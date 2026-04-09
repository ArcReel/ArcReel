# Character Design Prompt Optimization and Resolution Upgrade Design

> Optimize the prompt template for character design images, upgrade image/video resolution, and adjust the character design image aspect ratio

---

## 1. Requirements Overview

| Requirement | Description |
|-------------|-------------|
| Structured character description | Optimize the prompt template format; keep `description` as a single field |
| Character design image aspect ratio | Change from 16:9 to **3:4** (single full-body portrait) |
| Clue design image aspect ratio | Keep **16:9** unchanged |
| Image resolution | All images default to **2K** (API parameter `image_size="2K"`) |
| Video resolution | Change from 720p to **1080p** |
| WebUI sync | Adjust character card display box to accommodate 3:4 ratio |

---

## 2. Prompt Template Optimization

### 2.1 Character Design Image Prompt

**File to modify**: `.claude/skills/generate-characters/scripts/generate_character.py`

**Before**:
```python
def build_character_prompt(name: str, description: str, style: str = "") -> str:
    style_prefix = f", {style}" if style else ""

    prompt = f"""A professional character design reference image{style_prefix}.

Three-view design sheet for character "{name}". {description}

Three proportional full-body views arranged horizontally on a clean light-gray background: front view on the left, three-quarter view in the center, pure side profile on the right. Soft uniform studio lighting, no harsh shadows."""

    return prompt
```

**After**:
```python
def build_character_prompt(name: str, description: str, style: str = "") -> str:
    style_part = f", {style}" if style else ""

    prompt = f"""Character design reference image{style_part}.

Full-body character illustration of "{name}".

{description}

Composition: single full-body portrait, natural standing pose, facing the camera.
Background: clean light gray, no decorative elements.
Lighting: soft uniform studio lighting, no harsh shadows.
Quality: high resolution, clear details, accurate colors."""

    return prompt
```

**Improvements**:
- Removed the three-view requirement (changed to single full-body portrait for 3:4 ratio)
- Structured composition / background / lighting requirements
- Clearer hierarchy for better model comprehension

### 2.2 Clue Design Image Prompt

**File to modify**: `.claude/skills/generate-clues/scripts/generate_clue.py`

Keep the existing prompt structure unchanged; only ensure 2K resolution is used.

---

## 3. API Parameter Changes

### 3.1 GeminiClient Image Generation

**File to modify**: `lib/gemini_client.py`

Add `image_size` parameter support in the `_prepare_image_config` method:

```python
def _prepare_image_config(self, aspect_ratio: str, image_size: str = "2K"):
    """Build the image generation configuration."""
    return self.types.GenerateContentConfig(
        response_modalities=['IMAGE'],
        image_config=self.types.ImageConfig(
            aspect_ratio=aspect_ratio,
            image_size=image_size
        )
    )
```

Update all method signatures that call `_prepare_image_config`:
- `generate_image()` — add `image_size: str = "2K"` parameter
- `generate_image_async()` — add `image_size: str = "2K"` parameter

### 3.2 MediaGenerator Defaults

**File to modify**: `lib/media_generator.py`

```python
def generate_image(
    self,
    prompt: str,
    resource_type: str,
    resource_id: str,
    reference_images: Optional[List[Union[str, Path, Image.Image]]] = None,
    aspect_ratio: str = "9:16",
    image_size: str = "2K",  # New, default 2K
    **version_metadata
) -> Tuple[Path, int]:
```

Change the default video resolution to 1080p:

```python
def generate_video(
    self,
    prompt: str,
    resource_type: str,
    resource_id: str,
    start_image: Optional[Union[str, Path, Image.Image]] = None,
    aspect_ratio: str = "9:16",
    duration_seconds: str = "8",
    resolution: str = "1080p",  # Changed from 720p to 1080p
    ...
) -> Tuple[Path, int, any, Optional[str]]:
```

### 3.3 Character Design Image Aspect Ratio

**File to modify**: `.claude/skills/generate-characters/scripts/generate_character.py`

```python
output_path, version = generator.generate_image(
    prompt=prompt,
    resource_type="characters",
    resource_id=character_name,
    aspect_ratio="3:4"  # Changed from 16:9 to 3:4
)
```

### 3.4 WebUI Generation Router

**File to modify**: `webui/server/routers/generate.py`

Update the `get_aspect_ratio` function:

```python
def get_aspect_ratio(project: dict, resource_type: str) -> str:
    content_mode = project.get("content_mode", "narration")

    # Check for custom ratios
    custom_ratios = project.get("aspect_ratio", {})
    if resource_type in custom_ratios:
        return custom_ratios[resource_type]

    # Default ratios
    if resource_type == "characters":
        return "3:4"  # Character design images changed to 3:4
    elif resource_type == "clues":
        return "16:9"  # Clues keep 16:9
    elif content_mode == "narration":
        return "9:16"  # Narration mode portrait
    else:
        return "16:9"  # Drama mode landscape
```

Add `image_size` parameter to the character generation API:

```python
_, new_version = await generator.generate_image_async(
    prompt=full_prompt,
    resource_type="characters",
    resource_id=char_name,
    aspect_ratio=aspect_ratio,
    image_size="2K"  # New
)
```

---

## 4. WebUI Adjustments

### 4.1 Add 3:4 Ratio CSS Class

**File to modify**: `webui/css/styles.css`

```css
/* 3:4 portrait ratio (character design images) */
.aspect-portrait-3-4 {
    aspect-ratio: 3 / 4;
}
```

### 4.2 Character Card Rendering

**File to modify**: `webui/js/project.js`

Use the new ratio class for character card image containers:

```javascript
// Character card image container
<div class="aspect-portrait-3-4 bg-gray-700 rounded-lg overflow-hidden">
    <img src="${imageSrc}" class="w-full h-full object-cover" />
</div>
```

### 4.3 Character Edit Modal Preview

**File to modify**: `webui/project.html`

Adjust the character image preview area:

```html
<!-- Before -->
<div id="char-image-preview" class="hidden mb-4">
    <img src="" alt="Preview" class="max-h-48 mx-auto rounded">
</div>

<!-- After -->
<div id="char-image-preview" class="hidden mb-4">
    <img src="" alt="Preview" class="max-h-64 mx-auto rounded aspect-portrait-3-4 object-cover">
</div>
```

---

## 5. Documentation Updates

**File to modify**: `CLAUDE.md`

Update relevant notes:

```markdown
### Video Specs
- **Image resolution**: 2K (set via API parameter)
- **Video resolution**: 1080p

### Design Image Specs
- **Character design images**: 3:4 portrait, 2K resolution
- **Clue design images**: 16:9 landscape, 2K resolution
```

---

## 6. Modified File Checklist

| File | Changes |
|------|---------|
| `lib/gemini_client.py` | Add `image_size` parameter support |
| `lib/media_generator.py` | Default `image_size="2K"`, video `resolution="1080p"` |
| `.claude/skills/generate-characters/scripts/generate_character.py` | Optimize prompt, aspect ratio changed to 3:4 |
| `webui/server/routers/generate.py` | Update default aspect ratio and resolution |
| `webui/css/styles.css` | Add `.aspect-portrait-3-4` class |
| `webui/js/project.js` | Character card uses new aspect ratio |
| `webui/project.html` | Adjust character preview area |
| `CLAUDE.md` | Update documentation |

---

## 7. Backward Compatibility

- Custom `aspect_ratio` configuration in `project.json` has the highest priority and overrides defaults
- Existing character/clue design images in current projects are not affected; only newly generated images use the new specs
- The `description` field remains unchanged; no data migration required

---

*Design completed: 2026-01-30*
