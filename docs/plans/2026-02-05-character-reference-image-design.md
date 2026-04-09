# Character Reference Image Feature Design

**Date**: 2026-02-05
**Status**: Implemented

## Overview

Adds reference image support to the character generation feature. Users can upload a reference image (e.g., actor photo, hand-drawn sketch); when generating character design images, the AI uses this image to maintain appearance consistency.

## Terminology

| Type | Purpose | Source |
|------|---------|--------|
| **Reference image (reference_image)** | Used as AI input when generating character design images; controls appearance | Uploaded by user |
| **Design image (character_sheet)** | Used as reference when generating storyboards/videos; ensures character consistency | AI-generated |

## Data Structure Changes

### project.json

Add `reference_image` field to the `characters` structure:

```json
{
  "characters": {
    "Jiang Yuehui": {
      "description": "Young woman in her early twenties, oval face, willow-leaf brows...",
      "reference_image": "characters/refs/Jiang Yuehui.png",
      "character_sheet": "characters/Jiang Yuehui.png",
      "voice_style": "Gentle but authoritative"
    }
  }
}
```

### File Storage Structure

```
projects/{project_name}/
├── characters/
│   ├── refs/                  # New: reference image directory
│   │   └── Jiang Yuehui.png   # User-uploaded reference image
│   └── Jiang Yuehui.png       # AI-generated design image
```

## Backend API Changes

### 1. File Upload Route (`webui/server/routers/files.py`)

Add new upload type `character_ref`:

- Path: `POST /projects/{project_name}/upload/character_ref?name={char_name}`
- Save to: `characters/refs/{name}.png`
- Automatically update `reference_image` field in `project.json`

Add to `ALLOWED_EXTENSIONS`:
```python
"character_ref": [".png", ".jpg", ".jpeg", ".webp"],
```

Add handling logic in the `upload_file` function.

### 2. Character Management Route (`webui/server/routers/characters.py`)

Add optional field to `UpdateCharacterRequest`:
```python
reference_image: Optional[str] = None
```

Handle this field in the update logic.

### 3. Generation Route (`webui/server/routers/generate.py`)

`generate_character` endpoint adds logic to:
1. Check if the character has a `reference_image` field
2. If so, load the image file
3. Pass it to the `reference_images` parameter of `MediaGenerator.generate_image_async()`

### 4. CLI Script (`.claude/skills/generate-characters/scripts/generate_character.py`)

- **Remove** `--ref` command-line parameter
- Automatically read the character's `reference_image` field from `project.json`
- If present, load the image as a reference

## Frontend WebUI Changes

### Character Edit Modal Layout

Reference image and design image stacked vertically:

```
┌─────────────────────────────────────────────────┐
│  Edit Character                              [X] │
├─────────────────────────────────────────────────┤
│  Name: [Jiang Yuehui__________]                 │
│  Description: [Young woman in her early...]     │
│  Voice: [Gentle but authoritative______]        │
├─────────────────────────────────────────────────┤
│  Reference Image (user upload)                  │
│  ┌──────────────────────────────────────┐      │
│  │                                      │      │
│  │          [preview/placeholder]       │      │
│  │                                      │      │
│  └──────────────────────────────────────┘      │
│  [Choose file...]                               │
├─────────────────────────────────────────────────┤
│  Design Image (AI-generated)                    │
│  ┌──────────────────────────────────────┐      │
│  │                                      │      │
│  │          [preview/placeholder]       │      │
│  │                                      │      │
│  └──────────────────────────────────────┘      │
│  [Generate Design Image]  Version: [v1 ▼] [Restore] │
├─────────────────────────────────────────────────┤
│                    [Save] [Cancel]              │
└─────────────────────────────────────────────────┘
```

### Interaction Logic

1. **Select reference image**: After selecting a file, stage it in the frontend (File object) and show preview
2. **Click Save**:
   - If a new reference image file is selected → call `upload/character_ref` API first
   - Then save character data (including `reference_image` path)
3. **Generate design image**: Call `generate/character` API; backend automatically reads the reference image
4. **Version control**: Design images support version management (existing feature)

### Files Involved

- `webui/js/project/characters.js` — edit modal logic
- `webui/index.html` — modal HTML structure (if needed)

## User Operation Flow

```
1. Add/edit character → fill in name, description
         ↓
2. Select reference image (optional) → frontend preview
         ↓
3. Click "Save" → upload reference image + save character data
         ↓
4. Click "Generate Design Image" → API automatically uses reference image → AI generates
         ↓
5. View design image → if unsatisfied, regenerate (version management)
```

## Key Design Decisions

| Item | Decision | Reason |
|------|----------|--------|
| New field name | `reference_image` | Corresponds to `character_sheet`; clear semantics |
| Storage path | `characters/refs/{name}.png` | Separate directory; clean file organization |
| Number of reference images | Single | Simplifies implementation; covers main use cases |
| UI layout | Vertically stacked | Follows natural reading order |
| Save timing | Upload together when clicking Save | Avoids orphaned temporary files |
| CLI --ref parameter | Removed | Unified read from project.json; reduces user steps |

## Implementation Checklist

### Backend

- [ ] `files.py`: Add `character_ref` upload type
- [ ] `characters.py`: Add `reference_image` field to `UpdateCharacterRequest`
- [ ] `generate.py`: `generate_character` reads and uses reference image
- [ ] `generate_character.py` (CLI): Remove `--ref` parameter; auto-read from project.json

### Frontend

- [ ] `characters.js`: Add reference image upload area to modal
- [ ] `characters.js`: Handle reference image upload when saving
- [ ] `index.html`: Update modal HTML structure (if needed)
