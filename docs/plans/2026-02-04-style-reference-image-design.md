# Style Reference Image Mechanism Design

**Date**: 2026-02-04
**Status**: Pending implementation

## Overview

Adds a project-level style reference image mechanism to video projects. Users can upload a style reference image; the system automatically analyzes it to generate a style description, which is then used for all subsequent image generation to ensure visual consistency across the project.

## Design Decisions

| Aspect | Decision |
|--------|----------|
| Storage | Local file `style_reference.png` |
| Scope | All image generation (characters, clues, storyboard) |
| Analysis mechanism | AI analyzes → saves description → only description used afterwards |
| Trigger | WebUI (create project + project overview) |
| Field design | `style` (manual) + `style_description` (AI) + `style_image` (path) |
| UI interaction | Thumbnail + editable description |

## Data Structure

Add the following fields to `project.json`:

```json
{
  "title": "Project Name",
  "style": "Anime",
  "style_image": "style_reference.png",
  "style_description": "Soft lighting, pastel color palette, digital painting medium...",
  "content_mode": "narration"
}
```

### Field Descriptions

| Field | Source | Purpose |
|-------|--------|---------|
| `style` | User input | Base style tag (e.g., Anime, Photographic) |
| `style_image` | User upload | Relative path to style reference image; kept for reference |
| `style_description` | AI-generated | Detailed style description used in generation prompts |

### Prompt Composition During Generation

```
Style: {style}
Visual style: {style_description}

{image_prompt}
```

## File Storage

```
projects/{project_name}/
├── style_reference.png    # Style reference image (fixed filename)
├── project.json
├── characters/
├── ...
```

## API Design

### New Endpoints

| Endpoint | Method | Function |
|----------|--------|----------|
| `/projects/{name}/style-image` | `POST` | Upload style reference image and trigger AI analysis |
| `/projects/{name}/style-image` | `DELETE` | Delete style reference image and related fields |

### POST Upload Flow

1. Receive the uploaded image file
2. Save to `projects/{project_name}/style_reference.png`
3. Call Gemini API to analyze image style
4. Save `style_description` and `style_image` to project.json
5. Return `{ style_image, style_description }`

### Style Analysis Prompt

```
Analyze the visual style of this image. Describe the lighting, color palette, medium (e.g., oil painting, digital art, photography), texture, and overall mood. Do NOT describe the subject matter (e.g., people, objects) or specific content. Focus ONLY on the artistic style. Provide a concise comma-separated list of descriptors suitable for an image generation prompt.
```

## WebUI Design

### Create Project Modal

Add an optional style reference image upload area after the existing form fields:

- User selects image → frontend stores locally, shows local preview
- User clicks "Create" → create project → upload image → analyze style
- User clicks "Cancel" → discard without any server operations

### Project Overview Page

Add a style reference image management section to the Overview tab:

- Display style image thumbnail
- Display AI-generated style description (editable)
- Support replacing image, deleting, and saving description

## Implementation Checklist

### Backend Changes

| File | Change |
|------|--------|
| `webui/server/routers/files.py` | Add `POST/DELETE /projects/{name}/style-image` endpoints |
| `lib/gemini_client.py` | Add `analyze_style_image()` method |
| `lib/prompt_builders.py` | Add `build_style_prompt()` function |
| `lib/project_manager.py` | Support reading/writing `style_image` and `style_description` fields |

### Frontend Changes

| File | Change |
|------|--------|
| `webui/index.html` | Add style image upload area to create project modal |
| `webui/js/projects.js` | Handle style image staging; upload on project creation |
| `webui/js/api.js` | Add `uploadStyleImage()` and `deleteStyleImage()` methods |
| `webui/project.html` | Add style image management section to Overview tab |
| `webui/js/project/overview.js` | Style image upload/delete/edit description logic |

### Skill Script Changes

| File | Change |
|------|--------|
| `generate-characters/scripts/generate_character.py` | Use `build_style_prompt()` |
| `generate-clues/scripts/generate_clue.py` | Use `build_style_prompt()` |
| `generate-storyboard/scripts/generate_storyboard.py` | Use `build_style_prompt()` |

### Documentation Updates

| File | Change |
|------|--------|
| `CLAUDE.md` | Add style reference image mechanism documentation |

## References

- Storycraft style reference image implementation: `docs/storycraft/app/features/create/actions/analyze-style.ts`
