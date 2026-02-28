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
            filename = filename[len("scripts/"):]
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

        draft = calc.calculate_episode_stats("demo", {"content_mode": "narration", "segments": [{"duration_seconds": 4}]})
        in_prod = calc.calculate_episode_stats(
            "demo",
            {
                "content_mode": "narration",
                "segments": [{"generated_assets": {"storyboard_image": "a.png"}, "duration_seconds": 6}],
            },
        )
        completed = calc.calculate_episode_stats(
            "demo",
            {
                "content_mode": "drama",
                "scenes": [{"generated_assets": {"video_clip": "a.mp4"}, "duration_seconds": 8}],
            },
        )

        assert draft["status"] == "draft"
        assert in_prod["status"] == "in_production"
        assert completed["status"] == "completed"

    def test_calculate_project_progress_and_phase(self, tmp_path):
        project_root = tmp_path / "projects"
        project_path = project_root / "demo"
        project_path.mkdir(parents=True)

        (project_path / "characters").mkdir(parents=True)
        (project_path / "clues").mkdir(parents=True)
        (project_path / "characters" / "A.png").write_bytes(b"ok")
        (project_path / "clues" / "C.png").write_bytes(b"ok")

        project = {
            "characters": {"A": {"character_sheet": "characters/A.png"}, "B": {"character_sheet": ""}},
            "clues": {
                "C": {"importance": "major", "clue_sheet": "clues/C.png"},
                "D": {"importance": "minor", "clue_sheet": ""},
            },
            "episodes": [
                {"script_file": "scripts/episode_1.json"},
                {"script_file": "scripts/missing.json"},
            ],
        }
        scripts = {
            "episode_1.json": {
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": 4,
                        "generated_assets": {
                            "storyboard_image": "storyboards/scene_E1S01.png",
                            "video_clip": "videos/scene_E1S01.mp4",
                        },
                    }
                ],
            }
        }

        calc = StatusCalculator(_FakePM(project_root, project, scripts))
        progress = calc.calculate_project_progress("demo")

        assert progress["characters"] == {"total": 2, "completed": 1}
        assert progress["clues"] == {"total": 1, "completed": 1}
        assert progress["storyboards"]["completed"] == 1
        assert progress["videos"]["completed"] == 1
        assert calc.calculate_current_phase(progress) == "compose"

    def test_enrich_project_and_enrich_script(self, tmp_path):
        project_root = tmp_path / "projects"
        project_root.mkdir(parents=True)
        project = {
            "episodes": [
                {"script_file": "scripts/episode_1.json"},
                {"script_file": "scripts/missing.json"},
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

        enriched_project = calc.enrich_project("demo", {**project})
        assert "status" in enriched_project
        assert enriched_project["episodes"][0]["scenes_count"] == 1
        assert enriched_project["episodes"][1]["status"] == "missing"

        enriched_script = calc.enrich_script({**script})
        assert enriched_script["metadata"]["total_scenes"] == 1
        assert enriched_script["metadata"]["estimated_duration_seconds"] == 6
        assert enriched_script["characters_in_episode"] == ["A", "B"]
        assert enriched_script["clues_in_episode"] == ["C"]
