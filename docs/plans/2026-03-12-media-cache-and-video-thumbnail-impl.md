# 媒体缓存与视频缩略图 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现基于文件 mtime 指纹的零网络请求缓存，以及视频首帧缩略图，消除滚动/重进页面时的重复下载。

**Architecture:** 后端在项目 API 和 SSE 事件中返回 `asset_fingerprints`（path → mtime 映射），前端用 fingerprint 替代 session 级 revision 作为 URL cache-bust 参数。配合 `Cache-Control: immutable` 头，浏览器 disk cache 实现零网络请求。视频使用 ffmpeg 提取首帧缩略图作为 poster，配合 `preload="none"` 避免视频预加载。

**Tech Stack:** Python/FastAPI, TypeScript/React, Zustand, ffmpeg, @tanstack/react-virtual

**Design doc:** `docs/plans/2026-03-12-media-cache-and-video-thumbnail-design.md`

---

### Task 1: 后端 — compute_asset_fingerprints 工具函数

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
            ("clues", "玉佩.png"),
        ]:
            (tmp_path / subdir).mkdir()
            (tmp_path / subdir / name).write_bytes(b"x")

        result = compute_asset_fingerprints(tmp_path)
        assert "thumbnails/scene_E1S01.jpg" in result
        assert "characters/Alice.png" in result
        assert "clues/玉佩.png" in result

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
"""资产文件指纹计算 — 基于 mtime 的内容寻址缓存支持"""

from pathlib import Path

# 扫描的媒体子目录
_MEDIA_SUBDIRS = ("storyboards", "videos", "thumbnails", "characters", "clues")

# 根目录下的已知媒体文件（如风格参考图）
_ROOT_MEDIA_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".webp", ".mp4"))


def compute_asset_fingerprints(project_path: Path) -> dict[str, int]:
    """
    扫描项目目录下所有媒体文件，返回 {相对路径: mtime_int} 映射。

    mtime 为 int(stat.st_mtime)，精度到秒，用作 URL cache-bust 参数。
    对约 50 个文件，耗时 <1ms（仅读文件系统元数据）。
    """
    fingerprints: dict[str, int] = {}

    for subdir in _MEDIA_SUBDIRS:
        dir_path = project_path / subdir
        if not dir_path.is_dir():
            continue
        for f in dir_path.iterdir():
            if f.is_file():
                fingerprints[f"{subdir}/{f.name}"] = int(f.stat().st_mtime)

    # 根目录下的媒体文件（如 style_reference.png）
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

### Task 2: 后端 — 项目 API 返回 asset_fingerprints

**Files:**
- Modify: `server/routers/projects.py:298-306` (get_project 返回值)
- Test: `tests/test_projects_router.py` (追加测试)

**Step 1: Write the failing test**

在 `tests/test_projects_router.py` 中追加测试。先找到现有的 fixture 和测试模式，然后添加：

```python
def test_get_project_includes_asset_fingerprints(self, monkeypatch, tmp_path):
    """项目 API 应返回 asset_fingerprints 字段"""
    client, pm = _setup_project_client(monkeypatch, tmp_path)
    # 创建媒体文件
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
Expected: FAIL with AssertionError (asset_fingerprints 不在响应中)

**Step 3: Write minimal implementation**

修改 `server/routers/projects.py:298-306`，在 return 之前添加 fingerprint 计算：

```python
# 在 get_project 函数的 return 语句之前添加
from lib.asset_fingerprints import compute_asset_fingerprints

# ... existing code ...

        # 计算媒体文件指纹（用于前端内容寻址缓存）
        project_path = manager.get_project_path(name)
        fingerprints = compute_asset_fingerprints(project_path)

        return {
            "project": project,
            "scripts": scripts,
            "asset_fingerprints": fingerprints,
        }
```

注意：import 放在文件顶部。

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_projects_router.py -k "asset_fingerprints" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add server/routers/projects.py tests/test_projects_router.py
git commit -m "feat: project API returns asset_fingerprints for content-addressable caching"
```

---

### Task 3: 后端 — 文件路由添加 immutable 缓存头

**Files:**
- Modify: `server/routers/files.py:46-64` (serve_project_file)
- Test: `tests/test_files_router.py` (追加测试)

**Step 1: Write the failing test**

在 `tests/test_files_router.py` 追加：

```python
def test_cache_control_immutable_with_version_param(self, tmp_path, monkeypatch):
    """带 ?v= 参数时应返回 immutable 缓存头"""
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
    """versions/ 路径下的文件应返回 immutable 缓存头"""
    client, pm = _client(monkeypatch, tmp_path)
    project_path = pm.get_project_path("demo")
    (project_path / "versions" / "storyboards").mkdir(parents=True)
    (project_path / "versions" / "storyboards" / "E1S01_v1.png").write_bytes(b"img")

    with client:
        resp = client.get("/api/v1/files/demo/versions/storyboards/E1S01_v1.png")
        assert resp.status_code == 200
        assert "immutable" in resp.headers.get("cache-control", "")

def test_no_cache_control_without_version(self, tmp_path, monkeypatch):
    """无 ?v= 参数且非 versions 路径时不应有 immutable 头"""
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

修改 `server/routers/files.py:46-64`，将 `serve_project_file` 改为：

```python
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

# ...

@router.get("/files/{project_name}/{path:path}")
async def serve_project_file(project_name: str, path: str, request: Request):
    """服务项目内的静态文件（图片/视频）"""
    try:
        project_dir = get_project_manager().get_project_path(project_name)
        file_path = project_dir / path

        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")

        # 安全检查：确保路径在项目目录内
        try:
            file_path.resolve().relative_to(project_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="禁止访问项目目录外的文件")

        # 内容寻址缓存：带 ?v= 参数或 versions/ 路径时设 immutable
        headers = {}
        if request.query_params.get("v") or "versions/" in path:
            headers["Cache-Control"] = "public, max-age=31536000, immutable"

        return FileResponse(file_path, headers=headers)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")
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

### Task 4: 后端 — SSE 事件携带 asset_fingerprints

**Files:**
- Modify: `server/services/generation_tasks.py:198-268` (_emit_generation_success_batch)
- Test: `tests/test_generation_tasks_service.py` (追加测试)

**Step 1: Write the failing test**

在 `tests/test_generation_tasks_service.py` 追加。需要先了解 `_emit_generation_success_batch` 如何被测试。该函数调用 `emit_project_change_batch`，可以 monkeypatch 它来捕获参数：

```python
def test_emit_success_batch_includes_fingerprints(self, monkeypatch, tmp_path):
    """生成成功事件应携带 asset_fingerprints"""
    captured = []
    monkeypatch.setattr(
        generation_tasks, "emit_project_change_batch",
        lambda project_name, changes, source: captured.append(changes)
    )

    # 创建项目目录和媒体文件
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

修改 `server/services/generation_tasks.py:198-268`，在 `_emit_generation_success_batch` 中计算受影响文件的 fingerprint：

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

    # 计算受影响文件的 fingerprint
    asset_fingerprints = _compute_affected_fingerprints(
        project_name, task_type, resource_id
    )

    if task_type == "storyboard":
        changes = [
            {
                "entity_type": "segment",
                "action": "storyboard_ready",
                "entity_id": resource_id,
                "label": f"分镜「{resource_id}」",
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
                "label": f"分镜「{resource_id}」",
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
                "label": f"角色「{resource_id}」设计图",
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
                "label": f"线索「{resource_id}」设计图",
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
            "发送生成完成项目事件失败 project=%s task_type=%s resource_id=%s",
            project_name,
            task_type,
            resource_id,
        )


def _compute_affected_fingerprints(
    project_name: str, task_type: str, resource_id: str
) -> Dict[str, int]:
    """计算受影响文件的 mtime 指纹"""
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

### Task 5: 后端 — 版本还原 API 返回 asset_fingerprints

**Files:**
- Modify: `server/routers/versions.py:154-158` (restore_version 返回值)
- Test: `tests/test_versions_router.py` (追加测试)

**Step 1: Write the failing test**

在 `tests/test_versions_router.py` 中，`_FakeVM.restore_version` 需要配合。测试只验证返回值中包含 `asset_fingerprints`：

```python
def test_restore_returns_asset_fingerprints(self, monkeypatch, tmp_path):
    """版本还原应返回受影响文件的 fingerprint"""
    fake_pm = _FakePM()
    # 覆盖 get_project_path 使其返回 tmp_path
    fake_pm.get_project_path = lambda name: tmp_path

    # 创建目标文件（还原后的当前文件）
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

修改 `server/routers/versions.py:154-158`，在 return 之前计算 fingerprint：

```python
        # 计算还原后文件的 fingerprint
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

### Task 6: 后端 — 视频首帧缩略图提取

**Files:**
- Create: `lib/thumbnail.py`
- Test: `tests/test_thumbnail.py`
- Modify: `server/services/generation_tasks.py` (execute_video_task 调用)

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
        # 用 ffmpeg 生成一个最小测试视频
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
"""视频首帧缩略图提取"""

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
    使用 ffmpeg 提取视频第一帧作为 JPEG 缩略图。

    Args:
        video_path: 视频文件路径
        thumbnail_path: 输出缩略图路径

    Returns:
        缩略图路径（成功）或 None（失败）
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
        logger.warning("提取视频缩略图失败: %s", video_path, exc_info=True)
        return None
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_thumbnail.py -v`
Expected: All PASS (或 skip if no ffmpeg)

**Step 5: Commit**

```bash
git add lib/thumbnail.py tests/test_thumbnail.py
git commit -m "feat: add video thumbnail extraction utility using ffmpeg"
```

---

### Task 7: 后端 — 视频生成后自动提取缩略图

**Files:**
- Modify: `server/services/generation_tasks.py` (execute_video_task)
- Modify: `lib/project_manager.py:528-543` (create_generated_assets 新增 video_thumbnail)
- Test: 在 `tests/test_generation_tasks_service.py` 追加

**Step 1: Write the failing test**

```python
async def test_execute_video_task_generates_thumbnail(self, monkeypatch, tmp_path):
    """视频生成后应自动提取首帧缩略图"""
    project_path = tmp_path / "demo"
    project_path.mkdir()
    (project_path / "storyboards").mkdir()
    (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"img")
    (project_path / "videos").mkdir()

    # ... setup fake PM, fake generator, monkeypatches ...
    # 关键：验证 update_scene_asset 被调用时包含 video_thumbnail
    # 验证 thumbnails/scene_E1S01.jpg 存在

    # 注意：这个测试可能需要 mock extract_video_thumbnail
    # 因为 ffmpeg 可能不可用
```

实际实现中，monkeypatch `extract_video_thumbnail` 为返回预期路径的 mock，然后验证：
1. `extract_video_thumbnail` 被调用
2. `update_scene_asset` 被调用设置 `video_thumbnail`

**Step 2: Write minimal implementation**

修改 `server/services/generation_tasks.py` 的 `execute_video_task`，在视频下载后添加：

```python
    # 在 video 下载完成后、update_scene_asset 之前添加：
    from lib.thumbnail import extract_video_thumbnail

    # 提取视频首帧作为缩略图
    video_file = project_path / f"videos/scene_{resource_id}.mp4"
    thumbnail_file = project_path / f"thumbnails/scene_{resource_id}.jpg"
    await extract_video_thumbnail(video_file, thumbnail_file)

    # 更新 video_thumbnail 资源路径
    if thumbnail_file.exists():
        get_project_manager().update_scene_asset(
            project_name=project_name,
            script_filename=script_file,
            scene_id=resource_id,
            asset_type="video_thumbnail",
            asset_path=f"thumbnails/scene_{resource_id}.jpg",
        )
```

修改 `lib/project_manager.py:528-543`，在 `create_generated_assets` 中添加 `video_thumbnail`：

```python
    @staticmethod
    def create_generated_assets(content_mode: str = "narration") -> Dict:
        return {
            "storyboard_image": None,
            "video_clip": None,
            "video_thumbnail": None,   # 新增
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

### Task 8: 前端 — ProjectChange 类型和 projects-store 扩展

**Files:**
- Modify: `frontend/src/types/workspace.ts` (ProjectChange 新增 asset_fingerprints)
- Modify: `frontend/src/stores/projects-store.ts` (新增 fingerprint 状态管理)
- Test: `frontend/src/stores/stores.test.ts` (追加测试)

**Step 1: Write the failing test**

在 `frontend/src/stores/stores.test.ts` 追加：

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

修改 `frontend/src/types/workspace.ts:10-24`，给 ProjectChange 添加可选字段：

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
  asset_fingerprints?: Record<string, number>;  // 新增
}
```

修改 `frontend/src/stores/projects-store.ts`：

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

### Task 9: 前端 — SSE 处理器使用 fingerprints

**Files:**
- Modify: `frontend/src/hooks/useProjectEventsSSE.ts:212-258` (onChanges)
- Modify: 所有调用 `setCurrentProject` 的地方（传入 fingerprints）
- Test: `frontend/src/hooks/useProjectEventsSSE.test.tsx` (追加)

**Step 1: Write the failing test**

在 `frontend/src/hooks/useProjectEventsSSE.test.tsx` 追加测试，验证 SSE 事件中的 `asset_fingerprints` 被提取并更新到 store。

**Step 2: Write minimal implementation**

修改 `frontend/src/hooks/useProjectEventsSSE.ts:212-258` 的 `onChanges` 处理：

```typescript
onChanges(payload: ProjectChangeBatchPayload) {
  if (disposed) return;
  lastFingerprintRef.current = payload.fingerprint;
  setAssistantToolActivitySuppressed(true);

  // 提取并更新 asset fingerprints（零延迟）
  const mergedFingerprints: Record<string, number> = {};
  for (const change of payload.changes) {
    if (change.asset_fingerprints) {
      Object.assign(mergedFingerprints, change.asset_fingerprints);
    }
  }
  if (Object.keys(mergedFingerprints).length > 0) {
    useProjectsStore.getState().updateAssetFingerprints(mergedFingerprints);
  }

  // 保留 entityRevisions 用于触发非媒体相关的重渲染
  const invalidationKeys = payload.changes.map((change) =>
    buildEntityRevisionKey(change.entity_type, change.entity_id),
  );
  invalidateEntities(invalidationKeys);

  // ... rest of notification/toast logic unchanged ...

  void refreshProject();
},
```

同时，修改所有调用 `setCurrentProject` 的地方，传入 API 响应中的 fingerprints。搜索所有 `setCurrentProject` 调用点，一般在 `refreshProject` 回调中。需要修改 API 调用处，从响应中提取 `asset_fingerprints` 并传给 `setCurrentProject`。

关键调用点（搜索 `setCurrentProject` 的地方）：
- `useProjectEventsSSE.ts` 中的 `refreshProject`
- `useProjectAssetSync.ts` 中的 `refreshProject`
- `StudioCanvasRouter.tsx` 中的 `refreshProject`
- `OverviewCanvas.tsx` 中的 `refreshProject`

每处都需要把 `res.asset_fingerprints` 传入 `setCurrentProject` 的第四个参数。

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

### Task 10: 前端 — 组件 URL 构建切换到 fingerprint

**Files:**
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx:483-491`
- Modify: `frontend/src/components/canvas/lorebook/CharacterCard.tsx:128-134`
- Modify: `frontend/src/components/canvas/OverviewCanvas.tsx:141-143`
- Modify: `frontend/src/components/ui/AvatarStack.tsx:66,125`
- Test: 各组件的现有测试（确保不 regression）

**Step 1: Write implementation**

核心模式变更 — 以 SegmentCard 为例：

```typescript
// 旧代码 (SegmentCard.tsx:483-491)
const entityRevisionKey = buildEntityRevisionKey("segment", segmentId);
const mediaRevision = useAppStore((s) => s.getEntityRevision(entityRevisionKey));
const storyboardUrl = assets?.storyboard_image
  ? API.getFileUrl(projectName, assets.storyboard_image, mediaRevision)
  : null;
const videoUrl = assets?.video_clip
  ? API.getFileUrl(projectName, assets.video_clip, mediaRevision)
  : null;

// 新代码
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

对 CharacterCard、OverviewCanvas、AvatarStack 做同样的模式变更：
- 将 `useAppStore(s => s.getEntityRevision(key))` 替换为 `useProjectsStore(s => s.getAssetFingerprint(path))`

**Step 2: Update VideoPlayer to use poster + preload="none"**

```typescript
// SegmentCard.tsx — VideoPlayer 组件改造
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

调用时传入 poster：`<VideoPlayer src={videoUrl} poster={thumbnailUrl} />`

**Step 3: Run all frontend tests**

Run: `cd frontend && pnpm test`
Expected: All PASS（可能需要更新一些 mock）

**Step 4: Commit**

```bash
git add frontend/src/components/canvas/timeline/SegmentCard.tsx \
  frontend/src/components/canvas/lorebook/CharacterCard.tsx \
  frontend/src/components/canvas/OverviewCanvas.tsx \
  frontend/src/components/ui/AvatarStack.tsx
git commit -m "feat(frontend): switch media URL cache-busting from session revision to asset fingerprints"
```

---

### Task 11: 前端 — VersionTimeMachine 适配

**Files:**
- Modify: `frontend/src/components/canvas/timeline/VersionTimeMachine.tsx`
- Modify: `frontend/src/api.ts` (restoreVersion 返回类型)
- Test: `frontend/src/components/canvas/timeline/VersionTimeMachine.test.tsx`

**Step 1: Write implementation**

修改 `handleRestore` 使用返回的 fingerprints：

```typescript
async function handleRestore(version: number) {
  setRestoringVersion(version);
  try {
    const result = await API.restoreVersion(projectName, resourceType, resourceId, version);
    // 用返回的 fingerprint 更新 store（替代 invalidateEntities）
    if (result.asset_fingerprints) {
      useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
    }
    await onRestore?.(version);
    await loadVersions();
    setSelectedVersion(version);
    useAppStore.getState().pushToast(`已切换到 v${version}`, "success");
  } catch (err) {
    useAppStore.getState().pushToast(`切换版本失败: ${(err as Error).message}`, "error");
  } finally {
    setRestoringVersion(null);
  }
}
```

修改视频预览也使用 `preload="none"`：

```tsx
<video
  src={selectedInfo.file_url}
  className="mb-2 w-full rounded-lg border border-gray-800 bg-black object-contain"
  controls
  playsInline
  preload="none"
/>
```

修改 `frontend/src/api.ts` 中 `restoreVersion` 的返回类型，添加 `asset_fingerprints`：

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

### Task 12: 全栈验证和清理

**Files:**
- 全部修改过的文件

**Step 1: Run all backend tests**

Run: `python -m pytest -v`
Expected: All PASS

**Step 2: Run all frontend tests**

Run: `cd frontend && pnpm check`
Expected: typecheck + test 全 PASS

**Step 3: Build frontend**

Run: `cd frontend && pnpm build`
Expected: 构建成功

**Step 4: Commit final cleanup if needed**

```bash
git commit -m "chore: final cleanup for media cache and video thumbnail feature"
```
