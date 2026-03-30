from pathlib import Path

from lib.status_calculator import StatusCalculator


class _FakePM:
    def __init__(self, project_root: Path, project: dict, scripts: dict[str, dict]):
        self._project_root = project_root
        self._project = project
        self._scripts = scripts

    def load_project(self, project_name: str):
        return self._project

    def get_project_path(self, project_name: str):
        return self._project_root / project_name

    def load_script(self, project_name: str, filename: str):
        if filename.startswith("scripts/"):
            filename = filename[len("scripts/") :]
        if filename not in self._scripts:
            raise FileNotFoundError(filename)
        return self._scripts[filename]


class TestStatusCalculator:
    def test_select_content_mode_and_items(self):
        mode, items = StatusCalculator._select_content_mode_and_items(
            {"content_mode": "narration", "segments": [{"segment_id": "E1S01"}]}
        )
        assert mode == "narration"
        assert len(items) == 1

        mode2, items2 = StatusCalculator._select_content_mode_and_items({"scenes": [{"scene_id": "E1S01"}]})
        assert mode2 == "drama"
        assert len(items2) == 1

    def test_calculate_episode_stats_statuses(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))

        # draft：无任何资源
        draft = calc.calculate_episode_stats(
            "demo",
            {"content_mode": "narration", "segments": [{"duration_seconds": 4}]},
        )
        assert draft["status"] == "draft"
        assert draft["storyboards"] == {"total": 1, "completed": 0}
        assert draft["videos"] == {"total": 1, "completed": 0}
        assert draft["scenes_count"] == 1
        assert draft["duration_seconds"] == 4

        # in_production：有分镜图
        in_prod = calc.calculate_episode_stats(
            "demo",
            {
                "content_mode": "narration",
                "segments": [
                    {"generated_assets": {"storyboard_image": "a.png"}, "duration_seconds": 6},
                    {"duration_seconds": 4},
                ],
            },
        )
        assert in_prod["status"] == "in_production"
        assert in_prod["storyboards"] == {"total": 2, "completed": 1}
        assert in_prod["videos"] == {"total": 2, "completed": 0}

        # completed：所有场景有视频
        completed = calc.calculate_episode_stats(
            "demo",
            {
                "content_mode": "drama",
                "scenes": [
                    {"generated_assets": {"video_clip": "a.mp4"}, "duration_seconds": 8},
                ],
            },
        )
        assert completed["status"] == "completed"
        assert completed["storyboards"] == {"total": 1, "completed": 0}
        assert completed["videos"] == {"total": 1, "completed": 1}

    def test_load_episode_script(self, tmp_path):
        project_root = tmp_path / "projects"
        project_path = project_root / "demo"

        # Case 1: 脚本 JSON 存在 → ("generated", script)
        script_data = {"content_mode": "narration", "segments": []}
        scripts = {"episode_1.json": script_data}
        calc = StatusCalculator(_FakePM(project_root, {}, scripts))
        status, script = calc._load_episode_script("demo", 1, "scripts/episode_1.json")
        assert status == "generated"
        assert script == script_data

        # Case 2: 脚本不存在，draft 文件存在 → ("segmented", None)
        draft_dir = project_path / "drafts" / "episode_2"
        draft_dir.mkdir(parents=True)
        (draft_dir / "step1_segments.md").write_text("ok")
        calc2 = StatusCalculator(_FakePM(project_root, {}, {}))
        status2, script2 = calc2._load_episode_script("demo", 2, "scripts/episode_2.json")
        assert status2 == "segmented"
        assert script2 is None

        # Case 3: 两者都不存在 → ("none", None)
        calc3 = StatusCalculator(_FakePM(project_root, {}, {}))
        status3, script3 = calc3._load_episode_script("demo", 3, "scripts/episode_3.json")
        assert status3 == "none"
        assert script3 is None

    def test_calculate_current_phase_setup(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        project_no_overview = {}
        assert calc.calculate_current_phase(project_no_overview, []) == "setup"

    def test_calculate_current_phase_worldbuilding(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        project = {"overview": {"synopsis": "test"}}
        # 无任何 generated 脚本 → worldbuilding
        episodes_stats = [{"script_status": "none"}, {"script_status": "segmented"}]
        assert calc.calculate_current_phase(project, episodes_stats) == "worldbuilding"
        # 无集 → worldbuilding
        assert calc.calculate_current_phase(project, []) == "worldbuilding"

    def test_calculate_current_phase_scripting(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        project = {"overview": {"synopsis": "test"}}
        # 有至少一集 generated，但未全部 → scripting
        episodes_stats = [
            {"script_status": "generated", "status": "draft"},
            {"script_status": "none"},
        ]
        assert calc.calculate_current_phase(project, episodes_stats) == "scripting"

    def test_calculate_current_phase_production_and_completed(self, tmp_path):
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        project = {"overview": {"synopsis": "test"}}
        # 全部 generated，有未完成视频 → production
        episodes_stats = [
            {"script_status": "generated", "status": "in_production"},
            {"script_status": "generated", "status": "draft"},
        ]
        assert calc.calculate_current_phase(project, episodes_stats) == "production"
        # 全部 completed → completed
        episodes_stats_done = [
            {"script_status": "generated", "status": "completed"},
        ]
        assert calc.calculate_current_phase(project, episodes_stats_done) == "completed"

    def test_calculate_project_status(self, tmp_path):
        project_root = tmp_path / "projects"
        project_path = project_root / "demo"
        (project_path / "characters").mkdir(parents=True)
        (project_path / "clues").mkdir(parents=True)
        (project_path / "characters" / "A.png").write_bytes(b"ok")
        (project_path / "clues" / "C.png").write_bytes(b"ok")

        project = {
            "overview": {"synopsis": "test"},
            "characters": {"A": {"character_sheet": "characters/A.png"}, "B": {"character_sheet": ""}},
            "clues": {
                "C": {"importance": "major", "clue_sheet": "clues/C.png"},
                "D": {"importance": "minor", "clue_sheet": ""},
            },
            "episodes": [
                {"episode": 1, "script_file": "scripts/episode_1.json"},
            ],
        }
        scripts = {
            "episode_1.json": {
                "content_mode": "narration",
                "segments": [
                    {"duration_seconds": 4, "generated_assets": {"storyboard_image": "a.png", "video_clip": "b.mp4"}},
                ],
            }
        }
        calc = StatusCalculator(_FakePM(project_root, project, scripts))
        status = calc.calculate_project_status("demo", project)

        assert status["current_phase"] == "completed"
        assert status["phase_progress"] == 1.0
        assert status["characters"] == {"total": 2, "completed": 1}
        assert status["clues"] == {"total": 2, "completed": 1}
        assert status["episodes_summary"] == {"total": 1, "scripted": 1, "in_production": 0, "completed": 1}

    def test_enrich_project(self, tmp_path):
        project_root = tmp_path / "projects"
        project_root.mkdir(parents=True)
        project = {
            "overview": {"synopsis": "test"},
            "episodes": [
                {"episode": 1, "script_file": "scripts/episode_1.json"},
                {"episode": 2, "script_file": "scripts/missing.json"},
            ],
            "characters": {},
            "clues": {},
        }
        script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 6,
                    "characters_in_segment": ["A", "B"],
                    "clues_in_segment": ["C"],
                    "generated_assets": {},
                }
            ],
        }
        calc = StatusCalculator(_FakePM(project_root, project, {"episode_1.json": script}))

        enriched = calc.enrich_project(
            "demo",
            {
                **project,
                "episodes": [
                    {"episode": 1, "script_file": "scripts/episode_1.json"},
                    {"episode": 2, "script_file": "scripts/missing.json"},
                ],
            },
        )

        assert "status" in enriched
        assert enriched["status"]["current_phase"] == "scripting"
        ep1 = enriched["episodes"][0]
        assert ep1["script_status"] == "generated"
        assert ep1["status"] == "scripted"
        assert ep1["scenes_count"] == 1
        assert ep1["storyboards"] == {"total": 1, "completed": 0}
        ep2 = enriched["episodes"][1]
        assert ep2["script_status"] == "none"
        assert ep2["status"] == "draft"

    def test_enrich_script(self, tmp_path):
        script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 6,
                    "characters_in_segment": ["A", "B"],
                    "clues_in_segment": ["C"],
                    "generated_assets": {},
                }
            ],
        }
        calc = StatusCalculator(_FakePM(tmp_path, {}, {}))
        enriched_script = calc.enrich_script({**script})
        assert enriched_script["metadata"]["total_scenes"] == 1
        assert enriched_script["metadata"]["estimated_duration_seconds"] == 6
        assert enriched_script["characters_in_episode"] == ["A", "B"]
        assert enriched_script["clues_in_episode"] == ["C"]

    def test_load_episode_script_corrupted_json(self, tmp_path):
        """JSON 损坏时应降级返回 ('generated', None)，而不是上抛异常。"""
        import json

        class _CorruptPM(_FakePM):
            def load_script(self, project_name, filename):
                raise json.JSONDecodeError("Expecting value", "doc", 0)

        calc = StatusCalculator(_CorruptPM(tmp_path / "projects", {}, {}))
        status, script = calc._load_episode_script("demo", 1, "scripts/episode_1.json")
        assert status == "generated"
        assert script is None
