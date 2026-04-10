# Media Cache and Video Thumbnail Optimization Design

## Background

The current ArcReel timeline re-downloads images/videos on scroll (virtual scroll unmount/remount) and on re-entering the page.
Root cause: the frontend `entityRevisions` is a session-level counter, and the backend `FileResponse` has no cache headers.

## Goals

1. **Zero-network-request caching**: When file content has not changed, returning after scroll and re-entering across sessions both load from browser disk cache
2. **Video bandwidth optimization**: Videos do not preload by default; the first-frame thumbnail is used as the poster, and the video only loads on click
3. **Version browse caching**: Version snapshot files use immutable cache; version switching triggers an instant refresh

## Design

### Part 1: Content-addressed Caching Based on File Fingerprints

#### Core Concept

Use the file `mtime` (modification time) as the URL cache-bust parameter, replacing the session-level counter.
File unchanged → mtime unchanged → URL unchanged → browser disk cache hit → zero network.

#### Backend

**1) Project API returns `asset_fingerprints`**

`GET /api/v1/projects/{name}` response adds a top-level field:

```json
{
  "project": { "..." },
  "scripts": { "..." },
  "asset_fingerprints": {
    "storyboards/scene_E1S01.png": 1710288000,
    "videos/scene_E1S01.mp4": 1710289000,
    "thumbnails/scene_E1S01.jpg": 1710289000,
    "characters/character-name.png": 1710287000
  }
}
```

Implementation: Scan `storyboards/`, `videos/`, `thumbnails/`, `characters/`, `clues/` under the project directory,
and generate a fingerprint map using `int(file.stat().st_mtime)`. ~50 files takes <1ms.

**2) SSE events carry `asset_fingerprints`**

In `_emit_generation_success_batch()`, calculate the mtime of affected files after generation completes:

```json
{
  "entity_type": "segment",
  "action": "storyboard_ready",
  "entity_id": "S1",
  "label": "Storyboard S1",
  "asset_fingerprints": {
    "storyboards/scene_S1.png": 1710289000
  }
}
```

Benefit: fingerprints arrive instantly with SSE events; the frontend can update URLs without additional API calls.

**3) File routes set immutable cache headers**

`GET /api/v1/files/{project}/{path}` response headers:

```
Has ?v= parameter or path contains versions/  →  Cache-Control: public, max-age=31536000, immutable
Other                                            →  No special cache headers
```

#### Frontend

**4) Add fingerprint state management**

Add to projects-store:

```typescript
assetFingerprints: Record<string, number>;
updateAssetFingerprints: (fps: Record<string, number>) => void;
getAssetFingerprint: (path: string) => number | null;
```

Set from the project API response on initial load; incrementally update when SSE events arrive.

**5) SSE handling optimization**

```typescript
onChanges(payload) {
  // Immediately update fingerprints
  for (const change of payload.changes) {
    if (change.asset_fingerprints) {
      updateAssetFingerprints(change.asset_fingerprints);
    }
  }

  // Only call refreshProject() on structural changes
  const needsRefresh = payload.changes.some(c =>
    ["created", "deleted"].includes(c.action) ||
    ["episode", "project", "overview"].includes(c.entity_type)
  );
  if (needsRefresh) void refreshProject();
}
```

First generation (`generated_assets` going from null → path) still requires refreshProject() to get script updates.

**6) URL construction uses fingerprint**

```typescript
const fp = useProjectsStore(s => s.getAssetFingerprint(assetPath));
const url = API.getFileUrl(projectName, assetPath, fp);
// → "/api/v1/files/MyProject/storyboards/scene_E1S01.png?v=1710288000"
```

#### Cache Hit Scenarios

| Scenario | Old Approach | New Approach |
|------|--------|--------|
| Virtual scroll unmount + remount | Re-download | Disk cache, zero network |
| Page refresh | ?v=N re-download | Same mtime → same URL → cache hit |
| Open new session | ?v=0 content may have changed | Same mtime → same URL → cache hit |
| File regenerated | revision+1 | mtime changes → URL changes → re-download |

### Part 2: Video First-Frame Thumbnail

#### Generation Timing

After video generation completes (in `execute_video_task`), the same worker extracts the first frame using ffmpeg:

```python
thumbnail_path = project_path / "thumbnails" / f"scene_{resource_id}.jpg"
thumbnail_path.parent.mkdir(exist_ok=True)

await asyncio.create_subprocess_exec(
    "ffmpeg", "-i", str(video_path),
    "-vframes", "1", "-q:v", "2",
    "-y", str(thumbnail_path)
)
```

#### Storage Structure

```
projects/{project_name}/
├── videos/scene_E1S01.mp4
├── thumbnails/scene_E1S01.jpg        ← new: video first frame
├── storyboards/scene_E1S01.png
└── versions/
    └── thumbnails/                    ← new: version video first frames
        └── E1S01_v1_20260312T103045.jpg
```

#### Data Model Extension

`generated_assets` adds a new `video_thumbnail` field:

```json
{
  "storyboard_image": "storyboards/scene_E1S01.png",
  "video_clip": "videos/scene_E1S01.mp4",
  "video_thumbnail": "thumbnails/scene_E1S01.jpg",
  "video_uri": "...",
  "status": "completed"
}
```

#### Frontend Usage

```tsx
<video
  poster={thumbnailUrl}
  preload="none"
  src={videoUrl}
  controls
  playsInline
/>
```

#### SSE Events Include Thumbnail Fingerprint

```json
{
  "action": "video_ready",
  "asset_fingerprints": {
    "videos/scene_E1S01.mp4": 1710289000,
    "thumbnails/scene_E1S01.jpg": 1710289000
  }
}
```

### Part 3: Cache Adaptation for Version Browsing and Switching

#### Version File Caching

Version files (under `versions/`) are immutable snapshots; the URL includes a version number + timestamp, making them naturally unique.
The backend sets `immutable` cache headers when it detects that the path contains `versions/`.

#### Version Video Thumbnails

In `VersionManager.add_version()`, also extract the first frame for video version files:
- Stored at `versions/thumbnails/{resource_id}_v{N}_{timestamp}.jpg`
- Video previews in VersionTimeMachine also use `preload="none"` + poster

#### Version Switch Refresh

`restore_version()` API returns new `asset_fingerprints`:

```python
return {
    "success": True,
    **result,
    "file_path": file_path,
    "asset_fingerprints": {
        file_path: int(current_file.stat().st_mtime)
    }
}
```

The frontend directly updates the store with the returned fingerprint, causing the main display URL to change instantly.

## Affected Files

### Backend

| File | Change |
|------|------|
| `server/routers/projects.py` | Project API returns `asset_fingerprints` |
| `server/routers/files.py` | Add `Cache-Control: immutable` response header |
| `server/routers/versions.py` | restore API returns `asset_fingerprints` |
| `server/services/generation_tasks.py` | SSE events carry `asset_fingerprints` |
| `server/services/project_events.py` | ProjectChange type extension (optional) |
| `lib/media_generator.py` | Extract first-frame thumbnail after video generation |
| `lib/version_manager.py` | Extract thumbnail for video when saving a version |
| `lib/project_manager.py` | Add `video_thumbnail` field to `create_generated_assets` |

### Frontend

| File | Change |
|------|------|
| `frontend/src/stores/projects-store.ts` | Add fingerprint state management |
| `frontend/src/hooks/useProjectEventsSSE.ts` | SSE handling uses fingerprint |
| `frontend/src/components/canvas/timeline/SegmentCard.tsx` | URL uses fingerprint; video uses poster + preload=none |
| `frontend/src/components/canvas/timeline/VersionTimeMachine.tsx` | Video uses poster + preload=none; restore uses fingerprint |
| `frontend/src/components/canvas/lorebook/CharacterCard.tsx` | URL uses fingerprint |
| `frontend/src/components/canvas/lorebook/ClueCard.tsx` | URL uses fingerprint |
| `frontend/src/components/canvas/OverviewCanvas.tsx` | URL uses fingerprint |
| `frontend/src/components/ui/AvatarStack.tsx` | URL uses fingerprint |
| `frontend/src/api.ts` | VersionInfo type extension |
| `frontend/src/types/workspace.ts` | ProjectChange type extension |
