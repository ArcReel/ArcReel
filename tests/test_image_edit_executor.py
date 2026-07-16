"""image_edit executor 的编辑独有语义：底图即当前图且是唯一参考图、prompt 即指令、
按资源类型写回、版本带编辑标记、失败不写回。"""

import tempfile
from pathlib import Path

import pytest

from server.services import image_edit_tasks
from server.services.image_edit_tasks import (
    IMAGE_EDIT_VERSION_SOURCE,
    execute_image_edit_task,
    resolve_current_image_rel,
)


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


class _FakeResolved:
    provider_id = "gemini-aistudio"
    model_id = "gemini-image"


class _FakeGenerator:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.image_calls = []
        self.tracked = []
        self.versions = self

    async def generate_image_async(self, **kwargs):
        if self.fail:
            raise RuntimeError("backend boom")
        self.image_calls.append(kwargs)
        return Path(tempfile.gettempdir()) / "image.png", 2

    def ensure_current_tracked(self, resource_type, resource_id, current_file, prompt, **metadata):
        self.tracked.append({"resource_type": resource_type, "resource_id": resource_id, "prompt": prompt})
        return None

    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"created_at": "2026-01-01T00:00:00Z"}]}


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {
            "content_mode": "narration",
            "characters": {"Alice": {"character_sheet": "characters/Alice.png", "image_prompt": "原始角色 prompt"}},
            "scenes": {"祠堂": {"scene_sheet": ""}},
            "props": {},
            "products": {},
        }
        self.script = {
            "content_mode": "narration",
            "segments": [
                {"segment_id": "E1S01", "generated_assets": {"storyboard_image": "storyboards/scene_E1S01_first.png"}},
                {"segment_id": "E1S02", "generated_assets": {}},
            ],
        }
        self.sheet_updates = []
        self.scene_asset_updates = []

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def load_script(self, project_name, script_file):
        return self.script

    def update_scene_asset(self, **kwargs):
        self.scene_asset_updates.append(kwargs)

    def _update_asset_sheet(self, asset_type, project_name, name, sheet_path):
        self.sheet_updates.append((asset_type, name, sheet_path))


def _prepare_files(tmp_path: Path) -> Path:
    project_path = tmp_path / "projects" / "demo"
    (project_path / "characters").mkdir(parents=True, exist_ok=True)
    (project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "storyboards" / "scene_E1S01_first.png").write_bytes(b"png")
    return project_path


def _patch_common(monkeypatch, fake_pm, fake_generator):
    monkeypatch.setattr(image_edit_tasks, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(image_edit_tasks, "get_media_generator", _async_return(fake_generator))
    monkeypatch.setattr(image_edit_tasks, "_resolve_effective_image_backend", _async_return(_FakeResolved()))
    monkeypatch.setattr(image_edit_tasks, "_resolve_resolution", _async_return("2K"))


class TestResolveCurrentImageRel:
    def test_asset_sheet_and_missing(self):
        project = {"characters": {"Alice": {"character_sheet": "characters/Alice.png"}, "Bob": {}}}
        assert resolve_current_image_rel(project, "character", "Alice") == "characters/Alice.png"
        assert resolve_current_image_rel(project, "character", "Bob") is None
        with pytest.raises(KeyError):
            resolve_current_image_rel(project, "character", "不存在")

    def test_storyboard_pointer_and_canonical_fallback(self):
        script = {
            "content_mode": "narration",
            "segments": [
                {"segment_id": "E1S01", "generated_assets": {"storyboard_image": "storyboards/scene_E1S01_first.png"}},
                {"segment_id": "E1S02", "generated_assets": {}},
            ],
        }
        assert resolve_current_image_rel({}, "storyboard", "E1S01", script) == "storyboards/scene_E1S01_first.png"
        assert resolve_current_image_rel({}, "storyboard", "E1S02", script) == "storyboards/scene_E1S02.png"
        with pytest.raises(KeyError):
            resolve_current_image_rel({}, "storyboard", "E9S99", script)


class TestExecuteImageEditTask:
    async def test_character_edit_uses_current_image_as_sole_reference(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)

        result = await execute_image_edit_task(
            "demo",
            "Alice",
            {"resource_type": "character", "prompt": "把头发改成红色"},
        )

        call = fake_generator.image_calls[0]
        # 参考图仅当前图一张；prompt 仅编辑指令（不拼原 image_prompt）
        assert call["reference_images"] == [project_path / "characters/Alice.png"]
        assert call["prompt"] == "把头发改成红色"
        assert "原始角色 prompt" not in call["prompt"]
        # 新版本带编辑标记 metadata
        assert call["source"] == IMAGE_EDIT_VERSION_SOURCE
        assert call["resource_type"] == "characters"
        # 旧图先以中性元数据补登（不带编辑指令），保证编辑前版本可回滚
        assert fake_generator.tracked == [{"resource_type": "characters", "resource_id": "Alice", "prompt": ""}]
        # 按资源类型写回 canonical 路径；原 image_prompt 字段不被改动
        assert fake_pm.sheet_updates == [("character", "Alice", "characters/Alice.png")]
        assert fake_pm.project["characters"]["Alice"]["image_prompt"] == "原始角色 prompt"

        assert result["resource_type"] == "characters"
        assert result["resource_id"] == "Alice"
        assert result["version"] == 2
        assert result["file_path"] == "characters/Alice.png"

    async def test_storyboard_edit_reads_pointer_writes_canonical(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)

        result = await execute_image_edit_task(
            "demo",
            "E1S01",
            {"resource_type": "storyboard", "prompt": "去掉路人", "script_file": "episode_1.json"},
        )

        call = fake_generator.image_calls[0]
        # 底图取 generated_assets 指针（旧宫格项目路径），新图写回 canonical
        assert call["reference_images"] == [project_path / "storyboards/scene_E1S01_first.png"]
        assert call["resource_type"] == "storyboards"
        assert fake_pm.scene_asset_updates == [
            {
                "project_name": "demo",
                "script_filename": "episode_1.json",
                "scene_id": "E1S01",
                "asset_type": "storyboard_image",
                "asset_path": "storyboards/scene_E1S01.png",
            }
        ]
        assert result["file_path"] == "storyboards/scene_E1S01.png"

    async def test_no_current_image_raises(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)

        with pytest.raises(ValueError, match="no current image"):
            await execute_image_edit_task("demo", "祠堂", {"resource_type": "scene", "prompt": "x"})
        assert fake_generator.image_calls == []
        assert fake_pm.sheet_updates == []

    async def test_backend_failure_skips_writeback(self, tmp_path, monkeypatch):
        """失败零损失：backend 抛错时不写回资源字段（current 图指针由 MediaGenerator 保证不触碰）。
        旧图基线登记先于 backend 调用发生，与成败无关、不因失败回滚，不在本用例断言范围。
        """
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator(fail=True)
        _patch_common(monkeypatch, fake_pm, fake_generator)

        with pytest.raises(RuntimeError, match="backend boom"):
            await execute_image_edit_task("demo", "Alice", {"resource_type": "character", "prompt": "x"})
        assert fake_pm.sheet_updates == []
        assert fake_pm.scene_asset_updates == []

    async def test_invalid_payload_rejected(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)

        with pytest.raises(ValueError, match="resource_type"):
            await execute_image_edit_task("demo", "g1", {"resource_type": "grid", "prompt": "x"})
        with pytest.raises(ValueError, match="instruction"):
            await execute_image_edit_task("demo", "Alice", {"resource_type": "character", "prompt": "   "})
        with pytest.raises(ValueError, match="script_file"):
            await execute_image_edit_task("demo", "E1S01", {"resource_type": "storyboard", "prompt": "x"})


class TestImageEditEventMapping:
    def test_emit_maps_image_edit_to_resource_events(self, tmp_path, monkeypatch):
        """编辑完成事件与同资源的生成完成事件同形状：按 payload.resource_type 派发。"""
        from server.services import generation_tasks

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        captured = []
        monkeypatch.setattr(
            generation_tasks, "emit_project_change_batch", lambda name, changes: captured.extend(changes)
        )

        generation_tasks.emit_generation_success_batch(
            task_type="image_edit",
            project_name="demo",
            resource_id="Alice",
            payload={"resource_type": "character", "prompt": "x"},
        )
        assert captured[0]["entity_type"] == "character"
        assert captured[0]["action"] == "updated"
        # 指纹按 character 任务口径计算（characters/Alice.png 存在于磁盘）
        assert "characters/Alice.png" in captured[0]["asset_fingerprints"]

        captured.clear()
        generation_tasks.emit_generation_success_batch(
            task_type="image_edit",
            project_name="demo",
            resource_id="E1S01",
            payload={"resource_type": "storyboard", "prompt": "x", "script_file": "episode_1.json"},
        )
        assert captured[0]["entity_type"] == "segment"
        assert captured[0]["action"] == "storyboard_ready"
        assert captured[0]["script_file"] == "episode_1.json"


def test_image_edit_registered_in_task_executors():
    from server.services.generation_tasks import _TASK_EXECUTORS

    assert "image_edit" in _TASK_EXECUTORS
