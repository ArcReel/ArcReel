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


import subprocess


def _make_test_video(path: Path, duration_sec: float = 1.0, fps: int = 30):
    """使用 ffmpeg 生成一个极短的测试视频文件（64x64 像素）"""
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=black:size=64x64:duration={duration_sec}:rate={fps}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        check=True,
    )


class TestGenerateDraft:
    """测试 pyjianyingdraft 草稿生成"""

    def test_generates_draft_content_json(self, tmp_path):
        """生成的草稿目录包含 draft_content.json"""
        from server.services.jianying_draft_service import JianyingDraftService

        # 视频文件放在 draft_dir 外部，避免被 create_draft 清理
        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        _make_test_video(videos_dir / "scene_S1.mp4")
        _make_test_video(videos_dir / "scene_S2.mp4")

        draft_dir = tmp_path / "drafts" / "测试草稿"

        clips = [
            {"id": "S1", "local_path": str(videos_dir / "scene_S1.mp4"), "novel_text": ""},
            {"id": "S2", "local_path": str(videos_dir / "scene_S2.mp4"), "novel_text": ""},
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

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        _make_test_video(videos_dir / "seg_S1.mp4")

        draft_dir = tmp_path / "drafts" / "字幕草稿"

        clips = [
            {"id": "S1", "local_path": str(videos_dir / "seg_S1.mp4"), "novel_text": "从前有座山"},
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
        tracks = content.get("tracks", [])
        assert len(tracks) == 2

    def test_drama_mode_no_subtitle_track(self, tmp_path):
        """drama 模式不生成字幕轨"""
        from server.services.jianying_draft_service import JianyingDraftService

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        _make_test_video(videos_dir / "scene_S1.mp4")

        draft_dir = tmp_path / "drafts" / "无字幕草稿"

        clips = [
            {"id": "S1", "local_path": str(videos_dir / "scene_S1.mp4"), "novel_text": ""},
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
