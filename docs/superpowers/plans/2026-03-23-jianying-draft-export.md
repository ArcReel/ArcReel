# CapCut Draft Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export ArcReel's generated video clips for a single episode as a CapCut draft ZIP, which users unzip to their local CapCut drafts directory and open directly in CapCut for editing.

**Architecture:** 后端新增 `JianyingDraftService` 服务层，调用 pyjianyingdraft 库生成草稿文件 + ZIP 打包。复用现有 download token 签发机制，新增一个 GET 端点返回 ZIP 流。前端改造 `ExportScopeDialog`，新增剪映草稿选项（含集数下拉 + 草稿目录输入框）。

**Tech Stack:** pyjianyingdraft (Python), FastAPI, React + TypeScript, zustand

**Spec:** `docs/superpowers/specs/2026-03-23-jianying-draft-export-design.md`

**Prerequisites:** `pyjianyingdraft>=0.2.6` 已添加到 `pyproject.toml`（含传递依赖 `imageio`、`numpy`、`pymediainfo`）。系统需安装 `mediainfo`。

---

## File Structure

| Action | File Path | Responsibility |
|------|---------|------|
| Create | `server/services/jianying_draft_service.py` | Core CapCut draft generation service |
| Create | `tests/test_jianying_draft_service.py` | service layer unit tests |
| Create | `tests/test_jianying_draft_routes.py` | router layer integration tests |
| Modify | `server/routers/projects.py` | Add GET export endpoint |
| Modify | `frontend/src/api.ts` | Add download URL construction method |
| Modify | `frontend/src/components/layout/ExportScopeDialog.tsx` | Extend ExportScope type + add CapCut draft option + form |
| Modify | `frontend/src/components/layout/GlobalHeader.tsx` | Handle CapCut export callback |
| Modify | `frontend/src/components/layout/GlobalHeader.test.tsx` | adapt to new ExportScopeDialog props |

---

## Task 1: JianyingDraftService — 视频片段收集与画布尺寸

**Files:**
- Create: `server/services/jianying_draft_service.py`
- Create: `tests/test_jianying_draft_service.py`

- [ ] **Step 1: 创建测试文件，编写 `_collect_video_clips` 的测试**

`tests/test_jianying_draft_service.py`:

```python
"""剪映草稿导出服务的单元测试"""

import json
from pathlib import Path

import pytest


class TestCollectVideoClips:
    """测试从剧本中收集已完成视频片段"""

    def test_narration_mode_collects_existing_videos(self, tmp_path):
        """narration 模式：收集存在的 video_clip"""
        from server.services.jianying_draft_service import JianyingDraftService

        # 创建项目目录和视频文件
        project_dir = tmp_path / "projects" / "demo"
        videos_dir = project_dir / "videos"
        videos_dir.mkdir(parents=True)
        (videos_dir / "segment_S1.mp4").write_bytes(b"fake")
        (videos_dir / "segment_S2.mp4").write_bytes(b"fake")

        script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "S1",
                    "duration_seconds": 8,
                    "novel_text": "从前有座山",
                    "generated_assets": {"video_clip": "videos/segment_S1.mp4", "status": "completed"},
                },
                {
                    "segment_id": "S2",
                    "duration_seconds": 6,
                    "novel_text": "山上有座庙",
                    "generated_assets": {"video_clip": "videos/segment_S2.mp4", "status": "completed"},
                },
                {
                    "segment_id": "S3",
                    "duration_seconds": 8,
                    "novel_text": "庙里有个老和尚",
                    "generated_assets": {"status": "pending"},
                },
            ],
        }

        svc = JianyingDraftService.__new__(JianyingDraftService)
        clips = svc._collect_video_clips(script, project_dir)

        assert len(clips) == 2
        assert clips[0]["id"] == "S1"
        assert clips[0]["novel_text"] == "从前有座山"
        assert clips[1]["id"] == "S2"

    def test_drama_mode_collects_scenes(self, tmp_path):
        """drama 模式：收集 scenes 而非 segments"""
        from server.services.jianying_draft_service import JianyingDraftService

        project_dir = tmp_path / "projects" / "demo"
        videos_dir = project_dir / "videos"
        videos_dir.mkdir(parents=True)
        (videos_dir / "scene_E1S01.mp4").write_bytes(b"fake")

        script = {
            "content_mode": "drama",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 8,
                    "generated_assets": {"video_clip": "videos/scene_E1S01.mp4", "status": "completed"},
                },
            ],
        }

        svc = JianyingDraftService.__new__(JianyingDraftService)
        clips = svc._collect_video_clips(script, project_dir)

        assert len(clips) == 1
        assert clips[0]["id"] == "E1S01"
        assert clips[0]["novel_text"] == ""  # drama 模式无 novel_text

    def test_skips_missing_video_files(self, tmp_path):
        """script 中有记录但文件不存在时跳过"""
        from server.services.jianying_draft_service import JianyingDraftService

        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)

        script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "S1",
                    "duration_seconds": 8,
                    "novel_text": "text",
                    "generated_assets": {"video_clip": "videos/segment_S1.mp4", "status": "completed"},
                },
            ],
        }

        svc = JianyingDraftService.__new__(JianyingDraftService)
        clips = svc._collect_video_clips(script, project_dir)

        assert len(clips) == 0


class TestResolveCanvasSize:
    """测试画布尺寸解析"""

    def test_16_9_returns_1920x1080(self):
        from server.services.jianying_draft_service import JianyingDraftService

        svc = JianyingDraftService.__new__(JianyingDraftService)
        w, h = svc._resolve_canvas_size({"aspect_ratio": {"video": "16:9"}})
        assert (w, h) == (1920, 1080)

    def test_9_16_returns_1080x1920(self):
        from server.services.jianying_draft_service import JianyingDraftService

        svc = JianyingDraftService.__new__(JianyingDraftService)
        w, h = svc._resolve_canvas_size({"aspect_ratio": {"video": "9:16"}})
        assert (w, h) == (1080, 1920)

    def test_default_is_16_9(self):
        from server.services.jianying_draft_service import JianyingDraftService

        svc = JianyingDraftService.__new__(JianyingDraftService)
        w, h = svc._resolve_canvas_size({})
        assert (w, h) == (1920, 1080)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_jianying_draft_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.services.jianying_draft_service'`

- [ ] **Step 3: 实现 `_collect_video_clips` 和 `_resolve_canvas_size`**

`server/services/jianying_draft_service.py`:

```python
"""剪映草稿导出服务

将 ArcReel 单集已生成的视频片段导出为剪映草稿 ZIP。
使用 pyJianYingDraft 库生成 draft_content.json，
后处理路径替换使草稿指向用户本地剪映目录。
"""

import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pyJianYingDraft as draft
from pyJianYingDraft import TextSegment, TextStyle, TrackType, VideoMaterial, VideoSegment, trange

from lib.project_manager import ProjectManager

logger = logging.getLogger(__name__)


class JianyingDraftService:
    """剪映草稿导出服务"""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    # ------------------------------------------------------------------
    # 内部方法：数据提取
    # ------------------------------------------------------------------

    def _find_episode_script(
        self, project_name: str, project: dict, episode: int
    ) -> tuple[dict, str]:
        """定位指定集的剧本文件，返回 (script_dict, filename)"""
        episodes = project.get("episodes", [])
        ep_entry = next(
            (e for e in episodes if e.get("episode") == episode), None
        )
        if ep_entry is None:
            raise FileNotFoundError(f"第 {episode} 集不存在")

        script_file = ep_entry.get("script_file", "")
        filename = Path(script_file).name
        script_data = self.pm.load_script(project_name, filename)
        return script_data, filename

    def _collect_video_clips(
        self, script: dict, project_dir: Path
    ) -> list[dict[str, Any]]:
        """从剧本中提取已完成视频的片段列表"""
        content_mode = script.get("content_mode", "narration")
        items = script.get(
            "segments" if content_mode == "narration" else "scenes", []
        )
        id_field = "segment_id" if content_mode == "narration" else "scene_id"

        clips = []
        for item in items:
            assets = item.get("generated_assets") or {}
            video_clip = assets.get("video_clip")
            if not video_clip:
                continue

            abs_path = project_dir / video_clip
            if not abs_path.exists():
                continue

            clips.append(
                {
                    "id": item.get(id_field, ""),
                    "duration_seconds": item.get("duration_seconds", 8),
                    "video_clip": video_clip,
                    "abs_path": abs_path,
                    "novel_text": item.get("novel_text", ""),
                }
            )

        return clips

    def _resolve_canvas_size(self, project: dict) -> tuple[int, int]:
        """根据项目 aspect_ratio 确定画布尺寸"""
        aspect = project.get("aspect_ratio", {}).get("video", "16:9")
        if aspect == "9:16":
            return 1080, 1920
        return 1920, 1080
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_jianying_draft_service.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add server/services/jianying_draft_service.py tests/test_jianying_draft_service.py
git commit -m "feat(jianying): add video clip collection and canvas size resolution"
```

---

## Task 2: JianyingDraftService — 草稿生成核心逻辑

**Files:**
- Modify: `server/services/jianying_draft_service.py`
- Modify: `tests/test_jianying_draft_service.py`

> Note: 此任务的测试需要真实的视频文件（pyjianyingdraft 的 `VideoMaterial` 需要 mediainfo 提取时长）。使用 `imageio` 生成极短的测试视频。

- [ ] **Step 1: 编写草稿生成 + 路径替换的集成测试**

在 `tests/test_jianying_draft_service.py` 末尾追加：

```python
import imageio.v3 as iio
import numpy as np


def _make_test_video(path: Path, duration_frames: int = 30, fps: int = 30):
    """生成一个极短的测试视频文件（1 秒，30fps，64x64 像素）"""
    frames = [np.zeros((64, 64, 3), dtype=np.uint8) for _ in range(duration_frames)]
    iio.imwrite(str(path), frames, fps=fps, codec="libx264")


class TestGenerateDraft:
    """测试 pyjianyingdraft 草稿生成"""

    def test_generates_draft_content_json(self, tmp_path):
        """生成的草稿目录包含 draft_content.json"""
        from server.services.jianying_draft_service import JianyingDraftService

        # 准备临时草稿目录和素材
        draft_dir = tmp_path / "测试草稿"
        assets_dir = draft_dir / "assets"
        assets_dir.mkdir(parents=True)
        _make_test_video(assets_dir / "scene_S1.mp4")
        _make_test_video(assets_dir / "scene_S2.mp4")

        clips = [
            {"id": "S1", "local_path": str(assets_dir / "scene_S1.mp4"), "novel_text": ""},
            {"id": "S2", "local_path": str(assets_dir / "scene_S2.mp4"), "novel_text": ""},
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="测试草稿",
            clips=clips,
            width=1920,
            height=1080,
            content_mode="drama",
        )

        assert (draft_dir / "draft_content.json").exists()
        assert (draft_dir / "draft_meta_info.json").exists()

    def test_narration_mode_includes_subtitle_track(self, tmp_path):
        """narration 模式生成字幕轨"""
        from server.services.jianying_draft_service import JianyingDraftService

        draft_dir = tmp_path / "字幕草稿"
        assets_dir = draft_dir / "assets"
        assets_dir.mkdir(parents=True)
        _make_test_video(assets_dir / "seg_S1.mp4")

        clips = [
            {"id": "S1", "local_path": str(assets_dir / "seg_S1.mp4"), "novel_text": "从前有座山"},
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="字幕草稿",
            clips=clips,
            width=1080,
            height=1920,
            content_mode="narration",
        )

        content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        # 验证有两个轨道（video + text）
        tracks = content.get("tracks", [])
        assert len(tracks) == 2

    def test_drama_mode_no_subtitle_track(self, tmp_path):
        """drama 模式不生成字幕轨"""
        from server.services.jianying_draft_service import JianyingDraftService

        draft_dir = tmp_path / "无字幕草稿"
        assets_dir = draft_dir / "assets"
        assets_dir.mkdir(parents=True)
        _make_test_video(assets_dir / "scene_S1.mp4")

        clips = [
            {"id": "S1", "local_path": str(assets_dir / "scene_S1.mp4"), "novel_text": ""},
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="无字幕草稿",
            clips=clips,
            width=1920,
            height=1080,
            content_mode="drama",
        )

        content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        tracks = content.get("tracks", [])
        assert len(tracks) == 1


class TestReplacePaths:
    """测试路径后处理（JSON 安全替换）"""

    def test_replaces_tmp_prefix_in_json(self, tmp_path):
        """递归替换 JSON 中的临时路径前缀"""
        from server.services.jianying_draft_service import JianyingDraftService

        json_path = tmp_path / "draft_content.json"
        data = {
            "materials": {
                "videos": [
                    {"path": "/tmp/arcreel_jy_abc/草稿/assets/s1.mp4"},
                    {"path": "/tmp/arcreel_jy_abc/草稿/assets/s2.mp4"},
                ]
            },
            "other": "no change",
        }
        json_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._replace_paths_in_draft(
            json_path=json_path,
            tmp_prefix="/tmp/arcreel_jy_abc/草稿/assets",
            target_prefix="/Users/test/Movies/JianyingPro/草稿/assets",
        )

        result = json.loads(json_path.read_text(encoding="utf-8"))
        assert result["materials"]["videos"][0]["path"] == "/Users/test/Movies/JianyingPro/草稿/assets/s1.mp4"
        assert result["materials"]["videos"][1]["path"] == "/Users/test/Movies/JianyingPro/草稿/assets/s2.mp4"
        assert result["other"] == "no change"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_jianying_draft_service.py::TestGenerateDraft -v`
Expected: FAIL — `AttributeError: 'JianyingDraftService' object has no attribute '_generate_draft'`

- [ ] **Step 3: 实现 `_generate_draft` 和 `_replace_paths_in_draft`**

在 `server/services/jianying_draft_service.py` 的 `JianyingDraftService` 类中追加：

```python
    # ------------------------------------------------------------------
    # 内部方法：草稿生成
    # ------------------------------------------------------------------

    def _generate_draft(
        self,
        *,
        draft_dir: Path,
        draft_name: str,
        clips: list[dict],
        width: int,
        height: int,
        content_mode: str,
    ) -> None:
        """使用 pyJianYingDraft 在 draft_dir 中生成草稿文件"""
        folder = draft.DraftFolder(str(draft_dir.parent))
        script_file = folder.create_draft(draft_name, width=width, height=height)

        # 视频轨
        script_file.add_track(TrackType.video)

        # 字幕轨（仅 narration 模式）
        has_subtitle = content_mode == "narration"
        if has_subtitle:
            script_file.add_track(TrackType.text, "字幕")
            text_style = TextStyle(
                size=8.0,
                color=(1.0, 1.0, 1.0),
                align=1,
                bold=True,
                auto_wrapping=True,
            )

        # 逐片段添加
        offset_us = 0
        for clip in clips:
            # 预读实际视频时长
            material = VideoMaterial(clip["local_path"])
            actual_duration_us = material.duration

            # 视频片段
            video_seg = VideoSegment(
                clip["local_path"],
                trange(offset_us, actual_duration_us),
            )
            script_file.add_segment(video_seg)

            # 字幕片段
            if has_subtitle and clip.get("novel_text"):
                text_seg = TextSegment(
                    text=clip["novel_text"],
                    timerange=trange(offset_us, actual_duration_us),
                    style=text_style,
                )
                script_file.add_segment(text_seg)

            offset_us += actual_duration_us

        script_file.save()

    def _replace_paths_in_draft(
        self, *, json_path: Path, tmp_prefix: str, target_prefix: str
    ) -> None:
        """JSON 安全地替换 draft_content.json 中的临时路径"""
        data = json.loads(json_path.read_text(encoding="utf-8"))

        def _walk(obj: Any) -> Any:
            if isinstance(obj, str) and tmp_prefix in obj:
                return obj.replace(tmp_prefix, target_prefix)
            if isinstance(obj, dict):
                return {k: _walk(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_walk(v) for v in obj]
            return obj

        data = _walk(data)
        json_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
```

- [ ] **Step 4: 运行全部测试确认通过**

Run: `uv run python -m pytest tests/test_jianying_draft_service.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add server/services/jianying_draft_service.py tests/test_jianying_draft_service.py
git commit -m "feat(jianying): implement draft generation with pyjianyingdraft and path replacement"
```

---

## Task 3: JianyingDraftService — 完整导出流程 `export_episode_draft`

**Files:**
- Modify: `server/services/jianying_draft_service.py`
- Modify: `tests/test_jianying_draft_service.py`

- [ ] **Step 1: 编写 `export_episode_draft` 端到端测试**

在 `tests/test_jianying_draft_service.py` 追加：

```python
class TestExportEpisodeDraft:
    """端到端测试：完整导出流程"""

    def _setup_project(self, tmp_path) -> tuple:
        """创建带视频片段的测试项目"""
        from lib.project_manager import ProjectManager

        pm = ProjectManager(tmp_path / "projects")
        project_dir = pm.get_project_path("demo")
        project_dir.mkdir(parents=True)
        videos_dir = project_dir / "videos"
        videos_dir.mkdir()

        # 创建测试视频
        _make_test_video(videos_dir / "segment_S1.mp4")
        _make_test_video(videos_dir / "segment_S2.mp4")

        # 创建 project.json
        project_data = {
            "title": "测试项目",
            "content_mode": "narration",
            "aspect_ratio": {"video": "9:16"},
            "episodes": [
                {"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"},
            ],
        }
        (project_dir / "project.json").write_text(
            json.dumps(project_data, ensure_ascii=False), encoding="utf-8"
        )

        # 创建剧本
        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir()
        script_data = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "S1",
                    "duration_seconds": 8,
                    "novel_text": "从前有座山",
                    "generated_assets": {"video_clip": "videos/segment_S1.mp4", "status": "completed"},
                },
                {
                    "segment_id": "S2",
                    "duration_seconds": 6,
                    "novel_text": "山上有座庙",
                    "generated_assets": {"video_clip": "videos/segment_S2.mp4", "status": "completed"},
                },
            ],
        }
        (scripts_dir / "episode_1.json").write_text(
            json.dumps(script_data, ensure_ascii=False), encoding="utf-8"
        )

        return pm, project_dir

    def test_exports_zip_with_correct_structure(self, tmp_path):
        """导出 ZIP 包含草稿 JSON + 视频素材"""
        from server.services.jianying_draft_service import JianyingDraftService

        pm, _ = self._setup_project(tmp_path)
        svc = JianyingDraftService(pm)

        zip_path = svc.export_episode_draft(
            project_name="demo",
            episode=1,
            draft_path="/Users/test/Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
        )

        assert zip_path.exists()
        assert zip_path.suffix == ".zip"

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            # 草稿文件
            assert any("draft_content.json" in n for n in names)
            assert any("draft_meta_info.json" in n for n in names)
            # 视频素材
            assert any("segment_S1.mp4" in n for n in names)
            assert any("segment_S2.mp4" in n for n in names)

    def test_draft_content_has_user_paths(self, tmp_path):
        """draft_content.json 中的路径已替换为用户本地路径"""
        from server.services.jianying_draft_service import JianyingDraftService

        pm, _ = self._setup_project(tmp_path)
        svc = JianyingDraftService(pm)
        draft_path = "/Users/test/drafts"

        zip_path = svc.export_episode_draft(
            project_name="demo", episode=1, draft_path=draft_path
        )

        with zipfile.ZipFile(zip_path) as zf:
            content_entry = [n for n in zf.namelist() if "draft_content.json" in n][0]
            content = json.loads(zf.read(content_entry).decode("utf-8"))
            raw = json.dumps(content)
            # 不应包含临时目录路径
            assert "/tmp/" not in raw and "\\Temp\\" not in raw
            # 应包含用户路径
            assert draft_path in raw

    def test_episode_not_found_raises(self, tmp_path):
        """集数不存在时抛出 FileNotFoundError"""
        from server.services.jianying_draft_service import JianyingDraftService

        pm, _ = self._setup_project(tmp_path)
        svc = JianyingDraftService(pm)

        with pytest.raises(FileNotFoundError, match="第 99 集不存在"):
            svc.export_episode_draft(project_name="demo", episode=99, draft_path="/tmp")

    def test_no_videos_raises_value_error(self, tmp_path):
        """无已完成视频时抛出 ValueError"""
        from lib.project_manager import ProjectManager
        from server.services.jianying_draft_service import JianyingDraftService

        pm = ProjectManager(tmp_path / "projects")
        project_dir = pm.get_project_path("empty")
        project_dir.mkdir(parents=True)

        (project_dir / "project.json").write_text(json.dumps({
            "title": "空项目",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        }, ensure_ascii=False))

        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "episode_1.json").write_text(json.dumps({
            "content_mode": "narration",
            "segments": [
                {"segment_id": "S1", "duration_seconds": 8, "novel_text": "", "generated_assets": {"status": "pending"}},
            ],
        }, ensure_ascii=False))

        svc = JianyingDraftService(pm)
        with pytest.raises(ValueError, match="请先生成视频"):
            svc.export_episode_draft(project_name="empty", episode=1, draft_path="/tmp")
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_jianying_draft_service.py::TestExportEpisodeDraft -v`
Expected: FAIL — `AttributeError: 'JianyingDraftService' object has no attribute 'export_episode_draft'`

- [ ] **Step 3: 实现 `export_episode_draft`**

在 `server/services/jianying_draft_service.py` 的 `JianyingDraftService` 类中追加：

```python
    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def export_episode_draft(
        self,
        project_name: str,
        episode: int,
        draft_path: str,
    ) -> Path:
        """
        导出指定集的剪映草稿 ZIP。

        Returns:
            ZIP 文件路径（临时文件，调用方负责清理）

        Raises:
            FileNotFoundError: 项目或剧本不存在
            ValueError: 无可导出的视频片段
        """
        project = self.pm.load_project(project_name)
        project_dir = self.pm.get_project_path(project_name)

        # 1. 定位剧本
        script_data, _ = self._find_episode_script(project_name, project, episode)

        # 2. 收集已完成视频
        content_mode = script_data.get("content_mode", "narration")
        clips = self._collect_video_clips(script_data, project_dir)
        if not clips:
            raise ValueError(f"第 {episode} 集没有已完成的视频片段，请先生成视频")

        # 3. 画布尺寸
        width, height = self._resolve_canvas_size(project)

        # 4. 创建临时目录 + 复制素材
        title = project.get("title", project_name)
        draft_name = f"{title}_第{episode}集"
        tmp_dir = Path(tempfile.mkdtemp(prefix="arcreel_jy_"))
        draft_dir = tmp_dir / draft_name
        assets_dir = draft_dir / "assets"
        assets_dir.mkdir(parents=True)

        local_clips = []
        for clip in clips:
            src = clip["abs_path"]
            dst = assets_dir / src.name
            try:
                dst.hardlink_to(src)
            except OSError:
                shutil.copy2(src, dst)
            local_clips.append({**clip, "local_path": str(dst)})

        # 5. 生成草稿
        self._generate_draft(
            draft_dir=draft_dir,
            draft_name=draft_name,
            clips=local_clips,
            width=width,
            height=height,
            content_mode=content_mode,
        )

        # 6. 路径后处理
        self._replace_paths_in_draft(
            json_path=draft_dir / "draft_content.json",
            tmp_prefix=str(assets_dir),
            target_prefix=f"{draft_path}/{draft_name}/assets",
        )

        # 7. 打包 ZIP
        zip_path = tmp_dir / f"{draft_name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in draft_dir.rglob("*"):
                if file.is_file():
                    arcname = f"{draft_name}/{file.relative_to(draft_dir)}"
                    zf.write(file, arcname)

        return zip_path
```

- [ ] **Step 4: 运行全部服务测试确认通过**

Run: `uv run python -m pytest tests/test_jianying_draft_service.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add server/services/jianying_draft_service.py tests/test_jianying_draft_service.py
git commit -m "feat(jianying): implement full export_episode_draft pipeline"
```

---

## Task 4: 后端路由 — GET 导出端点

**Files:**
- Modify: `server/routers/projects.py` (在现有导出端点nearby)
- Create: `tests/test_jianying_draft_routes.py`

- [ ] **Step 1: 编写路由测试**

`tests/test_jianying_draft_routes.py`:

```python
"""剪映草稿导出路由的集成测试"""

import json
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import imageio.v3 as iio
import numpy as np
import pytest
from fastapi.testclient import TestClient

from lib.project_manager import ProjectManager
from server.auth import create_download_token, create_token


def _make_test_video(path: Path):
    frames = [np.zeros((64, 64, 3), dtype=np.uint8) for _ in range(30)]
    iio.imwrite(str(path), frames, fps=30, codec="libx264")


def _setup_project(pm: ProjectManager):
    """创建测试项目 + 剧本 + 视频"""
    project_dir = pm.get_project_path("demo")
    project_dir.mkdir(parents=True)

    videos_dir = project_dir / "videos"
    videos_dir.mkdir()
    _make_test_video(videos_dir / "segment_S1.mp4")

    scripts_dir = project_dir / "scripts"
    scripts_dir.mkdir()

    (project_dir / "project.json").write_text(json.dumps({
        "title": "测试",
        "content_mode": "narration",
        "aspect_ratio": {"video": "16:9"},
        "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
    }, ensure_ascii=False))

    (scripts_dir / "episode_1.json").write_text(json.dumps({
        "content_mode": "narration",
        "segments": [{
            "segment_id": "S1",
            "duration_seconds": 8,
            "novel_text": "测试文本",
            "generated_assets": {"video_clip": "videos/segment_S1.mp4", "status": "completed"},
        }],
    }, ensure_ascii=False))


def _client(monkeypatch, pm: ProjectManager) -> TestClient:
    """创建绑定到指定 ProjectManager 的 TestClient"""
    from server.routers import projects as proj_mod

    monkeypatch.setattr(proj_mod, "pm", pm)

    from server.app import app
    return TestClient(app)


class TestJianyingDraftExport:
    """剪映草稿导出端点测试"""

    def test_export_returns_zip(self, tmp_path, monkeypatch):
        """正常导出返回 ZIP"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "demo")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={
                "episode": 1,
                "draft_path": "/Users/test/drafts",
                "download_token": token,
            },
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "剪映草稿" in response.headers.get("content-disposition", "")

        # 验证是合法 ZIP
        zf = zipfile.ZipFile(BytesIO(response.content))
        names = zf.namelist()
        assert any("draft_content.json" in n for n in names)

    def test_missing_episode_returns_404(self, tmp_path, monkeypatch):
        """集数不存在返回 404"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "demo")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 99, "draft_path": "/tmp", "download_token": token},
        )
        assert response.status_code == 404

    def test_no_videos_returns_422(self, tmp_path, monkeypatch):
        """无已完成视频返回 422"""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = pm.get_project_path("empty")
        project_dir.mkdir(parents=True)

        (project_dir / "project.json").write_text(json.dumps({
            "title": "空",
            "content_mode": "narration",
            "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
        }, ensure_ascii=False))
        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "episode_1.json").write_text(json.dumps({
            "content_mode": "narration",
            "segments": [{"segment_id": "S1", "duration_seconds": 8, "novel_text": "", "generated_assets": {"status": "pending"}}],
        }, ensure_ascii=False))

        client = _client(monkeypatch, pm)
        token = create_download_token("testuser", "empty")
        response = client.get(
            "/api/v1/projects/empty/export/jianying-draft",
            params={"episode": 1, "draft_path": "/tmp", "download_token": token},
        )
        assert response.status_code == 422

    def test_invalid_token_returns_401(self, tmp_path, monkeypatch):
        """无效 token 返回 401"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 1, "draft_path": "/tmp", "download_token": "bad_token"},
        )
        assert response.status_code == 401

    def test_empty_draft_path_returns_422(self, tmp_path, monkeypatch):
        """draft_path 为空返回 422"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "demo")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 1, "draft_path": "", "download_token": token},
        )
        assert response.status_code == 422

    def test_control_chars_in_draft_path_returns_422(self, tmp_path, monkeypatch):
        """draft_path 含控制字符返回 422"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "demo")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 1, "draft_path": "/tmp/\x00bad", "download_token": token},
        )
        assert response.status_code == 422

    def test_long_draft_path_returns_422(self, tmp_path, monkeypatch):
        """draft_path 超过 1024 字符返回 422"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "demo")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 1, "draft_path": "x" * 1025, "download_token": token},
        )
        assert response.status_code == 422

    def test_mismatched_token_returns_403(self, tmp_path, monkeypatch):
        """token 与项目不匹配返回 403"""
        pm = ProjectManager(tmp_path / "projects")
        _setup_project(pm)
        client = _client(monkeypatch, pm)

        token = create_download_token("testuser", "other_project")
        response = client.get(
            "/api/v1/projects/demo/export/jianying-draft",
            params={"episode": 1, "draft_path": "/tmp", "download_token": token},
        )
        assert response.status_code == 403
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_jianying_draft_routes.py -v`
Expected: FAIL — 404（端点不存在）

- [ ] **Step 3: 在 `server/routers/projects.py` 中添加端点**

在现有 `export_project_archive` 端点（约行 199）之后追加：

```python
# --- 剪映草稿导出 ---

def get_jianying_draft_service() -> "JianyingDraftService":
    from server.services.jianying_draft_service import JianyingDraftService
    return JianyingDraftService(get_project_manager())


def _validate_draft_path(draft_path: str) -> str:
    """校验 draft_path 合法性"""
    if not draft_path or not draft_path.strip():
        raise HTTPException(status_code=422, detail="请提供有效的剪映草稿目录路径")
    if len(draft_path) > 1024:
        raise HTTPException(status_code=422, detail="草稿目录路径过长")
    if any(ord(c) < 32 for c in draft_path):
        raise HTTPException(status_code=422, detail="草稿目录路径包含非法字符")
    return draft_path.strip()


@router.get("/{name}/export/jianying-draft")
async def export_jianying_draft(
    name: str,
    episode: int = Query(..., description="集数编号"),
    draft_path: str = Query(..., description="用户本地剪映草稿目录"),
    download_token: str = Query(..., description="下载 token"),
):
    """导出指定集的剪映草稿 ZIP"""
    import jwt as pyjwt

    # 1. 验证 download_token
    try:
        verify_download_token(download_token, name)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="下载链接已过期，请重新导出")
    except ValueError:
        raise HTTPException(status_code=403, detail="下载 token 与项目不匹配")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="下载 token 无效")

    # 2. 校验 draft_path
    draft_path = _validate_draft_path(draft_path)

    # 3. 调用服务
    svc = get_jianying_draft_service()
    try:
        zip_path = svc.export_episode_draft(
            project_name=name, episode=episode, draft_path=draft_path
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logger.exception("剪映草稿导出失败: project=%s episode=%d", name, episode)
        raise HTTPException(status_code=500, detail="剪映草稿导出失败，请稍后重试")

    download_name = f"{name}_第{episode}集_剪映草稿.zip"

    def _cleanup_temp_dir(dir_path: str) -> None:
        shutil.rmtree(dir_path, ignore_errors=True)

    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=download_name,
        background=BackgroundTask(_cleanup_temp_dir, str(zip_path.parent)),
    )
```

确保文件顶部已导入 `verify_download_token`（检查现有 import，应该已有）和 `import shutil`。

- [ ] **Step 4: 运行路由测试确认通过**

Run: `uv run python -m pytest tests/test_jianying_draft_routes.py -v`
Expected: 9 passed

- [ ] **Step 5: 运行全部后端测试confirm no regressions**

Run: `uv run python -m pytest tests/ -v --timeout=60`
Expected: 全部通过

- [ ] **Step 6: Commit**

```bash
git add server/routers/projects.py tests/test_jianying_draft_routes.py
git commit -m "feat(jianying): add GET /export/jianying-draft endpoint with token auth"
```

---

## Task 5: 前端 — API 层 + 类型扩展

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components/layout/ExportScopeDialog.tsx` (ExportScope 类型定义在此文件第 5 行)

- [ ] **Step 1: 扩展 ExportScope 类型**

在 `frontend/src/components/layout/ExportScopeDialog.tsx` 第 5 行，将：

```typescript
export type ExportScope = "current" | "full";
```

改为：

```typescript
export type ExportScope = "current" | "full" | "jianying-draft";
```

- [ ] **Step 2: 在 `frontend/src/api.ts` 中新增方法**

在现有 `getExportDownloadUrl` 方法之后追加：

```typescript
  /** 构造剪映草稿下载 URL */
  static getJianyingDraftDownloadUrl(
    projectName: string,
    episode: number,
    draftPath: string,
    downloadToken: string,
  ): string {
    return `${API_BASE}/projects/${encodeURIComponent(projectName)}/export/jianying-draft?episode=${encodeURIComponent(episode)}&draft_path=${encodeURIComponent(draftPath)}&download_token=${encodeURIComponent(downloadToken)}`;
  }
```

- [ ] **Step 3: 运行前端类型检查**

Run: `cd frontend && pnpm typecheck`
Expected: 无错误

- [ ] **Step 4: 提交**

```bash
git add frontend/src/api.ts frontend/src/types/project.ts
git commit -m "feat(jianying): add frontend API method and extend ExportScope type"
```

---

## Task 6: 前端 — ExportScopeDialog 改造

**Files:**
- Modify: `frontend/src/components/layout/ExportScopeDialog.tsx`

- [ ] **Step 1: 改造 ExportScopeDialog 组件**

重写 `ExportScopeDialog.tsx`，保留原有的两个导出选项，新增剪映草稿选项和表单模式：

```typescript
// ExportScopeDialog 改造要点：
// 1. 新增 props: episodes (EpisodeMeta[]), onJianyingExport(episode, draftPath)
// 2. 内部状态：mode ("select" | "jianying-form")
// 3. 选择"剪映草稿"后切换到表单模式
// 4. 表单包含：集数下拉 + 草稿目录输入框 + 导出按钮
// 5. localStorage 缓存草稿目录
// 6. OS 检测 placeholder
```

组件 props 接口：

```typescript
interface ExportScopeDialogProps {
  open: boolean;
  onClose: () => void;                // 保持现有命名
  onSelect: (scope: "current" | "full") => void;
  anchorRef: React.RefObject<HTMLElement>;
  // 新增
  episodes?: EpisodeMeta[];
  onJianyingExport?: (episode: number, draftPath: string) => void;
  jianyingExporting?: boolean;
}
```

关键实现细节：

- **集数下拉**：`<select>` 遍历 `episodes`，仅一集时隐藏
- **草稿目录输入**：`<input type="text">`，`onChange` 时同步写 `localStorage.setItem("arcreel_jianying_draft_path", value)`
- **OS placeholder**：`navigator.platform.includes("Win")` 区分 Windows/macOS 路径示例
- **导出按钮**：`jianyingExporting` 时禁用，显示"导出中..."
- **返回按钮**：表单模式左上角 ← 回到选择模式

- [ ] **Step 2: 运行前端类型检查**

Run: `cd frontend && pnpm typecheck`
Expected: 无错误

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/layout/ExportScopeDialog.tsx
git commit -m "feat(jianying): add JianYing draft export option to ExportScopeDialog"
```

---

## Task 7: 前端 — GlobalHeader 集成

**Files:**
- Modify: `frontend/src/components/layout/GlobalHeader.tsx`

- [ ] **Step 1: 在 GlobalHeader 中添加剪映导出处理函数**

在 `handleExportProject` 附近新增：

```typescript
const [jianyingExporting, setJianyingExporting] = useState(false);

const handleJianyingExport = async (episode: number, draftPath: string) => {
  if (!currentProjectName || jianyingExporting) return;

  setJianyingExporting(true);
  try {
    // 复用现有 token 签发
    const { download_token } = await API.requestExportToken(currentProjectName, "current");
    const url = API.getJianyingDraftDownloadUrl(
      currentProjectName, episode, draftPath, download_token
    );
    window.open(url, "_blank");
    setExportDialogOpen(false);
    toast.success("导出已开始，请将下载的 ZIP 解压到剪映草稿目录中");
  } catch (err) {
    toast.error(`剪映草稿导出失败: ${err instanceof Error ? err.message : "未知错误"}`);
  } finally {
    setJianyingExporting(false);
  }
};
```

- [ ] **Step 2: 传递新 props 到 ExportScopeDialog**

找到 `<ExportScopeDialog` 的使用处，添加新 props：

```tsx
<ExportScopeDialog
  open={exportDialogOpen}
  onClose={() => setExportDialogOpen(false)}
  onSelect={handleExportProject}
  anchorRef={exportButtonRef}
  episodes={projectData?.episodes ?? []}
  onJianyingExport={handleJianyingExport}
  jianyingExporting={jianyingExporting}
/>
```

- [ ] **Step 3: 更新 `GlobalHeader.test.tsx` 中 ExportScopeDialog 的 mock**

在 `frontend/src/components/layout/GlobalHeader.test.tsx` 中，找到 `vi.mock("./ExportScopeDialog", ...)` 的 mock 定义，确保 mock 组件接受新增的 `episodes`、`onJianyingExport`、`jianyingExporting` props（即使不使用，也需避免类型报错）。

- [ ] **Step 4: 运行前端类型检查 + 测试**

Run: `cd frontend && pnpm check`
Expected: 无错误

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/layout/GlobalHeader.tsx frontend/src/components/layout/GlobalHeader.test.tsx
git commit -m "feat(jianying): integrate JianYing draft export in GlobalHeader"
```

---

## Task 8: 端到端验证

- [ ] **Step 1: 运行后端全部测试**

Run: `uv run python -m pytest tests/ -v --timeout=120`
Expected: 全部通过

- [ ] **Step 2: 运行前端全部检查**

Run: `cd frontend && pnpm check`
Expected: 全部通过

- [ ] **Step 3: 手动验证（如有测试项目）**

启动开发服务器：
```bash
uv run uvicorn server.app:app --reload --port 1241
cd frontend && pnpm dev
```

验证流程：
1. 打开工作台，点击"导出 ZIP"
2. 看到三个选项：仅当前版本 / 全部数据 / 导出为剪映草稿
3. 选择剪映草稿，弹出表单
4. 选集数，填草稿目录，点导出
5. 下载 ZIP，解压后验证 `draft_content.json` 路径正确

- [ ] **Step 4: 最终提交（如有遗漏修复）**

```bash
git add -A
git commit -m "chore(jianying): final fixes from e2e verification"
```
