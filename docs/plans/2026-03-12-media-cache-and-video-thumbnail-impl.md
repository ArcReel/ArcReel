# Media Cache and Video Thumbnail Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement zero-network-request caching based on file mtime fingerprints, and video first-frame thumbnails, eliminating repeat downloads on scroll/re-entering the page.

**Architecture:** The backend returns `asset_fingerprints` (path → mtime mapping) in the project API and SSE events. The frontend uses fingerprints instead of session-level revisions as the URL cache-bust parameter. Combined with `Cache-Control: immutable` headers, the browser disk cache achieves zero network requests. Videos use ffmpeg to extract a first-frame thumbnail as the poster, combined with `preload="none"` to avoid video preloading.

**Tech Stack:** Python/FastAPI, TypeScript/React, Zustand, ffmpeg, @tanstack/react-virtual

**Design doc:** `docs/plans/2026-03-12-media-cache-and-video-thumbnail-design.md`

---

### Task 1: Backend — compute_asset_fingerprints utility function

**Files:**
- Create: `lib/asset_fingerprints.py`
- Test: `tests/test_asset_fingerprints.py`

**Step 1: Write the failing test**

```python
# tests/test_asset_fingerprints.py
import time
from pathlib import Path

from lib.asset_fingerprints import compute_asset_fingerprints


class TestComputeAssetFingerprints:
    def test_empty_project(self, tmp_path):
        result = compute_asset_fingerprints(tmp_path)
        assert result == {}

    def test_scans_media_subdirs(self, tmp_path):
        (tmp_path / "storyboards").mkdir()
        sb = tmp_path / "storyboards" / "scene_E1S01.png"
        sb.write_bytes(b"img")

        (tmp_path / "videos").mkdir()
        vid = tmp_path / "videos" / "scene_E1S01.mp4"
        vid.write_bytes(b"vid")

        result = compute_asset_fingerprints(tmp_path)
        assert "storyboards/scene_E1S01.png" in result
        assert "videos/scene_E1S01.mp4" in result
        assert isinstance(result["storyboards/scene_E1S01.png"], int)

    def test_includes_thumbnails_and_characters_and_clues(self, tmp_path):
        for subdir, name in [
            ("thumbnails", "scene_E1S01.jpg"),
            ("characters", "Alice.png"),
            ("clues", "pendant.png"),
        ]:
            (tmp_path / subdir).mkdir()
            (tmp_path / subdir / name).write_bytes(b"x")

        result = compute_asset_fingerprints(tmp_path)
        assert "thumbnails/scene_E1S01.jpg" in result
        assert "characters/Alice.png" in result
        assert "clues/pendant.png" in result

    def test_includes_root_level_assets(self, tmp_path):
        (tmp_path / "style_reference.png").write_bytes(b"style")
        result = compute_asset_fingerprints(tmp_path)
        assert "style_reference.png" in result

    def test_ignores_non_media_files(self, tmp_path):
        (tmp_path / "project.json").write_text("{}")
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "ep01.json").write_text("{}")
        result = compute_asset_fingerprints(tmp_path)
        assert result == {}

    def test_fingerprint_changes_when_file_modified(self, tmp_path):
        (tmp_path / "storyboards").mkdir()
        f = tmp_path / "storyboards" / "scene_E1S01.png"
        f.write_bytes(b"v1")
        fp1 = compute_asset_fingerprints(tmp_path)["storyboards/scene_E1S01.png"]

        time.sleep(0.1)
        f.write_bytes(b"v2")
        fp2 = compute_asset_fingerprints(tmp_path)["storyboards/scene_E1S01.png"]
        assert fp2 != fp1
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_asset_fingerprints.py -v`
Expected: FAIL with "No module named 'lib.asset_fingerprints'"

**Step 3: Write minimal implementation**

```python
# lib/asset_fingerprints.py
"""Asset file fingerprint computation — support for mtime-based content-addressable caching"""

from pathlib import Path

# Media subdirectories to scan
_MEDIA_SUBDIRS = ("storyboards", "videos", "thumbnails", "characters", "clues")

# Known media files at root level (e.g., style reference images)
_ROOT_MEDIA_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".webp", ".mp4"))


def compute_asset_fingerprints(project_path: Path) -> dict[str, int]:
    """
    Scan all media files under the project directory and return a {relative_path: mtime_int} mapping.

    mtime is stat.st_mtime_ns (nanosecond integer), used as a URL cache-bust parameter.
    For ~50 files, takes <1ms (reads filesystem metadata only).
    """
    fingerprints: dict[str, int] = {}

    for subdir in _MEDIA_SUBDIRS:
        dir_path = project_path / subdir
        if not dir_path.is_dir():
            continue
        for f in dir_path.iterdir():
            if f.is_file():
                fingerprints[f"{subdir}/{f.name}"] = int(f.stat().st_mtime)

    # Media files at root level (e.g., style_reference.png)
    for f in project_path.iterdir():
        if f.is_file() and f.suffix.lower() in _ROOT_MEDIA_SUFFIXES:
            fingerprints[f.name] = int(f.stat().st_mtime)

    return fingerprints
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_asset_fingerprints.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add lib/asset_fingerprints.py tests/test_asset_fingerprints.py
git commit -m "feat: add compute_asset_fingerprints utility for content-addressable caching"
```

---

### Task 2: Backend — project API returns asset_fingerprints

**Files:**
- Modify: `server/routers/projects.py:298-306` (get_project return value)
- Test: `tests/test_projects_router.py` (append tests)

**Step 1: Write the failing test**

In `tests/test_projects_router.py`, append a test. First locate the existing fixtures and test patterns, then add:

```python
def test_get_project_includes_asset_fingerprints(self, monkeypatch, tmp_path):
    """Project API should return the asset_fingerprints field"""
    client, pm = _setup_project_client(monkeypatch, tmp_path)
    # Create media files
    project_path = pm.get_project_path("demo")
    (project_path / "storyboards").mkdir(exist_ok=True)
    (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"img")

    with client:
        resp = client.get("/api/v1/projects/demo")
        assert resp.status_code == 200
        data = resp.json()
        assert "asset_fingerprints" in data
        assert "storyboards/scene_E1S01.png" in data["asset_fingerprints"]
        assert isinstance(data["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_projects_router.py -k "asset_fingerprints" -v`
Expected: FAIL with AssertionError (asset_fingerprints not in response)

**Step 3: Write minimal implementation**

Modify `server/routers/projects.py:298-306`, add fingerprint computation before the return statement:

```python
# Add before the return statement in get_project
from lib.asset_fingerprints import compute_asset_fingerprints

# ... existing code ...

        # Compute media file fingerprints (for frontend content-addressable caching)
        project_path = manager.get_project_path(name)
        fingerprints = compute_asset_fingerprints(project_path)

        return {
            "project": project,
            "scripts": scripts,
            "asset_fingerprints": fingerprints,
        }
```

Note: place the import at the top of the file.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_projects_router.py -k "asset_fingerprints" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add server/routers/projects.py tests/test_projects_router.py
git commit -m "feat: project API returns asset_fingerprints for content-addressable caching"
```

---

### Task 3: Backend — add immutable cache headers to file routes

**Files:**
- Modify: `server/routers/files.py:46-64` (serve_project_file)
- Test: `tests/test_files_router.py` (append tests)

**Step 1: Write the failing test**

In `tests/test_files_router.py`, append:

```python
def test_cache_control_immutable_with_version_param(self, tmp_path, monkeypatch):
    """Should return immutable cache headers when ?v= parameter is present"""
    client, pm = _client(monkeypatch, tmp_path)
    project_path = pm.get_project_path("demo")
    (project_path / "storyboards").mkdir(exist_ok=True)
    (project_path / "storyboards" / "test.png").write_bytes(b"img")

    with client:
        resp = client.get("/api/v1/files/demo/storyboards/test.png?v=1710288000")
        assert resp.status_code == 200
        assert "immutable" in resp.headers.get("cache-control", "")
        assert "max-age=31536000" in resp.headers.get("cache-control", "")

def test_cache_control_immutable_for_version_files(self, tmp_path, monkeypatch):
    """Files under versions/ path should return immutable cache headers"""
    client, pm = _client(monkeypatch, tmp_path)
    project_path = pm.get_project_path("demo")
    (project_path / "versions" / "storyboards").mkdir(parents=True)
    (project_path / "versions" / "storyboards" / "E1S01_v1.png").write_bytes(b"img")

    with client:
        resp = client.get("/api/v1/files/demo/versions/storyboards/E1S01_v1.png")
        assert resp.status_code == 200
        assert "immutable" in resp.headers.get("cache-control", "")

def test_no_cache_control_without_version(self, tmp_path, monkeypatch):
    """Should not have immutable headers when no ?v= parameter and not a versions/ path"""
    client, pm = _client(monkeypatch, tmp_path)
    project_path = pm.get_project_path("demo")
    (project_path / "storyboards").mkdir(exist_ok=True)
    (project_path / "storyboards" / "test.png").write_bytes(b"img")

    with client:
        resp = client.get("/api/v1/files/demo/storyboards/test.png")
        assert resp.status_code == 200
        assert "immutable" not in resp.headers.get("cache-control", "")
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_files_router.py -k "cache_control" -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Modify `server/routers/files.py:46-64`, change `serve_project_file` to:

```python
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

# ...

@router.get("/files/{project_name}/{path:path}")
async def serve_project_file(project_name: str, path: str, request: Request):
    """Serve static files (images/videos) within a project"""
    try:
        project_dir = get_project_manager().get_project_path(project_name)
        file_path = project_dir / path

        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {path}")

        # Security check: ensure path is within the project directory
        try:
            file_path.resolve().relative_to(project_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="Access to files outside the project directory is forbidden")

        # Content-addressable caching: set immutable when ?v= param or versions/ path
        headers = {}
        if request.query_params.get("v") or "versions/" in path:
            headers["Cache-Control"] = "public, max-age=31536000, immutable"

        return FileResponse(file_path, headers=headers)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_files_router.py -k "cache_control" -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add server/routers/files.py tests/test_files_router.py
git commit -m "feat: add immutable cache headers for versioned file responses"
```

---

### Task 4: Backend — SSE events carry asset_fingerprints

**Files:**
- Modify: `server/services/generation_tasks.py:198-268` (_emit_generation_success_batch)
- Test: `tests/test_generation_tasks_service.py` (append tests)

**Step 1: Write the failing test**

In `tests/test_generation_tasks_service.py`, append. First understand how `_emit_generation_success_batch` is tested. The function calls `emit_project_change_batch`, which can be monkeypatched to capture arguments:

```python
def test_emit_success_batch_includes_fingerprints(self, monkeypatch, tmp_path):
    """Generation success events should carry asset_fingerprints"""
    captured = []
    monkeypatch.setattr(
        generation_tasks, "emit_project_change_batch",
        lambda project_name, changes, source: captured.append(changes)
    )

    # Create project directory and media files
    project_path = tmp_path / "demo"
    project_path.mkdir()
    (project_path / "storyboards").mkdir()
    sb = project_path / "storyboards" / "scene_E1S01.png"
    sb.write_bytes(b"img")

    fake_pm = _FakePM(project_path)
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

    generation_tasks._emit_generation_success_batch(
        task_type="storyboard",
        project_name="demo",
        resource_id="E1S01",
        payload={"script_file": "ep01.json"},
    )

    assert len(captured) == 1
    change = captured[0][0]
    assert "asset_fingerprints" in change
    assert "storyboards/scene_E1S01.png" in change["asset_fingerprints"]
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_generation_tasks_service.py -k "fingerprints" -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Modify `server/services/generation_tasks.py:198-268`, compute fingerprints of affected files in `_emit_generation_success_batch`:

```python
def _emit_generation_success_batch(
    *,
    task_type: str,
    project_name: str,
    resource_id: str,
    payload: Dict[str, Any],
) -> None:
    script_file = str(payload.get("script_file") or "") or None
    episode = _resolve_script_episode(project_name, script_file)

    # Compute fingerprints of affected files
    asset_fingerprints = _compute_affected_fingerprints(
        project_name, task_type, resource_id
    )

    if task_type == "storyboard":
        changes = [
            {
                "entity_type": "segment",
                "action": "storyboard_ready",
                "entity_id": resource_id,
                "label": f"Storyboard {resource_id}",
                "script_file": script_file,
                "episode": episode,
                "focus": None,
                "important": True,
                "asset_fingerprints": asset_fingerprints,
            }
        ]
    elif task_type == "video":
        changes = [
            {
                "entity_type": "segment",
                "action": "video_ready",
                "entity_id": resource_id,
                "label": f"Storyboard {resource_id}",
                "script_file": script_file,
                "episode": episode,
                "focus": None,
                "important": True,
                "asset_fingerprints": asset_fingerprints,
            }
        ]
    elif task_type == "character":
        changes = [
            {
                "entity_type": "character",
                "action": "updated",
                "entity_id": resource_id,
                "label": f"Character {resource_id} design",
                "focus": None,
                "important": True,
                "asset_fingerprints": asset_fingerprints,
            }
        ]
    elif task_type == "clue":
        changes = [
            {
                "entity_type": "clue",
                "action": "updated",
                "entity_id": resource_id,
                "label": f"Clue {resource_id} design",
                "focus": None,
                "important": True,
                "asset_fingerprints": asset_fingerprints,
            }
        ]
    else:
        return

    try:
        emit_project_change_batch(project_name, changes, source="worker")
    except Exception:
        logger.exception(
            "Failed to emit generation success project event: project=%s task_type=%s resource_id=%s",
            project_name,
            task_type,
            resource_id,
        )


def _compute_affected_fingerprints(
    project_name: str, task_type: str, resource_id: str
) -> Dict[str, int]:
    """Compute mtime fingerprints of affected files"""
    try:
        project_path = get_project_manager().get_project_path(project_name)
    except Exception:
        return {}

    paths: list[tuple[str, Path]] = []

    if task_type == "storyboard":
        paths.append((
            f"storyboards/scene_{resource_id}.png",
            project_path / "storyboards" / f"scene_{resource_id}.png",
        ))
    elif task_type == "video":
        paths.append((
            f"videos/scene_{resource_id}.mp4",
            project_path / "videos" / f"scene_{resource_id}.mp4",
        ))
        paths.append((
            f"thumbnails/scene_{resource_id}.jpg",
            project_path / "thumbnails" / f"scene_{resource_id}.jpg",
        ))
    elif task_type == "character":
        paths.append((
            f"characters/{resource_id}.png",
            project_path / "characters" / f"{resource_id}.png",
        ))
    elif task_type == "clue":
        paths.append((
            f"clues/{resource_id}.png",
            project_path / "clues" / f"{resource_id}.png",
        ))

    result: Dict[str, int] = {}
    for rel, abs_path in paths:
        if abs_path.exists():
            result[rel] = int(abs_path.stat().st_mtime)

    return result
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_generation_tasks_service.py -k "fingerprints" -v`
Expected: PASS

**Step 5: Run all existing tests to check no regression**

Run: `python -m pytest tests/test_generation_tasks_service.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add server/services/generation_tasks.py tests/test_generation_tasks_service.py
git commit -m "feat: SSE change events carry asset_fingerprints for instant cache invalidation"
```

---

### Task 5: Backend — restore version API returns asset_fingerprints

**Files:**
- Modify: `server/routers/versions.py:154-158` (restore_version return value)
- Test: `tests/test_versions_router.py` (append tests)

**Step 1: Write the failing test**

In `tests/test_versions_router.py`, `_FakeVM.restore_version` needs to cooperate. The test only verifies that the return value contains `asset_fingerprints`:

```python
def test_restore_returns_asset_fingerprints(self, monkeypatch, tmp_path):
    """Version restore should return fingerprints of affected files"""
    fake_pm = _FakePM()
    # Override get_project_path to return tmp_path
    fake_pm.get_project_path = lambda name: tmp_path

    # Create the target file (the current file after restore)
    (tmp_path / "storyboards").mkdir()
    (tmp_path / "storyboards" / "scene_E1S01.png").write_bytes(b"restored")

    monkeypatch.setattr(versions, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(versions, "get_version_manager", lambda name: _FakeVM())

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: {"sub": "testuser"}
    app.include_router(versions.router, prefix="/api/v1")
    client = TestClient(app)

    with client:
        resp = client.post(
            "/api/v1/projects/demo/versions/storyboards/E1S01/restore/1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "asset_fingerprints" in data
        assert "storyboards/scene_E1S01.png" in data["asset_fingerprints"]
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_versions_router.py -k "fingerprints" -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Modify `server/routers/versions.py:154-158`, compute fingerprint before the return statement:

```python
        # Compute fingerprint of the restored file
        asset_fingerprints = {}
        if current_file.exists():
            asset_fingerprints[file_path] = int(current_file.stat().st_mtime)

        return {
            "success": True,
            **result,
            "file_path": file_path,
            "asset_fingerprints": asset_fingerprints,
        }
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_versions_router.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add server/routers/versions.py tests/test_versions_router.py
git commit -m "feat: restore version API returns asset_fingerprints"
```

---

### Task 6: Backend — video first-frame thumbnail extraction

**Files:**
- Create: `lib/thumbnail.py`
- Test: `tests/test_thumbnail.py`
- Modify: `server/services/generation_tasks.py` (execute_video_task invocation)

**Step 1: Write the failing test**

```python
# tests/test_thumbnail.py
import asyncio
import shutil
from pathlib import Path

import pytest

from lib.thumbnail import extract_video_thumbnail


class TestExtractVideoThumbnail:
    @pytest.fixture(autouse=True)
    def check_ffmpeg(self):
        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not available")

    async def test_extracts_thumbnail_from_video(self, tmp_path):
        # Generate a minimal test video using ffmpeg
        video_path = tmp_path / "test.mp4"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
            "-c:v", "libx264", "-t", "1", "-y", str(video_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        assert video_path.exists()

        thumbnail_path = tmp_path / "thumb.jpg"
        result = await extract_video_thumbnail(video_path, thumbnail_path)
        assert result == thumbnail_path
        assert thumbnail_path.exists()
        assert thumbnail_path.stat().st_size > 0

    async def test_creates_parent_directory(self, tmp_path):
        video_path = tmp_path / "test.mp4"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
            "-c:v", "libx264", "-t", "1", "-y", str(video_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        thumbnail_path = tmp_path / "sub" / "dir" / "thumb.jpg"
        result = await extract_video_thumbnail(video_path, thumbnail_path)
        assert result == thumbnail_path
        assert thumbnail_path.exists()

    async def test_returns_none_for_missing_video(self, tmp_path):
        result = await extract_video_thumbnail(
            tmp_path / "missing.mp4", tmp_path / "thumb.jpg"
        )
        assert result is None

    async def test_returns_none_when_ffmpeg_fails(self, tmp_path):
        bad_video = tmp_path / "bad.mp4"
        bad_video.write_text("not a video")
        result = await extract_video_thumbnail(bad_video, tmp_path / "thumb.jpg")
        assert result is None
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_thumbnail.py -v`
Expected: FAIL with "No module named 'lib.thumbnail'"

**Step 3: Write minimal implementation**

```python
# lib/thumbnail.py
"""Video first-frame thumbnail extraction"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


async def extract_video_thumbnail(
    video_path: Path,
    thumbnail_path: Path,
) -> Optional[Path]:
    """
    Extract the first frame of a video as a JPEG thumbnail using ffmpeg.

    Args:
        video_path: Path to the video file
        thumbnail_path: Output thumbnail path

    Returns:
        Thumbnail path (success) or None (failure)
    """
    if not video_path.exists():
        return None

    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            "-y", str(thumbnail_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode != 0 or not thumbnail_path.exists():
            return None

        return thumbnail_path
    except Exception:
        logger.warning("Failed to extract video thumbnail: %s", video_path, exc_info=True)
        return None
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_thumbnail.py -v`
Expected: All PASS (or skip if no ffmpeg)

**Step 5: Commit**

```bash
git add lib/thumbnail.py tests/test_thumbnail.py
git commit -m "feat: add video thumbnail extraction utility using ffmpeg"
```

---

### Task 7: Backend — auto-extract thumbnail after video generation

**Files:**
- Modify: `server/services/generation_tasks.py` (execute_video_task)
- Modify: `lib/project_manager.py:528-543` (add video_thumbnail to create_generated_assets)
- Test: append to `tests/test_generation_tasks_service.py`

**Step 1: Write the failing test**

```python
async def test_execute_video_task_generates_thumbnail(self, monkeypatch, tmp_path):
    """Should auto-extract first-frame thumbnail after video generation"""
    project_path = tmp_path / "demo"
    project_path.mkdir()
    (project_path / "storyboards").mkdir()
    (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"img")
    (project_path / "videos").mkdir()

    # ... setup fake PM, fake generator, monkeypatches ...
    # Key: verify update_scene_asset is called with video_thumbnail
    # Verify thumbnails/scene_E1S01.jpg exists

    # Note: this test may need to mock extract_video_thumbnail
    # because ffmpeg may not be available
```

In practice, monkeypatch `extract_video_thumbnail` to a mock that returns the expected path, then verify:
1. `extract_video_thumbnail` is called
2. `update_scene_asset` is called to set `video_thumbnail`

**Step 2: Write minimal implementation**

Modify the `execute_video_task` in `server/services/generation_tasks.py`, add after video download:

```python
    # Add after video download completes, before update_scene_asset:
    from lib.thumbnail import extract_video_thumbnail

    # Extract video first frame as thumbnail
    video_file = project_path / f"videos/scene_{resource_id}.mp4"
    thumbnail_file = project_path / f"thumbnails/scene_{resource_id}.jpg"
    await extract_video_thumbnail(video_file, thumbnail_file)

    # Update video_thumbnail asset path
    if thumbnail_file.exists():
        get_project_manager().update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="video_thumbnail",
            asset_path=f"thumbnails/scene_{resource_id}.jpg",
        )
```

Modify `lib/project_manager.py:528-543`, add `video_thumbnail` to `create_generated_assets`:

```python
    @staticmethod
    def create_generated_assets(content_mode: str = "narration") -> Dict:
        return {
            "storyboard_image": None,
            "video_clip": None,
            "video_thumbnail": None,   # new
            "video_uri": None,
            "status": "pending",
        }
```

**Step 3: Run tests**

Run: `python -m pytest tests/test_generation_tasks_service.py -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add server/services/generation_tasks.py lib/project_manager.py tests/test_generation_tasks_service.py
git commit -m "feat: auto-extract video thumbnail after generation, add video_thumbnail to generated_assets"
```

---

### Task 8: Frontend — ProjectChange type and projects-store extension

**Files:**
- Modify: `frontend/src/types/workspace.ts` (add asset_fingerprints to ProjectChange)
- Modify: `frontend/src/stores/projects-store.ts` (add fingerprint state management)
- Test: `frontend/src/stores/stores.test.ts` (append tests)

**Step 1: Write the failing test**

In `frontend/src/stores/stores.test.ts`, append:

```typescript
describe("ProjectsStore fingerprints", () => {
  it("should store and retrieve asset fingerprints", () => {
    const { updateAssetFingerprints, getAssetFingerprint } =
      useProjectsStore.getState();
    updateAssetFingerprints({ "storyboards/scene_E1S01.png": 1710288000 });
    expect(getAssetFingerprint("storyboards/scene_E1S01.png")).toBe(1710288000);
  });

  it("should merge fingerprints on update", () => {
    const { updateAssetFingerprints, getAssetFingerprint } =
      useProjectsStore.getState();
    updateAssetFingerprints({ "a.png": 100 });
    updateAssetFingerprints({ "b.png": 200 });
    expect(getAssetFingerprint("a.png")).toBe(100);
    expect(getAssetFingerprint("b.png")).toBe(200);
  });

  it("should return null for unknown paths", () => {
    expect(useProjectsStore.getState().getAssetFingerprint("unknown")).toBeNull();
  });

  it("should set fingerprints from project API response", () => {
    useProjectsStore.getState().setCurrentProject("demo", {} as any, {}, {
      "storyboards/x.png": 999,
    });
    expect(useProjectsStore.getState().getAssetFingerprint("storyboards/x.png")).toBe(999);
  });
});
```

**Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- --run stores.test`
Expected: FAIL

**Step 3: Write minimal implementation**

Modify `frontend/src/types/workspace.ts:10-24`, add optional field to ProjectChange:

```typescript
export interface ProjectChange {
  entity_type: "project" | "character" | "clue" | "segment" | "episode" | "overview";
  action: "created" | "updated" | "deleted" | "storyboard_ready" | "video_ready";
  entity_id: string;
  label: string;
  script_file?: string;
  episode?: number;
  focus?: ProjectChangeFocus | null;
  important: boolean;
  asset_fingerprints?: Record<string, number>;  // new
}
```

Modify `frontend/src/stores/projects-store.ts`:

```typescript
import { create } from "zustand";
import type { ProjectData, ProjectSummary, EpisodeScript } from "@/types";

interface ProjectsState {
  // ... existing fields ...

  // Asset fingerprints (path → mtime)
  assetFingerprints: Record<string, number>;

  // ... existing actions ...
  setCurrentProject: (
    name: string | null,
    data: ProjectData | null,
    scripts?: Record<string, EpisodeScript>,
    fingerprints?: Record<string, number>,
  ) => void;
  updateAssetFingerprints: (fps: Record<string, number>) => void;
  getAssetFingerprint: (path: string) => number | null;
}

export const useProjectsStore = create<ProjectsState>((set, get) => ({
  // ... existing state ...

  assetFingerprints: {},

  // ... existing actions ...
  setCurrentProject: (name, data, scripts = {}, fingerprints) =>
    set((s) => ({
      currentProjectName: name,
      currentProjectData: data,
      currentScripts: scripts,
      assetFingerprints: fingerprints ?? s.assetFingerprints,
    })),

  updateAssetFingerprints: (fps) =>
    set((s) => ({
      assetFingerprints: { ...s.assetFingerprints, ...fps },
    })),

  getAssetFingerprint: (path) => get().assetFingerprints[path] ?? null,
}));
```

**Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm test -- --run stores.test`
Expected: PASS

**Step 5: Commit**

```bash
git add frontend/src/types/workspace.ts frontend/src/stores/projects-store.ts frontend/src/stores/stores.test.ts
git commit -m "feat(frontend): add asset fingerprint state to projects-store and ProjectChange type"
```

---

### Task 9: Frontend — SSE handler uses fingerprints

**Files:**
- Modify: `frontend/src/hooks/useProjectEventsSSE.ts:212-258` (onChanges)
- Modify: all locations calling `setCurrentProject` (pass fingerprints)
- Test: `frontend/src/hooks/useProjectEventsSSE.test.tsx` (append)

**Step 1: Write the failing test**

In `frontend/src/hooks/useProjectEventsSSE.test.tsx`, append a test verifying that `asset_fingerprints` from SSE events are extracted and updated in the store.

**Step 2: Write minimal implementation**

Modify the `onChanges` handler in `frontend/src/hooks/useProjectEventsSSE.ts:212-258`:

```typescript
onChanges(payload: ProjectChangeBatchPayload) {
  if (disposed) return;
  lastFingerprintRef.current = payload.fingerprint;
  setAssistantToolActivitySuppressed(true);

  // Extract and update asset fingerprints (zero delay)
  const mergedFingerprints: Record<string, number> = {};
  for (const change of payload.changes) {
    if (change.asset_fingerprints) {
      Object.assign(mergedFingerprints, change.asset_fingerprints);
    }
  }
  if (Object.keys(mergedFingerprints).length > 0) {
    useProjectsStore.getState().updateAssetFingerprints(mergedFingerprints);
  }

  // Retain entityRevisions for triggering non-media-related re-renders
  const invalidationKeys = payload.changes.map((change) =>
    buildEntityRevisionKey(change.entity_type, change.entity_id),
  );
  invalidateEntities(invalidationKeys);

  // ... rest of notification/toast logic unchanged ...

  void refreshProject();
},
```

Also modify all places that call `setCurrentProject` to pass in fingerprints from the API response. Search all `setCurrentProject` call sites (generally in `refreshProject` callbacks). Modify the API call locations to extract `asset_fingerprints` from the response and pass it to the fourth argument of `setCurrentProject`.

Key call sites (search for `setCurrentProject`):
- `refreshProject` in `useProjectEventsSSE.ts`
- `refreshProject` in `useProjectAssetSync.ts`
- `refreshProject` in `StudioCanvasRouter.tsx`
- `refreshProject` in `OverviewCanvas.tsx`

Each location needs to pass `res.asset_fingerprints` as the fourth argument to `setCurrentProject`.

**Step 3: Run tests**

Run: `cd frontend && pnpm test`
Expected: All PASS

**Step 4: Commit**

```bash
git add frontend/src/hooks/useProjectEventsSSE.ts frontend/src/hooks/useProjectAssetSync.ts \
  frontend/src/components/canvas/StudioCanvasRouter.tsx \
  frontend/src/components/canvas/OverviewCanvas.tsx
git commit -m "feat(frontend): SSE handler extracts asset_fingerprints, propagate to projects-store"
```

---

### Task 10: Frontend — switch component URL construction to fingerprint

**Files:**
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx:483-491`
- Modify: `frontend/src/components/canvas/lorebook/CharacterCard.tsx:128-134`
- Modify: `frontend/src/components/canvas/OverviewCanvas.tsx:141-143`
- Modify: `frontend/src/components/ui/AvatarStack.tsx:66,125`
- Test: existing tests for each component (ensure no regressions)

**Step 1: Write implementation**

Core pattern change — using SegmentCard as an example:

```typescript
// Old code (SegmentCard.tsx:483-491)
const entityRevisionKey = buildEntityRevisionKey("segment", segmentId);
const mediaRevision = useAppStore((s) => s.getEntityRevision(entityRevisionKey));
const storyboardUrl = assets?.storyboard_image
  ? API.getFileUrl(projectName, assets.storyboard_image, mediaRevision)
  : null;
const videoUrl = assets?.video_clip
  ? API.getFileUrl(projectName, assets.video_clip, mediaRevision)
  : null;

// New code
const storyboardFp = useProjectsStore(
  (s) => assets?.storyboard_image ? s.getAssetFingerprint(assets.storyboard_image) : null
);
const videoFp = useProjectsStore(
  (s) => assets?.video_clip ? s.getAssetFingerprint(assets.video_clip) : null
);
const thumbnailFp = useProjectsStore(
  (s) => assets?.video_thumbnail ? s.getAssetFingerprint(assets.video_thumbnail) : null
);
const storyboardUrl = assets?.storyboard_image
  ? API.getFileUrl(projectName, assets.storyboard_image, storyboardFp)
  : null;
const videoUrl = assets?.video_clip
  ? API.getFileUrl(projectName, assets.video_clip, videoFp)
  : null;
const thumbnailUrl = assets?.video_thumbnail
  ? API.getFileUrl(projectName, assets.video_thumbnail, thumbnailFp)
  : null;
```

Apply the same pattern change to CharacterCard, OverviewCanvas, AvatarStack:
- Replace `useAppStore(s => s.getEntityRevision(key))` with `useProjectsStore(s => s.getAssetFingerprint(path))`

**Step 2: Update VideoPlayer to use poster + preload="none"**

```typescript
// SegmentCard.tsx — VideoPlayer component refactor
function VideoPlayer({ src, poster }: { src: string; poster?: string | null }) {
  return (
    <video
      src={src}
      poster={poster ?? undefined}
      className="h-full w-full bg-black object-contain"
      controls
      playsInline
      preload={poster ? "none" : "metadata"}
    />
  );
}
```

Pass poster when calling: `<VideoPlayer src={videoUrl} poster={thumbnailUrl} />`

**Step 3: Run all frontend tests**

Run: `cd frontend && pnpm test`
Expected: All PASS (may need to update some mocks)

**Step 4: Commit**

```bash
git add frontend/src/components/canvas/timeline/SegmentCard.tsx \
  frontend/src/components/canvas/lorebook/CharacterCard.tsx \
  frontend/src/components/canvas/OverviewCanvas.tsx \
  frontend/src/components/ui/AvatarStack.tsx
git commit -m "feat(frontend): switch media URL cache-busting from session revision to asset fingerprints"
```

---

### Task 11: Frontend — VersionTimeMachine adaptation

**Files:**
- Modify: `frontend/src/components/canvas/timeline/VersionTimeMachine.tsx`
- Modify: `frontend/src/api.ts` (restoreVersion return type)
- Test: `frontend/src/components/canvas/timeline/VersionTimeMachine.test.tsx`

**Step 1: Write implementation**

Modify `handleRestore` to use the returned fingerprints:

```typescript
async function handleRestore(version: number) {
  setRestoringVersion(version);
  try {
    const result = await API.restoreVersion(projectName, resourceType, resourceId, version);
    // Update store with returned fingerprint (replaces invalidateEntities)
    if (result.asset_fingerprints) {
      useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
    }
    await onRestore?.(version);
    await loadVersions();
    setSelectedVersion(version);
    useAppStore.getState().pushToast(`Switched to v${version}`, "success");
  } catch (err) {
    useAppStore.getState().pushToast(`Failed to switch version: ${(err as Error).message}`, "error");
  } finally {
    setRestoringVersion(null);
  }
}
```

Modify video preview to also use `preload="none"`:

```tsx
<video
  src={selectedInfo.file_url}
  className="mb-2 w-full rounded-lg border border-gray-800 bg-black object-contain"
  controls
  playsInline
  preload="none"
/>
```

Modify the return type of `restoreVersion` in `frontend/src/api.ts`, adding `asset_fingerprints`:

```typescript
static async restoreVersion(
  projectName: string,
  resourceType: string,
  resourceId: string,
  version: number
): Promise<{ success: boolean; file_path: string; asset_fingerprints?: Record<string, number> }> {
```

**Step 2: Run tests**

Run: `cd frontend && pnpm test`
Expected: All PASS

**Step 3: Commit**

```bash
git add frontend/src/components/canvas/timeline/VersionTimeMachine.tsx frontend/src/api.ts
git commit -m "feat(frontend): VersionTimeMachine uses fingerprints from restore API"
```

---

### Task 12: Full-stack validation and cleanup

**Files:**
- All modified files

**Step 1: Run all backend tests**

Run: `python -m pytest -v`
Expected: All PASS

**Step 2: Run all frontend tests**

Run: `cd frontend && pnpm check`
Expected: typecheck + test all PASS

**Step 3: Build frontend**

Run: `cd frontend && pnpm build`
Expected: Build succeeds

**Step 4: Commit final cleanup if needed**

```bash
git commit -m "chore: final cleanup for media cache and video thumbnail feature"
```
