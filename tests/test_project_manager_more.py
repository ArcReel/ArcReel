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
        _write(project_dir / "scenes" / "s.png", "x")
        _write(project_dir / "props" / "p.png", "x")
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

    def test_save_script_rejects_mismatch_before_write(self, tmp_path):
        """save_script 在 filename/内部 episode 不一致时必须写盘前 fail-fast。

        回归（codex 评审）：旧版把校验放在 sync_episode_from_script，会造成
        脚本文件已原子写、project.json 未同步的部分提交状态。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        bad = {
            "episode": 1,  # 与文件名 episode_10.json 错配
            "title": "第十集错误标题",
            "content_mode": "narration",
            "segments": [],
        }
        with pytest.raises(ValueError, match="不一致"):
            pm.save_script("demo", bad, "episode_10.json")

        # 关键断言：文件不应被写入磁盘（原子性保持）
        scripts_dir = pm.get_project_path("demo") / "scripts"
        assert not (scripts_dir / "episode_10.json").exists()

    def test_sync_episode_rejects_filename_episode_mismatch(self, tmp_path):
        """文件名隐含集号与脚本内 episode 字段不一致时必须拒绝同步。

        回归：AI 生成 episode_10.json 但内部 episode=1 曾导致第 1 集条目被覆盖、
        第 10 集丢失，并触发 SSE 循环不停 touch metadata.updated_at。
        """
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")

        ep1 = {
            "episode": 1,
            "title": "第一集原标题",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4}],
        }
        pm.save_script("demo", ep1, "episode_1.json")

        # 伪造错误脚本：文件名是 episode_10.json，但内部 episode=1（AI 幻觉场景）
        corrupted = {
            "episode": 1,
            "title": "第十集错误标题",
            "content_mode": "narration",
            "segments": [{"segment_id": "E10S01", "duration_seconds": 4}],
        }
        # 绕过 save_script 的潜在未来校验，直接落盘模拟历史产物
        scripts_dir = pm.get_project_path("demo") / "scripts"
        (scripts_dir / "episode_10.json").write_text(json.dumps(corrupted, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(ValueError, match="不一致"):
            pm.sync_episode_from_script("demo", "episode_10.json")

        # project.json 中第 1 集条目必须保持不被污染
        proj = pm.load_project("demo")
        ep1_entry = next(ep for ep in proj["episodes"] if ep["episode"] == 1)
        assert ep1_entry["title"] == "第一集原标题"
        assert ep1_entry["script_file"] == "scripts/episode_1.json"

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
        }
        _write(tmp_path / "projects" / "demo" / "scripts" / "legacy.json", json.dumps(raw_script, ensure_ascii=False))

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(pm, "sync_characters_from_script", lambda *args, **kwargs: None, raising=False)
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

        # scene lifecycle
        pm.add_scenes_batch("demo", {"客厅": {"description": "宽敞的客厅"}})
        pm.update_scene_sheet("demo", "客厅", "scenes/客厅.png")
        assert pm.get_scene("demo", "客厅")["scene_sheet"].endswith("客厅.png")

        project_dir = pm.get_project_path("demo")
        (project_dir / "scenes" / "客厅.png").write_bytes(b"png")
        assert pm.get_pending_project_scenes("demo") == []

        # prop lifecycle
        pm.add_props_batch("demo", {"玉佩": {"description": "古玉"}})
        pm.update_prop_sheet("demo", "玉佩", "props/玉佩.png")
        assert pm.get_prop("demo", "玉佩")["prop_sheet"].endswith("玉佩.png")

        (project_dir / "props" / "玉佩.png").write_bytes(b"png")
        assert pm.get_pending_project_props("demo") == []

        # direct add_* return bool
        assert pm.add_character("demo", "Bob", "side", "") is True
        assert pm.add_character("demo", "Bob", "side", "") is False
        assert pm.add_project_scene("demo", "卧室", "宁静的卧室") is True
        assert pm.add_project_scene("demo", "卧室", "宁静的卧室") is False
        assert pm.add_prop("demo", "宝剑", "古代宝剑") is True
        assert pm.add_prop("demo", "宝剑", "古代宝剑") is False

        added_chars = pm.add_characters_batch("demo", {"Bob": {"description": "d"}, "C": {"description": "d"}})
        assert added_chars == 1
        added_scenes = pm.add_scenes_batch(
            "demo", {"卧室": {"description": "d"}, "书房": {"description": "d"}}
        )  # 卧室已存在
        assert added_scenes == 1
        added_props = pm.add_props_batch("demo", {"玉佩": {"description": "d"}, "铜钱": {"description": "d"}})
        assert added_props == 1

        pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
        pm.add_episode("demo", 1, "第一集-改", "scripts/episode_1.json")
        assert pm.load_project("demo")["episodes"][0]["title"].startswith("第一集")

        assert str(pm.get_source_path("demo", "a.txt")).endswith("/source/a.txt")
        assert str(pm.get_character_path("demo", "a.png")).endswith("/characters/a.png")
        assert str(pm.get_storyboard_path("demo", "a.png")).endswith("/storyboards/a.png")
        assert str(pm.get_video_path("demo", "a.mp4")).endswith("/videos/a.mp4")
        assert str(pm.get_output_path("demo", "a.mp4")).endswith("/output/a.mp4")
        assert str(pm.get_scene_path("demo", "a.png")).endswith("/scenes/a.png")
        assert str(pm.get_prop_path("demo", "a.png")).endswith("/props/a.png")

        with pytest.raises(KeyError):
            pm.get_project_character("demo", "none")
        with pytest.raises(KeyError):
            pm.update_project_character_sheet("demo", "none", "x")
        with pytest.raises(KeyError):
            pm.update_character_reference_image("demo", "none", "x")
        with pytest.raises(KeyError):
            pm.get_scene("demo", "none")
        with pytest.raises(KeyError):
            pm.update_scene_sheet("demo", "none", "x")
        with pytest.raises(KeyError):
            pm.get_prop("demo", "none")
        with pytest.raises(KeyError):
            pm.update_prop_sheet("demo", "none", "x")

    @pytest.mark.asyncio
    async def test_reference_read_source_and_generate_overview(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")

        pm.add_character("demo", "Alice", "hero")
        pm.add_props_batch("demo", {"玉佩": {"description": "古玉"}})

        project_dir = pm.get_project_path("demo")
        (project_dir / "characters" / "Alice.png").write_bytes(b"png")
        (project_dir / "props" / "玉佩.png").write_bytes(b"png")

        project = pm.load_project("demo")
        project["characters"]["Alice"]["character_sheet"] = "characters/Alice.png"
        project["props"]["玉佩"]["prop_sheet"] = "props/玉佩.png"
        pm.save_project("demo", project)

        refs = pm.collect_reference_images(
            "demo",
            {"characters_in_scene": ["Alice"], "props_in_scene": ["玉佩"]},
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


class TestResolveEpisodeFromScript:
    def test_prefers_script_top_level_episode(self):
        ep = ProjectManager.resolve_episode_from_script({"episode": 7, "scenes": []}, "whatever.json")
        assert ep == 7

    def test_falls_back_to_filename_regex(self):
        ep = ProjectManager.resolve_episode_from_script({"scenes": []}, "episode_3.json")
        assert ep == 3

    def test_filename_regex_case_insensitive_and_spaced(self):
        assert ProjectManager.resolve_episode_from_script({}, "Episode 12.json") == 12

    def test_filename_regex_supports_hyphen(self):
        assert ProjectManager.resolve_episode_from_script({}, "episode-5.json") == 5

    def test_ignores_non_int_episode_field(self):
        """非整数的 episode 字段（如 '1'）应回退到文件名。"""
        ep = ProjectManager.resolve_episode_from_script({"episode": "1"}, "episode_9.json")
        assert ep == 9

    def test_raises_when_unresolvable(self):
        with pytest.raises(ValueError, match="无法确定集号"):
            ProjectManager.resolve_episode_from_script({}, "random_name.json")


class TestScenePropLifecycle:
    """scene / prop 生命周期测试（Task 9）"""

    def test_add_scene_creates_entry(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")

        assert pm.add_project_scene("demo", "客厅", "宽敞的客厅") is True
        assert pm.add_project_scene("demo", "客厅", "宽敞的客厅") is False  # 重复返回 False

        project = pm.load_project("demo")
        assert "客厅" in project["scenes"]
        assert project["scenes"]["客厅"]["description"] == "宽敞的客厅"
        assert project["scenes"]["客厅"]["scene_sheet"] == ""

    def test_add_prop_creates_entry(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")

        assert pm.add_prop("demo", "玉佩", "一块古玉") is True
        assert pm.add_prop("demo", "玉佩", "一块古玉") is False  # 重复返回 False

        project = pm.load_project("demo")
        assert "玉佩" in project["props"]
        assert project["props"]["玉佩"]["description"] == "一块古玉"
        assert project["props"]["玉佩"]["prop_sheet"] == ""

    def test_get_pending_scenes_lists_without_sheet(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_project_scene("demo", "客厅", "宽敞的客厅")
        pm.add_project_scene("demo", "书房", "安静的书房")

        # 无 scene_sheet 时全部 pending
        pending = pm.get_pending_project_scenes("demo")
        assert len(pending) == 2
        assert any(s["name"] == "客厅" for s in pending)

        # 设置 sheet 但文件不存在 → 依然 pending
        pm.update_scene_sheet("demo", "客厅", "scenes/客厅.png")
        pending2 = pm.get_pending_project_scenes("demo")
        assert len(pending2) == 2

        # 文件存在 → 不再 pending
        project_dir = pm.get_project_path("demo")
        (project_dir / "scenes" / "客厅.png").parent.mkdir(parents=True, exist_ok=True)
        (project_dir / "scenes" / "客厅.png").write_bytes(b"png")
        pending3 = pm.get_pending_project_scenes("demo")
        assert len(pending3) == 1
        assert pending3[0]["name"] == "书房"

    def test_get_pending_props_lists_without_sheet(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_prop("demo", "玉佩", "古玉")
        pm.add_prop("demo", "宝剑", "利剑")

        # 无 prop_sheet 时全部 pending
        pending = pm.get_pending_project_props("demo")
        assert len(pending) == 2

        # 文件存在 → 不再 pending
        pm.update_prop_sheet("demo", "玉佩", "props/玉佩.png")
        project_dir = pm.get_project_path("demo")
        (project_dir / "props" / "玉佩.png").parent.mkdir(parents=True, exist_ok=True)
        (project_dir / "props" / "玉佩.png").write_bytes(b"png")
        pending2 = pm.get_pending_project_props("demo")
        assert len(pending2) == 1
        assert pending2[0]["name"] == "宝剑"

    def test_add_scenes_batch_skips_existing(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_project_scene("demo", "客厅", "已有的客厅")

        added = pm.add_scenes_batch("demo", {"客厅": {"description": "新描述"}, "书房": {"description": "书房"}})
        assert added == 1

        project = pm.load_project("demo")
        # 已有的不被覆盖
        assert project["scenes"]["客厅"]["description"] == "已有的客厅"
        # 新的被添加
        assert "书房" in project["scenes"]

    def test_add_props_batch_skips_existing(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_prop("demo", "玉佩", "已有的玉佩")  # add_prop 无命名冲突

        added = pm.add_props_batch("demo", {"玉佩": {"description": "新描述"}, "宝剑": {"description": "宝剑"}})
        assert added == 1

        project = pm.load_project("demo")
        assert project["props"]["玉佩"]["description"] == "已有的玉佩"
        assert "宝剑" in project["props"]


def test_read_source_files_raises_on_non_utf8(tmp_path):
    import random

    from lib.source_loader.errors import SourceDecodeError

    pm = ProjectManager(tmp_path)
    project_dir = tmp_path / "demo"
    (project_dir / "source").mkdir(parents=True)
    bad = project_dir / "source" / "broken.txt"
    # 使用种子化 PRNG 生成高熵随机字节，确保触发 decode_txt 的 5% 乱码阈值
    bad.write_bytes(random.Random(42).randbytes(4000))

    with pytest.raises(SourceDecodeError) as exc_info:
        pm._read_source_files("demo")
    assert exc_info.value.filename == "broken.txt"
