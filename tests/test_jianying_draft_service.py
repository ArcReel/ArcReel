"""剪映草稿导出服务的单元测试"""

import json
import zipfile

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


class TestCollectNarrationAudio:
    """测试从剧本中收集旁白音频路径"""

    def _make_script(self, narration_audio: str | None) -> dict:
        assets: dict = {"video_clip": "videos/segment_S1.mp4", "status": "completed"}
        if narration_audio is not None:
            assets["narration_audio"] = narration_audio
        return {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "S1",
                    "duration_seconds": 8,
                    "novel_text": "从前有座山",
                    "generated_assets": assets,
                },
            ],
        }

    def test_collects_existing_narration_audio(self, tmp_path):
        """段含 narration_audio 且文件存在时收集其绝对路径"""
        from server.services.jianying_draft_service import JianyingDraftService

        project_dir = tmp_path / "projects" / "demo"
        (project_dir / "videos").mkdir(parents=True)
        (project_dir / "videos" / "segment_S1.mp4").write_bytes(b"fake")
        (project_dir / "audio").mkdir()
        (project_dir / "audio" / "segment_S1.wav").write_bytes(b"fake")

        svc = JianyingDraftService.__new__(JianyingDraftService)
        clips = svc._collect_video_clips(self._make_script("audio/segment_S1.wav"), project_dir)

        assert len(clips) == 1
        assert clips[0]["narration_audio_abs"] == (project_dir / "audio" / "segment_S1.wav").resolve()

    def test_segment_without_narration_audio_yields_none(self, tmp_path):
        """缺 narration_audio 的段不报错，音频路径为 None"""
        from server.services.jianying_draft_service import JianyingDraftService

        project_dir = tmp_path / "projects" / "demo"
        (project_dir / "videos").mkdir(parents=True)
        (project_dir / "videos" / "segment_S1.mp4").write_bytes(b"fake")

        svc = JianyingDraftService.__new__(JianyingDraftService)
        clips = svc._collect_video_clips(self._make_script(None), project_dir)

        assert len(clips) == 1
        assert clips[0]["narration_audio_abs"] is None

    def test_missing_audio_file_yields_none(self, tmp_path):
        """narration_audio 有记录但文件不存在时视同缺失"""
        from server.services.jianying_draft_service import JianyingDraftService

        project_dir = tmp_path / "projects" / "demo"
        (project_dir / "videos").mkdir(parents=True)
        (project_dir / "videos" / "segment_S1.mp4").write_bytes(b"fake")

        svc = JianyingDraftService.__new__(JianyingDraftService)
        clips = svc._collect_video_clips(self._make_script("audio/segment_S1.wav"), project_dir)

        assert len(clips) == 1
        assert clips[0]["narration_audio_abs"] is None

    def test_path_traversal_audio_yields_none(self, tmp_path):
        """narration_audio 路径越界时视同缺失，视频照常导出"""
        from server.services.jianying_draft_service import JianyingDraftService

        project_dir = tmp_path / "projects" / "demo"
        (project_dir / "videos").mkdir(parents=True)
        (project_dir / "videos" / "segment_S1.mp4").write_bytes(b"fake")
        (tmp_path / "secret.wav").write_bytes(b"fake")

        svc = JianyingDraftService.__new__(JianyingDraftService)
        clips = svc._collect_video_clips(self._make_script("../../secret.wav"), project_dir)

        assert len(clips) == 1
        assert clips[0]["narration_audio_abs"] is None


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


from tests.conftest import make_test_audio, make_test_video


class TestGenerateDraft:
    """测试 pyjianyingdraft 草稿生成"""

    def test_generates_draft_content_json(self, tmp_path):
        """生成的草稿目录包含 draft_content.json"""
        from server.services.jianying_draft_service import JianyingDraftService

        # 视频文件放在 draft_dir 外部，避免被 create_draft 清理
        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        make_test_video(videos_dir / "scene_S1.mp4")
        make_test_video(videos_dir / "scene_S2.mp4")

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
        make_test_video(videos_dir / "seg_S1.mp4")

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
        make_test_video(videos_dir / "scene_S1.mp4")

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


class TestNarrationAudioTrack:
    """测试逐段旁白音轨在剪映草稿中的接入"""

    def test_clip_with_narration_audio_adds_audio_segment_at_video_offset(self, tmp_path):
        """段含旁白音频时，草稿含音频轨，音频段按视频段 offset 摆放"""
        from server.services.jianying_draft_service import JianyingDraftService

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        make_test_video(videos_dir / "seg_S1.mp4")
        make_test_video(videos_dir / "seg_S2.mp4")
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        make_test_audio(audio_dir / "segment_S2.wav")

        draft_dir = tmp_path / "drafts" / "旁白草稿"
        clips = [
            {"id": "S1", "local_path": str(videos_dir / "seg_S1.mp4"), "novel_text": ""},
            {
                "id": "S2",
                "local_path": str(videos_dir / "seg_S2.mp4"),
                "novel_text": "",
                "narration_audio_local": str(audio_dir / "segment_S2.wav"),
            },
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="旁白草稿",
            clips=clips,
            width=1080,
            height=1920,
            content_mode="drama",
        )

        content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        audios = content.get("materials", {}).get("audios", [])
        assert len(audios) == 1
        assert audios[0]["path"].endswith("segment_S2.wav")

        audio_tracks = [t for t in content.get("tracks", []) if t.get("type") == "audio"]
        assert len(audio_tracks) == 1
        segments = audio_tracks[0]["segments"]
        assert len(segments) == 1

        # 音频段 offset 与第二个视频段一致（即第一个视频的实际时长）
        video_track = next(t for t in content["tracks"] if t.get("type") == "video")
        second_video_start = video_track["segments"][1]["target_timerange"]["start"]
        assert segments[0]["target_timerange"]["start"] == second_video_start

    def test_audio_duration_follows_audio_file_not_video(self, tmp_path):
        """音频段时长取音频文件真实时长，不与视频段对齐"""
        from server.services.jianying_draft_service import JianyingDraftService

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        make_test_video(videos_dir / "seg_S1.mp4", duration_sec=1.0)
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        make_test_audio(audio_dir / "segment_S1.wav", duration_sec=2.0)

        draft_dir = tmp_path / "drafts" / "时长草稿"
        clips = [
            {
                "id": "S1",
                "local_path": str(videos_dir / "seg_S1.mp4"),
                "novel_text": "",
                "narration_audio_local": str(audio_dir / "segment_S1.wav"),
            },
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="时长草稿",
            clips=clips,
            width=1080,
            height=1920,
            content_mode="narration",
        )

        content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        audio_track = next(t for t in content["tracks"] if t.get("type") == "audio")
        audio_duration_us = audio_track["segments"][0]["target_timerange"]["duration"]
        video_track = next(t for t in content["tracks"] if t.get("type") == "video")
        video_duration_us = video_track["segments"][0]["target_timerange"]["duration"]

        # 音频约 2s，明显长于 1s 视频
        assert abs(audio_duration_us - 2_000_000) < 200_000
        assert audio_duration_us > video_duration_us

    def test_unparseable_audio_skipped_without_error(self, tmp_path):
        """音频文件存在但无法解析（如截断/空文件）时跳过该段配音，导出不报错"""
        from server.services.jianying_draft_service import JianyingDraftService

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        make_test_video(videos_dir / "seg_S1.mp4")
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        (audio_dir / "segment_S1.wav").write_bytes(b"not real audio")

        draft_dir = tmp_path / "drafts" / "坏音频草稿"
        clips = [
            {
                "id": "S1",
                "local_path": str(videos_dir / "seg_S1.mp4"),
                "novel_text": "",
                "narration_audio_local": str(audio_dir / "segment_S1.wav"),
            },
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="坏音频草稿",
            clips=clips,
            width=1080,
            height=1920,
            content_mode="narration",
        )

        content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        assert content.get("materials", {}).get("audios", []) == []
        assert all(t.get("type") != "audio" for t in content.get("tracks", []))

    def test_audio_open_failure_skipped_without_error(self, tmp_path, monkeypatch):
        """音频文件解析阶段抛出运行时错误（如被占用）时跳过该段配音，导出不报错"""
        from server.services.jianying_draft_service import JianyingDraftService

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        make_test_video(videos_dir / "seg_S1.mp4")
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        make_test_audio(audio_dir / "segment_S1.wav")

        def raise_runtime_error(*args, **kwargs):
            raise RuntimeError("An error occurred while opening the file")

        monkeypatch.setattr("server.services.jianying_draft_service.AudioMaterial", raise_runtime_error)

        draft_dir = tmp_path / "drafts" / "占用草稿"
        clips = [
            {
                "id": "S1",
                "local_path": str(videos_dir / "seg_S1.mp4"),
                "novel_text": "",
                "narration_audio_local": str(audio_dir / "segment_S1.wav"),
            },
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="占用草稿",
            clips=clips,
            width=1080,
            height=1920,
            content_mode="narration",
        )

        content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        assert content.get("materials", {}).get("audios", []) == []

    def test_zero_duration_audio_skipped_without_error(self, tmp_path, monkeypatch):
        """音频有效时长为 0（解析异常或被收口到 0）时跳过该段配音，导出不报错"""
        from server.services.jianying_draft_service import JianyingDraftService

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        make_test_video(videos_dir / "seg_S1.mp4")
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        make_test_audio(audio_dir / "segment_S1.wav")

        class ZeroDurationAudioMaterial:
            def __init__(self, path):
                self.path = path
                self.duration = 0

        monkeypatch.setattr("server.services.jianying_draft_service.AudioMaterial", ZeroDurationAudioMaterial)

        draft_dir = tmp_path / "drafts" / "零时长草稿"
        clips = [
            {
                "id": "S1",
                "local_path": str(videos_dir / "seg_S1.mp4"),
                "novel_text": "",
                "narration_audio_local": str(audio_dir / "segment_S1.wav"),
            },
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="零时长草稿",
            clips=clips,
            width=1080,
            height=1920,
            content_mode="narration",
        )

        content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        assert content.get("materials", {}).get("audios", []) == []
        assert all(t.get("type") != "audio" for t in content.get("tracks", []))

    def test_overlong_audio_clamped_to_next_narration_start(self, tmp_path):
        """前段音频长过下一段音频的起点时收口到起点，导出不报错"""
        from server.services.jianying_draft_service import JianyingDraftService

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        make_test_video(videos_dir / "seg_S1.mp4", duration_sec=1.0)
        make_test_video(videos_dir / "seg_S2.mp4", duration_sec=1.0)
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        make_test_audio(audio_dir / "segment_S1.wav", duration_sec=3.0)
        make_test_audio(audio_dir / "segment_S2.wav", duration_sec=1.0)

        draft_dir = tmp_path / "drafts" / "超长草稿"
        clips = [
            {
                "id": "S1",
                "local_path": str(videos_dir / "seg_S1.mp4"),
                "novel_text": "",
                "narration_audio_local": str(audio_dir / "segment_S1.wav"),
            },
            {
                "id": "S2",
                "local_path": str(videos_dir / "seg_S2.mp4"),
                "novel_text": "",
                "narration_audio_local": str(audio_dir / "segment_S2.wav"),
            },
        ]

        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="超长草稿",
            clips=clips,
            width=1080,
            height=1920,
            content_mode="narration",
        )

        content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
        audio_track = next(t for t in content["tracks"] if t.get("type") == "audio")
        segments = audio_track["segments"]
        assert len(segments) == 2

        first, second = segments[0], segments[1]
        # 第一段收口到第二段音频起点，互不重叠
        assert (
            first["target_timerange"]["start"] + first["target_timerange"]["duration"]
            <= second["target_timerange"]["start"]
        )
        # 第二段保持真实时长（约 1s）
        assert abs(second["target_timerange"]["duration"] - 1_000_000) < 200_000


class TestTransitions:
    """测试 transition_to_next 字段在剪映草稿中的实际接入"""

    def _generate_with_transitions(self, tmp_path, transitions: list[str]) -> dict:
        from server.services.jianying_draft_service import JianyingDraftService

        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        clips = []
        for i, t in enumerate(transitions):
            path = videos_dir / f"scene_S{i + 1}.mp4"
            make_test_video(path)
            clips.append({"id": f"S{i + 1}", "local_path": str(path), "novel_text": "", "transition_to_next": t})

        draft_dir = tmp_path / "drafts" / "转场草稿"
        svc = JianyingDraftService.__new__(JianyingDraftService)
        svc._generate_draft(
            draft_dir=draft_dir,
            draft_name="转场草稿",
            clips=clips,
            width=1920,
            height=1080,
            content_mode="drama",
        )
        return json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))

    def test_cut_does_not_attach_transition(self, tmp_path):
        content = self._generate_with_transitions(tmp_path, ["cut", "cut"])
        assert content.get("materials", {}).get("transitions", []) == []

    def test_fade_attaches_transition_material(self, tmp_path):
        content = self._generate_with_transitions(tmp_path, ["fade", "cut"])
        transitions = content.get("materials", {}).get("transitions", [])
        assert len(transitions) == 1
        # 闪黑 在 transition_meta 中的 effect_id
        assert transitions[0].get("effect_id") == "321493"

    def test_dissolve_attaches_transition_material(self, tmp_path):
        content = self._generate_with_transitions(tmp_path, ["dissolve", "cut"])
        transitions = content.get("materials", {}).get("transitions", [])
        assert len(transitions) == 1
        # 叠化 effect_id
        assert transitions[0].get("effect_id") == "322577"

    def test_last_segment_transition_ignored(self, tmp_path):
        # 最后一段即使字段非 cut 也不能挂（剪映约定挂在前段）
        content = self._generate_with_transitions(tmp_path, ["cut", "fade"])
        assert content.get("materials", {}).get("transitions", []) == []

    def test_collect_video_clips_includes_transition_field(self, tmp_path):
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
                    "duration_seconds": 6,
                    "transition_to_next": "fade",
                    "generated_assets": {"video_clip": "videos/scene_E1S01.mp4", "status": "completed"},
                },
            ],
        }
        svc = JianyingDraftService.__new__(JianyingDraftService)
        clips = svc._collect_video_clips(script, project_dir)
        assert clips[0]["transition_to_next"] == "fade"


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


class TestExportEpisodeDraft:
    """端到端测试：完整导出流程"""

    def _setup_project(self, tmp_path) -> tuple:
        """创建带视频片段的测试项目"""
        from lib.project_manager import ProjectManager

        pm = ProjectManager(tmp_path / "projects")
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        videos_dir = project_dir / "videos"
        videos_dir.mkdir()

        make_test_video(videos_dir / "segment_S1.mp4")
        make_test_video(videos_dir / "segment_S2.mp4")

        project_data = {
            "title": "测试项目",
            "content_mode": "narration",
            "aspect_ratio": {"video": "9:16"},
            "episodes": [
                {"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"},
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
        (scripts_dir / "episode_1.json").write_text(json.dumps(script_data, ensure_ascii=False), encoding="utf-8")

        return pm, project_dir

    def _add_narration_audio(self, project_dir, segment_id: str) -> None:
        """为指定段补充旁白音频文件并写回剧本"""
        audio_dir = project_dir / "audio"
        audio_dir.mkdir(exist_ok=True)
        make_test_audio(audio_dir / f"segment_{segment_id}.wav")

        script_path = project_dir / "scripts" / "episode_1.json"
        script_data = json.loads(script_path.read_text(encoding="utf-8"))
        for segment in script_data["segments"]:
            if segment["segment_id"] == segment_id:
                segment["generated_assets"]["narration_audio"] = f"audio/segment_{segment_id}.wav"
        script_path.write_text(json.dumps(script_data, ensure_ascii=False), encoding="utf-8")

    def test_export_with_narration_audio_includes_audio_track(self, tmp_path):
        """含 narration_audio 的项目导出后，ZIP 带音频素材，草稿含旁白音轨且路径已替换"""
        from server.services.jianying_draft_service import JianyingDraftService

        pm, project_dir = self._setup_project(tmp_path)
        self._add_narration_audio(project_dir, "S1")  # S2 缺 narration_audio，应跳过不报错
        svc = JianyingDraftService(pm)
        draft_path = "/mock/JianyingDrafts"

        zip_path = svc.export_episode_draft(project_name="demo", episode=1, draft_path=draft_path)

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert any("segment_S1.wav" in n for n in names)

            content_entry = [n for n in zf.namelist() if "draft_info.json" in n][0]
            content = json.loads(zf.read(content_entry).decode("utf-8"))

        audios = content["materials"]["audios"]
        assert len(audios) == 1
        assert audios[0]["path"].startswith(draft_path)
        assert audios[0]["path"].endswith("segment_S1.wav")

        audio_track = next(t for t in content["tracks"] if t.get("type") == "audio")
        assert len(audio_track["segments"]) == 1
        assert audio_track["segments"][0]["target_timerange"]["start"] == 0

    def test_stage_rejects_source_replaced_outside_project(self, tmp_path, monkeypatch):
        """收集后源路径被替换为项目外目标时，暂存前重校验拒绝导出"""
        from server.services.jianying_draft_service import JianyingDraftService

        pm, _ = self._setup_project(tmp_path)
        outside = tmp_path / "outside.mp4"
        make_test_video(outside)
        svc = JianyingDraftService(pm)
        original = svc._collect_video_clips

        def tampered(script_data, project_dir):
            clips = original(script_data, project_dir)
            clips[0]["abs_path"] = outside
            return clips

        monkeypatch.setattr(svc, "_collect_video_clips", tampered)

        with pytest.raises(ValueError, match="路径越界"):
            svc.export_episode_draft(project_name="demo", episode=1, draft_path="/mock/JianyingDrafts")

    def test_segments_sharing_one_audio_file_export_once(self, tmp_path):
        """多段共享同一旁白音频文件时导出成功，素材只打包一份"""
        from server.services.jianying_draft_service import JianyingDraftService

        pm, project_dir = self._setup_project(tmp_path)
        audio_dir = project_dir / "audio"
        audio_dir.mkdir()
        make_test_audio(audio_dir / "shared.wav", duration_sec=0.5)

        script_path = project_dir / "scripts" / "episode_1.json"
        script_data = json.loads(script_path.read_text(encoding="utf-8"))
        for segment in script_data["segments"]:
            segment["generated_assets"]["narration_audio"] = "audio/shared.wav"
        script_path.write_text(json.dumps(script_data, ensure_ascii=False), encoding="utf-8")

        svc = JianyingDraftService(pm)
        zip_path = svc.export_episode_draft(project_name="demo", episode=1, draft_path="/mock/JianyingDrafts")

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert sum(1 for n in names if "shared" in n and n.endswith(".wav")) == 1

            content_entry = [n for n in names if "draft_info.json" in n][0]
            content = json.loads(zf.read(content_entry).decode("utf-8"))

        audio_track = next(t for t in content["tracks"] if t.get("type") == "audio")
        assert len(audio_track["segments"]) == 2

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
            assert any("draft_info.json" in n for n in names)
            assert any("draft_meta_info.json" in n for n in names)
            assert any("segment_S1.mp4" in n for n in names)
            assert any("segment_S2.mp4" in n for n in names)

    def test_draft_content_has_user_paths(self, tmp_path):
        """draft_info.json 中的路径已替换为用户本地路径"""
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
        project_dir = tmp_path / "projects" / "empty"
        project_dir.mkdir(parents=True)

        (project_dir / "project.json").write_text(
            json.dumps(
                {
                    "title": "空项目",
                    "content_mode": "narration",
                    "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
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
        with pytest.raises(ValueError, match="请先生成视频"):
            svc.export_episode_draft(project_name="empty", episode=1, draft_path="/tmp")
