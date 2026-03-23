"""剪映草稿导出服务的单元测试"""

import json
from pathlib import Path

import pytest


class TestCollectVideoClips:
    """测试从剧本中收集已完成视频片段"""

    def test_narration_mode_collects_existing_videos(self, tmp_path):
        """narration 模式：收集存在的 video_clip"""
        from server.services.jianying_draft_service import JianyingDraftService

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
        assert clips[0]["novel_text"] == ""

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
