# CapCut Draft Export Feature Design

**Date**: 2026-03-23
**Status**: Confirmed

---

## Overview

Export the generated video clips for a single episode of ArcReel as a CapCut (JianYing) draft file. After decompressing to the local CapCut draft directory, users can open it in CapCut for secondary editing (subtitles, transitions, effects, etc.).

### Design Goals

- Export by episode, with video assets arranged sequentially on the timeline
- Narration mode includes subtitle track (`novel_text`)
- Self-contained assets: ZIP contains draft JSON + video files
- Reuse existing download token + browser native download mechanism
- Users fill in the CapCut draft directory via a dialog, backend generates `draft_content.json` with correct paths

### Non-Goals

- No support for CapCut template mode (reading/modifying existing drafts)
- No support for remote URL asset references
- No export of audio tracks (BGM, voice-over)
- No support for CapCut international edition
- Drama mode does not export subtitles (multi-character dialogue structure is complex, non-MVP)

---

## Technology Selection

**pyJianYingDraft** (`pyjianyingdraft>=0.2.6`): A mature community library with 2800+ stars, clean API, pip-installable, consistent with ArcReel's Python backend. System dependency: `mediainfo` (requires `apt-get install` in Docker).

---

## Backend API Design

### Endpoints

#### 1. Issue token — reuse existing endpoint

```
POST /api/v1/projects/{name}/export/token
```

Directly reuse the existing export token endpoint (`create_download_token`, `purpose="download"`). Frontend gets the token and constructs the CapCut draft-specific download URL, no need to add a new token endpoint.

#### 2. Export draft ZIP (new endpoint)

```
GET /api/v1/projects/{name}/export/jianying-draft
    ?episode={N}
    &draft_path={user local CapCut draft root directory}
    &download_token={token}
```

- `episode` (required): episode number
- `draft_path` (required): absolute path to user's local CapCut draft root directory
- `download_token` (required): download token
- Response: `application/zip` streaming download

Error codes:

| Status Code | Scenario |
|-------------|---------|
| 404 | Project or episode does not exist |
| 422 | No completed videos for this episode / draft_path is empty or contains control characters |
| 401 | Token expired or invalid |
| 403 | Token does not match project |

Authentication: GET endpoint does **not** add `Depends(get_current_user)`, manually validates `download_token` parameter in the function body (same pattern as existing `export_project_archive`).

---

## Service Layer Design

New `server/services/jianying_draft_service.py`:

```python
class JianyingDraftService:
    def export_episode_draft(
        self, project_name: str, episode: int, draft_path: str
    ) -> Path:
```

### Core Flow

1. **Load script**: distinguish `content_mode` (narration → segments, drama → scenes)
2. **Collect completed videos**: traverse `generated_assets.video_clip`, keep only segments with existing files; narration mode additionally extracts `novel_text`
3. **Determine canvas size**: `aspect_ratio.video` → 16:9 = 1920×1080, 9:16 = 1080×1920
4. **Create temp directory**, copy videos to `assets/` (prefer hard links, fall back to `shutil.copy2` for cross-filesystem)
5. **Call pyjianyingdraft to generate draft**:
   - `DraftFolder(tmp_dir)` → `create_draft(draft_name, width, height)`
   - `add_track(TrackType.video)` — video track
   - For each segment, pre-read actual duration with `VideoMaterial(path).duration`, construct `VideoSegment`
   - Narration mode: `add_track(TrackType.text, "Subtitle")` + per-segment `TextSegment`
6. **Path post-processing**: after `save()`, read `draft_content.json` and replace temp directory paths with `{draft_path}/{draft_name}/assets/...`
7. **Package ZIP**, `BackgroundTask` cleans up temp files

### Video Duration Strategy

Ignore `duration_seconds` in the script, use actual duration automatically extracted by pyjianyingdraft from the video file. Avoids `ValueError` from duration mismatches.

### Subtitle Track (narration mode only)

```python
if content_mode == "narration":
    script.add_track(draft.TrackType.text, "Subtitle")
    text_style = draft.TextStyle(
        size=8.0, color=(1.0, 1.0, 1.0), align=1,
        bold=True, auto_wrapping=True,  # exported as subtitle type
    )
    for clip in clips:
        if clip.get("novel_text"):
            seg = draft.TextSegment(
                text=clip["novel_text"],
                timerange=trange(offset_us, clip["actual_duration_us"]),
                style=text_style,
            )
            script.add_segment(seg)
```

Subtitle duration matches the actual duration of the corresponding video segment. Segments without `novel_text` skip subtitles.

---

## Frontend Interaction Design

### Entry Point

Add a third option in the existing `ExportScopeDialog`: **"Export as CapCut Draft"**.

After selection, expand additional form:

#### 1. Episode Selection (dropdown)

- Data source: `project.episodes[]`
- Only list episodes with completed videos
- Auto-select if only one episode, don't show dropdown

#### 2. CapCut Draft Directory (text input)

- Input placeholder shows example paths based on OS detection (browser cannot get system username, serves as prompt only):
  - Windows: `C:\Users\YourUsername\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft`
  - macOS: `/Users/YourUsername/Movies/JianyingPro/User Data/Projects/com.lveditor.draft`
- Prompt below input: *"Please enter the full path to the CapCut draft directory. Open CapCut → Settings → Draft Location to find it."*
- `localStorage` key `arcreel_jianying_draft_path` caches the value, auto-fills from cache when available (higher priority than placeholder)

#### 3. Export Button

- Click: issue token → `window.open(GET url)` triggers browser download
- Button disabled during download, shows "Exporting..."

### ExportScopeDialog Redesign

The existing component is a simple two-button selector ("Current version only"/"All data"), selection triggers `onSelect(scope)`. Redesign to:

1. Extend `ExportScope` type: add `"jianying-draft"` value
2. After selecting "Export as CapCut Draft", dialog switches from "selection mode" to "form mode", expanding episode dropdown + draft directory input
3. CapCut export uses independent callback `onJianyingExport(episode, draftPath)`, doesn't reuse `onSelect`
4. Component needs to receive `episodes` prop (or read from store) to populate episode dropdown
5. State machine: selection mode → form mode → exporting (button disabled) → done (close dialog)

### API Layer

Add to `frontend/src/api.ts`:

```typescript
// Reuse existing requestExportToken, no new method needed
getJianyingDraftDownloadUrl(projectName: string, episode: number, draftPath: string, token: string): string
```

---

## Export ZIP Structure

```
{project_name}_episode_{N}_capcut_draft.zip
└── {project_name}_episode_{N}/
    ├── draft_content.json      # paths replaced with user's local paths
    ├── draft_meta_info.json    # auto-generated by pyjianyingdraft save()
    └── assets/
        ├── scene_E1S01.mp4
        ├── scene_E1S02.mp4
        └── ...
```

### User Flow

1. Workspace → "Export ZIP" → Select "Export as CapCut Draft"
2. Select episode + enter CapCut draft directory (localStorage auto-fills)
3. Click export, browser downloads ZIP
4. Unzip ZIP to the entered CapCut draft directory
5. Open CapCut, the project appears in draft list with videos + subtitles already arranged on timeline

---

## Path Post-Processing

After `save()`, replace the temp directory paths in `draft_content.json` via JSON parsing (not text-level `str.replace`, to avoid path quotes or special characters corrupting the JSON structure):

```python
import json

data = json.loads(json_path.read_text(encoding="utf-8"))
tmp_prefix = str(tmp_assets_dir)
target_prefix = f"{draft_path}/{draft_name}/assets"

def replace_paths(obj):
    """Recursively traverse JSON, replace all string values containing temp path"""
    if isinstance(obj, str) and tmp_prefix in obj:
        return obj.replace(tmp_prefix, target_prefix)
    if isinstance(obj, dict):
        return {k: replace_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replace_paths(v) for v in obj]
    return obj

data = replace_paths(data)
json_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
```

All asset path prefixes are under `assets/`, recursive replacement ensures complete JSON structure.

---

## Error Handling

| Scenario | Handling |
|----------|---------|
| Episode does not exist | 404 + "Episode N does not exist" |
| No completed videos for this episode | 422 + "Please generate videos first" |
| Video file missing (recorded in script but file not present) | Skip that segment, export only existing videos |
| pyjianyingdraft generation failure | 500 + log record, return friendly error |
| draft_path is empty, contains control characters, or exceeds 1024 characters | 422 + "Please provide a valid CapCut draft directory path" |

---

## Dependencies Changes

### Python

```toml
# pyproject.toml
"pyjianyingdraft>=0.2.6",
```

### System

```dockerfile
RUN apt-get update && apt-get install -y mediainfo && rm -rf /var/lib/apt/lists/*
```

---

## Testing Strategy

- **Unit tests**: mock video files (generate short videos with `imageio`), verify `draft_content.json` structure is correct, paths are replaced correctly, subtitle track exists (narration mode)
- **Route integration tests**: reuse existing `test_projects_archive_routes.py` pattern, test token issuance + ZIP download + error codes

---

## Risks and Mitigation

| Risk | Mitigation |
|------|-----------|
| CapCut format has no official documentation, updates may break compatibility | pyJianYingDraft community is active, usually catches up within weeks; lock version number |
| CapCut 6+ draft encryption | Only affects reading existing drafts, creating new drafts is not affected |
| Large ZIP size (dozens of video segments) | Browser native download supports progress display; hard links avoid actual copying |
| Temp directory accumulation | `BackgroundTask` immediately cleans up after response completes |
| pymediainfo requires system mediainfo | One apt-get line in Docker |

---

## Future Extensions (non-MVP)

- Export audio tracks (BGM, voice-over)
- Drama mode subtitles (multi-character dialogue)
- Transition effect mapping (`transition_to_next` → CapCut transitions)
- CapCut international edition support
- Desktop Helper tool (one-click import)
