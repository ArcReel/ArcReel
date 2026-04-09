# WebUI Image / Video Regeneration and Version Control Design

**Date**: 2026-01-29
**Status**: Pending implementation

---

## Requirements Overview

1. Add generate / regenerate buttons to WebUI edit modals that call GeminiClient async methods to generate images / videos
2. Introduce a version number mechanism for storyboard images, videos, character images, and clue images — retain history versions and allow rollback
3. Each historical version is bound to its corresponding prompt

---

## Design Decisions

| Decision | Choice |
|----------|--------|
| Coverage | Segment/scene storyboards, videos, character design images, clue design images (4 types total) |
| Generation UX | Loading state inside modal, does not block other editing |
| Version storage | Centralized `versions/` directory; current version stays at original path |
| Version metadata | Managed in a single `versions/versions.json` file |
| History retention | All versions retained indefinitely |
| Version switch UI | Dropdown selector above the preview area |
| Grid images | Not included in version control (only `scene_*.png` needs versioning) |

---

## Data Structure Design

### 1. Version Directory Structure

```
projects/{project_name}/
├── versions/                    # Centralized version directory
│   ├── versions.json            # Version metadata
│   ├── storyboards/             # Storyboard image history (scene_*.png only)
│   │   ├── E1S01_v1_20260129T103045.png
│   │   ├── E1S01_v2_20260129T114530.png
│   │   └── ...
│   ├── videos/                  # Video history
│   │   ├── E1S01_v1_20260129T120000.mp4
│   │   └── ...
│   ├── characters/              # Character image history
│   │   ├── character1_v1_20260129T090000.png
│   │   └── ...
│   └── clues/                   # Clue image history
│       ├── clue1_v1_20260129T091500.png
│       └── ...
├── storyboards/                 # Current version (at original path)
│   ├── scene_E1S01.png          # Needs version control
│   ├── grid_001.png             # Grid image — no version control needed
│   └── ...
├── videos/
├── characters/
└── clues/
```

### 2. versions.json Structure

```json
{
  "storyboards": {
    "E1S01": {
      "current_version": 2,
      "versions": [
        {
          "version": 1,
          "file": "storyboards/E1S01_v1_20260129T103045.png",
          "prompt": "Medium shot, back garden of the Jiang mansion...",
          "created_at": "2026-01-29T10:30:45Z",
          "aspect_ratio": "9:16"
        },
        {
          "version": 2,
          "file": "storyboards/E1S01_v2_20260129T114530.png",
          "prompt": "Medium shot, revised description...",
          "created_at": "2026-01-29T11:45:30Z",
          "aspect_ratio": "9:16"
        }
      ]
    }
  },
  "videos": {
    "E1S01": {
      "current_version": 1,
      "versions": [
        {
          "version": 1,
          "file": "videos/E1S01_v1_20260129T120000.mp4",
          "prompt": "Camera slowly pushes in...",
          "created_at": "2026-01-29T12:00:00Z",
          "duration_seconds": 4
        }
      ]
    }
  },
  "characters": {
    "character1": {
      "current_version": 1,
      "versions": [
        {
          "version": 1,
          "file": "characters/character1_v1_20260129T090000.png",
          "prompt": "A woman in her early twenties, oval face, willow-leaf brows...",
          "created_at": "2026-01-29T09:00:00Z"
        }
      ]
    }
  },
  "clues": {
    "jade_pendant": {
      "current_version": 1,
      "versions": [
        {
          "version": 1,
          "file": "clues/jade_pendant_v1_20260129T091500.png",
          "prompt": "Emerald-green jade pendant, carved with a lotus pattern...",
          "created_at": "2026-01-29T09:15:00Z"
        }
      ]
    }
  }
}
```

**Notes**:
- Keys for `storyboards` and `videos` use segment/scene IDs (e.g. `E1S01`)
- Keys for `characters` and `clues` use the name
- Grid images (`grid_*.png`) are not included in version control

---

## Backend API Design

### 1. API Endpoints

| Method | Path | Function |
|--------|------|----------|
| `POST` | `/api/v1/projects/{name}/generate/storyboard/{segment_id}` | Generate storyboard image (first time or new version) |
| `POST` | `/api/v1/projects/{name}/generate/video/{segment_id}` | Generate video (first time or new version) |
| `POST` | `/api/v1/projects/{name}/generate/character/{char_name}` | Generate character design image |
| `POST` | `/api/v1/projects/{name}/generate/clue/{clue_name}` | Generate clue design image |
| `GET` | `/api/v1/projects/{name}/versions/{resource_type}/{resource_id}` | Get resource version list |
| `POST` | `/api/v1/projects/{name}/versions/{resource_type}/{resource_id}/restore/{version}` | Restore to specified version |

### 2. Generate Request / Response

```json
// POST /api/v1/projects/{name}/generate/storyboard/{segment_id}
// Request body
{
  "prompt": "image_prompt text",
  "script_file": "episode_1.json"
}

// Response
{
  "success": true,
  "version": 1,
  "file_path": "storyboards/scene_E1S01.png",
  "created_at": "2026-01-29T11:45:30Z"
}
```

### 3. Version List Response

```json
// GET /api/v1/projects/{name}/versions/storyboards/E1S01
{
  "resource_type": "storyboards",
  "resource_id": "E1S01",
  "current_version": 2,
  "versions": [
    {
      "version": 1,
      "file": "versions/storyboards/E1S01_v1_20260129T103045.png",
      "file_url": "/api/v1/files/{name}/versions/storyboards/E1S01_v1_20260129T103045.png",
      "prompt": "Medium shot, back garden of the Jiang mansion...",
      "created_at": "2026-01-29T10:30:45Z"
    },
    {
      "version": 2,
      "file": "versions/storyboards/E1S01_v2_20260129T114530.png",
      "file_url": "/api/v1/files/{name}/versions/storyboards/...",
      "prompt": "Revised description...",
      "created_at": "2026-01-29T11:45:30Z",
      "is_current": true
    }
  ]
}
```

### 4. Restore Response

```json
// POST /api/v1/projects/{name}/versions/storyboards/E1S01/restore/1
{
  "success": true,
  "restored_version": 1,
  "new_current_version": 3,
  "prompt": "Original description text..."
}
```

### 5. Core Generation Logic

```python
async def generate_storyboard(name, segment_id, prompt, script_file):
    current_file = f"storyboards/scene_{segment_id}.png"

    if current_file exists:
        # File already exists → back up to versions directory
        backup_to_versions(current_file, prompt)

    # Call GeminiClient to generate new image
    new_image = await gemini_client.generate_image_async(
        prompt=prompt,
        reference_images=get_character_refs(segment_id),
        aspect_ratio=get_aspect_ratio(project),
        output_path=current_file
    )

    # Update versions.json (add new version record)
    add_version_record(segment_id, prompt)

    # Update generated_assets in the script
    update_script_assets(script_file, segment_id, current_file)

    return {"success": True, "version": new_version, ...}
```

---

## Frontend UI Design

### 1. Edit Modal Changes

In the edit modals for segments/scenes, characters, and clues, add to the preview area:
- **Version dropdown selector**: displayed above the preview image
- **Generate button**: shows "Generate" when no image exists; shows "Regenerate" when an image exists

```
┌─────────────────────────────────────┐
│  Storyboard Preview                  │
│  ┌─────────────────────┬──────────┐ │
│  │ Version: [v2 current ▼] │ Regenerate │ │
│  └─────────────────────┴──────────┘ │
│  ┌─────────────────────────────────┐ │
│  │                                 │ │
│  │         (image preview area)    │ │
│  │                                 │ │
│  └─────────────────────────────────┘ │
│  Version prompt: Medium shot, ...    │
└─────────────────────────────────────┘
```

### 2. Version Dropdown Behavior

- When switching versions: the preview area shows the image for that version; the prompt for that version is displayed below (read-only)
- The prompt in the current edit box is independent (used for generating a new version)
- When a non-current version is selected, a "Restore This Version" button appears

### 3. Generate Button States

| State | Button Text | Style |
|-------|------------|-------|
| No image | Generate | Green primary button |
| Image exists | Regenerate | Blue button |
| Generating | Generating... | Gray disabled + loading spinner |

### 4. Restore Interaction

When the user selects a non-current version:

```
┌─────────────────────────────────────┐
│  Storyboard Preview                  │
│  ┌─────────────────────┬──────────┐ │
│  │ Version: [v1 ▼]     │  Restore │ │
│  └─────────────────────┴──────────┘ │
│  ┌─────────────────────────────────┐ │
│  │                                 │ │
│  │    (v1 image preview)           │ │
│  │                                 │ │
│  └─────────────────────────────────┘ │
│  Historical prompt: Original text... │
└─────────────────────────────────────┘
```

After clicking "Restore":
1. Back up the current version to the `versions/` directory
2. Copy the selected historical version file to the current path
3. Update `current_version` in `versions.json`
4. Fill the historical version's prompt into the edit box
5. Refresh the preview and version list

---

## File Structure and Implementation Modules

### 1. New / Modified Files

| File | Type | Description |
|------|------|-------------|
| `lib/version_manager.py` | New | Core version management logic (backup, restore, record) |
| `webui/server/routers/generate.py` | New | Generation API routes |
| `webui/server/routers/versions.py` | New | Version management API routes |
| `webui/server/app.py` | Modify | Register new routes |
| `webui/js/api.js` | Modify | Add generation and version-related API calls |
| `webui/js/project.js` | Modify | Add version selector and generate button interaction logic |
| `webui/project.html` | Modify | Update modal UI (version selector, generate button) |
| `webui/css/styles.css` | Modify | Add version selector and loading state styles |

### 2. lib/version_manager.py Core Class

```python
class VersionManager:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.versions_dir = project_path / "versions"
        self.versions_file = self.versions_dir / "versions.json"

    def get_versions(self, resource_type: str, resource_id: str) -> dict:
        """Get all version information for a resource."""
        pass

    def add_version(self, resource_type: str, resource_id: str,
                    file_path: str, prompt: str, **metadata) -> int:
        """Add a new version record; return the version number."""
        pass

    def backup_current(self, resource_type: str, resource_id: str,
                       current_file: Path, prompt: str) -> None:
        """Back up the current file to the versions directory."""
        pass

    def restore_version(self, resource_type: str, resource_id: str,
                        version: int) -> dict:
        """Restore to the specified version; return restore info."""
        pass

    def get_current_version(self, resource_type: str, resource_id: str) -> int:
        """Get the current version number."""
        pass
```

---

## Implementation Priority

| Phase | Content | Estimated Effort |
|-------|---------|-----------------|
| Phase 1 | `VersionManager` + generation API (supports first-time generation and regeneration) | Medium |
| Phase 2 | Frontend generate button and loading state | Small |
| Phase 3 | Version list API + frontend version selector | Medium |
| Phase 4 | Restore API + frontend restore interaction | Small |

---

## Notes

1. **Grid image exclusion**: `grid_*.png` are not included in version management; only `scene_*.png` is processed
2. **Reference image passing**: when generating storyboards/videos, automatically retrieve `characters_in_segment` and `clues_in_segment` from the segment/scene, and pass the corresponding design images as references
3. **Aspect ratio**: automatically selected based on `content_mode` (narration: 9:16, drama: 16:9)
4. **Concurrency safety**: `versions.json` reads/writes must be locked to prevent concurrent conflicts
5. **Error handling**: if an API call fails, the original file is preserved and existing versions are unaffected
