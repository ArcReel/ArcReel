import json
import warnings
from pathlib import Path

import pytest

from lib.project_manager import ProjectManager


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _FakeTextBackend:
    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake-model"

    @property
    def capabilities(self):
        return set()

    async def generate(self, request):
        from lib.text_backends.base import TextGenerationResult

        return TextGenerationResult(
            text=json.dumps(
                {
                    "synopsis": "故事梗概",
                    "genre": "悬疑",
                    "theme": "真相",
                    "world_setting": "古代",
                },
                ensure_ascii=False,
            ),
            provider="fake",
            model="fake-model",
        )


class TestProjectManagerMore:
    def test_project_and_status_lifecycle(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        _write(project_dir / "source" / "a.txt", "source")
        _write(project_dir / "scripts" / "episode_1.json", "{}")
        _write(project_dir / "characters" / "alice.png", "x")
        _write(project_dir / "clues" / "clue.png", "x")
        _write(project_dir / "storyboards" / "scene_1.png", "x")
        _write(project_dir / "videos" / "scene_1.mp4", "x")
        _write(project_dir / "output" / "final.mp4", "x")

        assert "demo" in pm.list_projects()
        status = pm.get_project_status("demo")
        assert status["current_stage"] == "completed"
        assert status["source_files"] == ["a.txt"]

        assert pm.project_exists("demo")
        loaded = pm.load_project("demo")
        assert loaded["title"] == "Demo"

        loaded["style"] = "Noir"
        pm.save_project("demo", loaded)
        assert pm.load_project("demo")["style"] == "Noir"

    def test_project_identifier_validation_and_title_fallback(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")

        with pytest.raises(ValueError):
            pm.create_project("bad name")
        with pytest.raises(ValueError):
            pm.create_project("bad_name")

        pm.create_project("demo")
        project = pm.create_project_metadata("demo", "")

        assert project["title"] == "demo"

    def test_generate_project_name_is_unique_and_safe(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")

        first = pm.generate_project_name("My Demo Project")
        second = pm.generate_project_name("我的项目")

        assert first.startswith("my-demo-project-")
        assert second.startswith("project-")
        assert first != second
        assert pm.normalize_project_name(first) == first
        assert pm.normalize_project_name(second) == second

    def test_script_operations_and_scene_updates(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        script = {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
        }
        path = pm.save_script("demo", script, "episode_1.json")
        assert path.name == "episode_1.json"

        loaded = pm.load_script("demo", "episode_1.json")
        assert loaded["metadata"]["total_scenes"] == 1
        assert loaded["metadata"]["estimated_duration_seconds"] == 4
        assert pm.list_scripts("demo") == ["episode_1.json"]

        synced = pm.sync_episode_from_script("demo", "episode_1.json")
        assert synced["episodes"][0]["episode"] == 1

        # add_scene (drama format)
        drama_script = {
            "episode": 2,
            "title": "第二集",
            "content_mode": "drama",
            "scenes": [],
        }
        pm.save_script("demo", drama_script, "episode_2.json")
        pm.add_scene("demo", "episode_2.json", {"duration_seconds": 8, "generated_assets": {}})
        loaded_drama = pm.load_script("demo", "episode_2.json")
        assert loaded_drama["scenes"][0]["scene_id"] == "001"

        # update_scene_asset + pending helpers
        narration_script = pm.load_script("demo", "episode_1.json")
        narration_script["segments"][0]["generated_assets"] = {}
        pm.save_script("demo", narration_script, "episode_1.json")

        pm.update_scene_asset(
            "demo",
            "episode_1.json",
            "E1S01",
            "storyboard_image",
            "storyboards/scene_E1S01.png",
        )
        updated = pm.load_script("demo", "episode_1.json")
        assert updated["segments"][0]["generated_assets"]["status"] == "storyboard_ready"

        pending_video = pm.get_pending_scenes("demo", "episode_1.json", "video_clip")
        assert len(pending_video) == 1

        # get_scenes_needing_storyboard
        drama = pm.load_script("demo", "episode_2.json")
        drama["scenes"][0]["generated_assets"] = {"storyboard_image": None}
        pm.save_script("demo", drama, "episode_2.json")
        assert len(pm.get_scenes_needing_storyboard("demo", "episode_2.json")) == 1

        with pytest.raises(KeyError):
            pm.update_scene_asset("demo", "episode_1.json", "NOT_FOUND", "video_clip", "x.mp4")

    def test_load_script_strips_scripts_prefix(self, tmp_path):
        """load_script / save_script / update_scene_asset 应兼容带 scripts/ 前缀的文件名"""
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        script = {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4, "generated_assets": {}}],
        }
        pm.save_script("demo", script, "episode_1.json")

        # 纯文件名
        loaded1 = pm.load_script("demo", "episode_1.json")
        assert loaded1["episode"] == 1

        # 带 scripts/ 前缀（前端传入的格式）
        loaded2 = pm.load_script("demo", "scripts/episode_1.json")
        assert loaded2["episode"] == 1

        # save_script 也应兼容带前缀的文件名
        script["title"] = "修改后"
        pm.save_script("demo", script, "scripts/episode_1.json")
        loaded3 = pm.load_script("demo", "episode_1.json")
        assert loaded3["title"] == "修改后"

        # update_scene_asset 也应兼容
        pm.update_scene_asset(
            "demo", "scripts/episode_1.json", "E1S01", "storyboard_image", "storyboards/scene_E1S01.png"
        )
        updated = pm.load_script("demo", "episode_1.json")
        assert updated["segments"][0]["generated_assets"]["storyboard_image"] == "storyboards/scene_E1S01.png"

    def test_normalize_and_templates(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "drama")

        scene = {"scene_id": "S1", "generated_assets": {}}
        normalized = pm.normalize_scene(scene, episode=3)
        assert normalized["episode"] == 3
        assert normalized["generated_assets"]["status"] == "pending"

        assert pm.update_scene_status({"generated_assets": {"video_clip": "v.mp4"}}) == "completed"
        assert pm.update_scene_status({"generated_assets": {"storyboard_image": "s.png"}}) == "storyboard_ready"
        assert pm.update_scene_status({"generated_assets": {}}) == "pending"

        raw_script = {
            "novel": {"chapter": "chapter"},
            "scenes": [{"scene_id": "001"}],
            "characters": {"A": {"description": "desc"}},
            "clues": {"C": {"type": "prop", "description": "d", "importance": "major"}},
        }
        _write(tmp_path / "projects" / "demo" / "scripts" / "legacy.json", json.dumps(raw_script, ensure_ascii=False))

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(pm, "sync_characters_from_script", lambda *args, **kwargs: None, raising=False)
        monkeypatch.setattr(pm, "sync_clues_from_script", lambda *args, **kwargs: None, raising=False)
        normalized_script = pm.normalize_script("demo", "legacy.json", save=False)
        monkeypatch.undo()

        assert "metadata" in normalized_script
        assert normalized_script["duration_seconds"] >= 0

    def test_entity_and_batch_management_and_paths(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")

        pm.add_project_character("demo", "Alice", "hero", "soft")
        pm.update_project_character_sheet("demo", "Alice", "characters/Alice.png")
        pm.update_character_reference_image("demo", "Alice", "characters/refs/Alice.png")
        assert pm.get_project_character("demo", "Alice")["reference_image"].endswith("Alice.png")

        pm.add_clues_batch("demo", {"玉佩": {"type": "prop", "description": "d", "importance": "major"}})
        pm.update_clue_sheet("demo", "玉佩", "clues/玉佩.png")
        assert pm.get_clue("demo", "玉佩")["clue_sheet"].endswith("玉佩.png")

        project_dir = pm.get_project_path("demo")
        (project_dir / "clues" / "玉佩.png").write_bytes(b"png")
        assert pm.get_pending_clues("demo") == []

        # direct add_* return bool
        assert pm.add_character("demo", "Bob", "side", "") is True
        assert pm.add_character("demo", "Bob", "side", "") is False
        assert pm.add_clue("demo", "线索X", "prop", "desc", "minor") is True
        assert pm.add_clue("demo", "线索X", "prop", "desc", "minor") is False

        added_chars = pm.add_characters_batch("demo", {"Bob": {"description": "d"}, "C": {"description": "d"}})
        assert added_chars == 1
        added_clues = pm.add_clues_batch("demo", {"线索X": {"type": "prop"}, "线索Y": {"type": "location"}})
        assert added_clues == 1

        pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
        pm.add_episode("demo", 1, "第一集-改", "scripts/episode_1.json")
        assert pm.load_project("demo")["episodes"][0]["title"].startswith("第一集")

        assert str(pm.get_source_path("demo", "a.txt")).endswith("/source/a.txt")
        assert str(pm.get_character_path("demo", "a.png")).endswith("/characters/a.png")
        assert str(pm.get_storyboard_path("demo", "a.png")).endswith("/storyboards/a.png")
        assert str(pm.get_video_path("demo", "a.mp4")).endswith("/videos/a.mp4")
        assert str(pm.get_output_path("demo", "a.mp4")).endswith("/output/a.mp4")
        assert str(pm.get_clue_path("demo", "a.png")).endswith("/clues/a.png")

        with pytest.raises(KeyError):
            pm.get_project_character("demo", "none")
        with pytest.raises(KeyError):
            pm.update_project_character_sheet("demo", "none", "x")
        with pytest.raises(KeyError):
            pm.update_character_reference_image("demo", "none", "x")
        with pytest.raises(KeyError):
            pm.get_clue("demo", "none")
        with pytest.raises(KeyError):
            pm.update_clue_sheet("demo", "none", "x")

    @pytest.mark.asyncio
    async def test_reference_read_source_and_generate_overview(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")

        pm.add_character("demo", "Alice", "hero")
        pm.add_clues_batch("demo", {"玉佩": {"type": "prop", "description": "d", "importance": "major"}})

        project_dir = pm.get_project_path("demo")
        (project_dir / "characters" / "Alice.png").write_bytes(b"png")
        (project_dir / "clues" / "玉佩.png").write_bytes(b"png")

        project = pm.load_project("demo")
        project["characters"]["Alice"]["character_sheet"] = "characters/Alice.png"
        project["clues"]["玉佩"]["clue_sheet"] = "clues/玉佩.png"
        pm.save_project("demo", project)

        refs = pm.collect_reference_images(
            "demo",
            {"characters_in_scene": ["Alice"], "clues_in_scene": ["玉佩"]},
        )
        assert len(refs) == 2

        _write(project_dir / "source" / "1.txt", "a" * 10)
        _write(project_dir / "source" / "2.md", "b" * 10)
        _write(project_dir / "source" / "3.bin", "ignored")
        content = pm._read_source_files("demo", max_chars=15)
        assert "1.txt" in content

        async def _fake_create_backend(*args, **kwargs):
            return _FakeTextBackend()

        monkeypatch.setattr("lib.text_generator.create_text_backend_for_task", _fake_create_backend)
        overview = await pm.generate_overview("demo")
        assert overview["genre"] == "悬疑"
        assert "generated_at" in overview

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            project_data = pm.sync_project_status("demo")
        assert captured
        assert project_data["title"] == "Demo"

        pm_empty = ProjectManager(tmp_path / "projects-empty")
        pm_empty.create_project("demo")
        pm_empty.create_project_metadata("demo", "Demo")
        with pytest.raises(ValueError):
            await pm_empty.generate_overview("demo")


class TestFromCwd:
    """Tests for ProjectManager.from_cwd() classmethod."""

    def test_from_cwd_infers_project(self, tmp_path, monkeypatch):
        projects_root = tmp_path / "projects"
        project_dir = projects_root / "my-proj"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text("{}", encoding="utf-8")

        monkeypatch.chdir(project_dir)
        pm, name = ProjectManager.from_cwd()
        assert name == "my-proj"
        assert pm.projects_root == projects_root

    def test_from_cwd_raises_when_no_project_json(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "projects" / "empty"
        project_dir.mkdir(parents=True)

        monkeypatch.chdir(project_dir)
        with pytest.raises(FileNotFoundError, match="不是有效的项目目录"):
            ProjectManager.from_cwd()


class TestPathTraversalProtection:
    """路径遍历防护测试"""

    def test_get_project_path_rejects_traversal(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        # normalize_project_name 的正则先拦截
        with pytest.raises(ValueError):
            pm.get_project_path("../etc")
        with pytest.raises(ValueError):
            pm.get_project_path("demo/../../etc")

    def test_normalize_project_name_rejects_special_chars(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        with pytest.raises(ValueError):
            pm.normalize_project_name("../hack")
        with pytest.raises(ValueError):
            pm.normalize_project_name("foo/bar")
        with pytest.raises(ValueError):
            pm.normalize_project_name("")

    def test_load_script_rejects_traversal_filename(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        with pytest.raises(ValueError, match="非法文件名"):
            pm.load_script("demo", "../../etc/passwd")

    def test_save_script_rejects_traversal_filename(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        script = {"novel": {"chapter": "ch1"}, "scenes": [], "metadata": {}}
        with pytest.raises(ValueError, match="非法文件名"):
            pm.save_script("demo", script, filename="../../evil.json")

    def test_safe_subpath_allows_normal_filenames(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        project_dir = pm.get_project_path("demo")
        scripts_dir = project_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        # 正常文件名不应被拦截
        real = pm._safe_subpath(scripts_dir, "episode_1.json")
        assert real.endswith("episode_1.json")
