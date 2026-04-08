"""Unit tests for the CapCut draft export service."""

import json
import zipfile

import pytest


class TestCollectVideoClips:
    """Test collecting completed video clips from scripts."""

    def test_narration_mode_collects_existing_videos(self, tmp_path):
        """Narration mode: collect existing video_clips."""
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
                    "novel_text": "Once upon a time there was a mountain",
                    "generated_assets": {"video_clip": "videos/segment_S1.mp4", "status": "completed"},
                },
                {
                    "segment_id": "S2",
                    "duration_seconds": 6,
                    "novel_text": "On the mountain there was a temple",
                    "generated_assets": {"video_clip": "videos/segment_S2.mp4", "status": "completed"},
                },
                {
                    "segment_id": "S3",
                    "duration_seconds": 8,
                    "novel_text": "In the temple lived an old monk",
                    "generated_assets": {"status": "pending"},
                },
            ],
        }

        svc = JianyingDraftService.__new__(JianyingDraftService)
        clips = svc._collect_video_clips(script, project_dir)

        assert len(clips) == 2
        assert clips[0]["id"] == "S1"
        assert clips[0]["novel_text"] == "Once upon a time there was a mountain"
        assert clips[1]["id"] == "S2"

    def test_drama_mode_collects_scenes(self, tmp_path):
        """Drama mode: collect scenes instead of segments."""
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
        """Skip entries recorded in the script but whose files do not exist."""
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
    """Test canvas size resolution."""

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


from tests.conftest import make_test_video


class TestGenerateDraft:
    """Test pyjianyingdraft draft generation."""

    def test_generates_draft_content_json(self, tmp_path):
        """The generated draft directory contains draft_content.json."""
        from server.services.jianying_draft_service import JianyingDraftService

        # Place video files outside draft_dir to avoid being cleaned up by create_draft
        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        make_test_video(videos_dir / "scene_S1.mp4")
        make_test_video(videos_dir / "scene_S2.mp4")

        draft_dir = tmp_path / "drafts" / "test-draft"

        clips = [
            {"id": "S1", "local_path": str(videos_dir / "scene_S1.mp4"), "novel_text": ""},
            {"id": "S2", "local_path": str(videos_dir / "scene_S2.mp4"), "novel_text": ""},
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="test-draft",
            clips=clips,
            width=1920,
            height=1080,
            content_mode="drama",
        )

        assert (draft_dir / "draft_content.json").exists()
        assert (draft_dir / "draft_meta_info.json").exists()

    def test_narration_mode_includes_subtitle_track(self, tmp_path):
        """Narration mode generates a subtitle track."""
        from server.services.jianying_draft_service import JianyingDraftService

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        make_test_video(videos_dir / "seg_S1.mp4")

        draft_dir = tmp_path / "drafts" / "subtitle-draft"

        clips = [
            {"id": "S1", "local_path": str(videos_dir / "seg_S1.mp4"), "novel_text": "Once upon a time there was a mountain"},
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="subtitle-draft",
            clips=clips,
            width=1080,
            height=1920,
            content_mode="narration",
        )

        content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        tracks = content.get("tracks", [])
        assert len(tracks) == 2

    def test_drama_mode_no_subtitle_track(self, tmp_path):
        """Drama mode does not generate a subtitle track."""
        from server.services.jianying_draft_service import JianyingDraftService

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        make_test_video(videos_dir / "scene_S1.mp4")

        draft_dir = tmp_path / "drafts" / "no-subtitle-draft"

        clips = [
            {"id": "S1", "local_path": str(videos_dir / "scene_S1.mp4"), "novel_text": ""},
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="no-subtitle-draft",
            clips=clips,
            width=1920,
            height=1080,
            content_mode="drama",
        )

        content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        tracks = content.get("tracks", [])
        assert len(tracks) == 1


class TestReplacePaths:
    """Test path post-processing (safe JSON replacement)."""

    def test_replaces_tmp_prefix_in_json(self, tmp_path):
        """Recursively replace the temporary path prefix in JSON."""
        from server.services.jianying_draft_service import JianyingDraftService

        json_path = tmp_path / "draft_content.json"
        data = {
            "materials": {
                "videos": [
                    {"path": "/tmp/arcreel_jy_abc/draft/assets/s1.mp4"},
                    {"path": "/tmp/arcreel_jy_abc/draft/assets/s2.mp4"},
                ]
            },
            "other": "no change",
        }
        json_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._replace_paths_in_draft(
            json_path=json_path,
            tmp_prefix="/tmp/arcreel_jy_abc/drafts/assets",
            target_prefix="/Users/test/Movies/JianyingPro/drafts/assets",
        )

        result = json.loads(json_path.read_text(encoding="utf-8"))
        assert result["materials"]["videos"][0]["path"] == "/Users/test/Movies/JianyingPro/drafts/assets/s1.mp4"
        assert result["materials"]["videos"][1]["path"] == "/Users/test/Movies/JianyingPro/drafts/assets/s2.mp4"
        assert result["other"] == "no change"


class TestExportEpisodeDraft:
    """End-to-end tests: complete export flow."""

    def _setup_project(self, tmp_path) -> tuple:
        """Create a test project with video clips."""
        from lib.project_manager import ProjectManager

        pm = ProjectManager(tmp_path / "projects")
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        videos_dir = project_dir / "videos"
        videos_dir.mkdir()

        make_test_video(videos_dir / "segment_S1.mp4")
        make_test_video(videos_dir / "segment_S2.mp4")

        project_data = {
            "title": "Test Project",
            "content_mode": "narration",
            "aspect_ratio": {"video": "9:16"},
            "episodes": [
                {"episode": 1, "title": "Episode 1", "script_file": "scripts/episode_1.json"},
            ],
        }
        (project_dir / "project.json").write_text(json.dumps(project_data, ensure_ascii=False), encoding="utf-8")

        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir()
        script_data = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "S1",
                    "duration_seconds": 8,
                    "novel_text": "Once upon a time there was a mountain",
                    "generated_assets": {"video_clip": "videos/segment_S1.mp4", "status": "completed"},
                },
                {
                    "segment_id": "S2",
                    "duration_seconds": 6,
                    "novel_text": "On the mountain there was a temple",
                    "generated_assets": {"video_clip": "videos/segment_S2.mp4", "status": "completed"},
                },
            ],
        }
        (scripts_dir / "episode_1.json").write_text(json.dumps(script_data, ensure_ascii=False), encoding="utf-8")

        return pm, project_dir

    def test_exports_zip_with_correct_structure(self, tmp_path):
        """Exported ZIP contains draft JSON and video assets."""
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
            assert any("draft_info.json" in n for n in names)
            assert any("draft_meta_info.json" in n for n in names)
            assert any("segment_S1.mp4" in n for n in names)
            assert any("segment_S2.mp4" in n for n in names)

    def test_draft_content_has_user_paths(self, tmp_path):
        """Paths in draft_info.json are replaced with the user's local paths."""
        from server.services.jianying_draft_service import JianyingDraftService

        pm, _ = self._setup_project(tmp_path)
        svc = JianyingDraftService(pm)
        draft_path = "/Users/test/drafts"

        zip_path = svc.export_episode_draft(project_name="demo", episode=1, draft_path=draft_path)

        with zipfile.ZipFile(zip_path) as zf:
            content_entry = [n for n in zf.namelist() if "draft_info.json" in n][0]
            content = json.loads(zf.read(content_entry).decode("utf-8"))
            raw = json.dumps(content)
            assert "/tmp/" not in raw and "\\Temp\\" not in raw
            assert draft_path in raw

    def test_episode_not_found_raises(self, tmp_path):
        """Raises FileNotFoundError when episode does not exist."""
        from server.services.jianying_draft_service import JianyingDraftService

        pm, _ = self._setup_project(tmp_path)
        svc = JianyingDraftService(pm)

        with pytest.raises(FileNotFoundError, match="Episode 99 does not exist"):
            svc.export_episode_draft(project_name="demo", episode=99, draft_path="/tmp")

    def test_no_videos_raises_value_error(self, tmp_path):
        """Raises ValueError when no completed videos are available."""
        from lib.project_manager import ProjectManager
        from server.services.jianying_draft_service import JianyingDraftService

        pm = ProjectManager(tmp_path / "projects")
        project_dir = tmp_path / "projects" / "empty"
        project_dir.mkdir(parents=True)

        (project_dir / "project.json").write_text(
            json.dumps(
                {
                    "title": "Empty Project",
                    "content_mode": "narration",
                    "episodes": [{"episode": 1, "title": "Episode 1", "script_file": "scripts/episode_1.json"}],
                },
                ensure_ascii=False,
            )
        )

        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "episode_1.json").write_text(
            json.dumps(
                {
                    "content_mode": "narration",
                    "segments": [
                        {
                            "segment_id": "S1",
                            "duration_seconds": 8,
                            "novel_text": "",
                            "generated_assets": {"status": "pending"},
                        },
                    ],
                },
                ensure_ascii=False,
            )
        )

        svc = JianyingDraftService(pm)
        with pytest.raises(ValueError, match="please generate videos first"):
            svc.export_episode_draft(project_name="empty", episode=1, draft_path="/tmp")
