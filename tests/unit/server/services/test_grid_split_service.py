"""Tests for the standalone grid split service (apply_grid_split)."""

import contextlib
import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from lib.grid.models import GridGeneration
from lib.grid_manager import GridManager
from lib.project_manager import ProjectManager
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.services.grid_split import GridImageNotReadyError, apply_grid_split


@pytest.fixture
def project_with_script(tmp_path):
    p = tmp_path / "projects" / "test-project"
    for d in ("storyboards", "grids", "scripts"):
        (p / d).mkdir(parents=True)
    (p / "project.json").write_text(
        json.dumps(
            {
                "name": "test-project",
                "title": "Test",
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "style": "realistic",
                "content_mode": "narration",
                "aspect_ratio": "9:16",
                "generation_mode": "storyboard",
                "grid_storyboard": True,
                "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
                "characters": {},
            }
        ),
        encoding="utf-8",
    )
    (p / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": f"E1S0{i}",
                        "episode": 1,
                        "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
                    }
                    for i in range(1, 4)
                ],
            }
        ),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def grid_with_image(project_with_script):
    """联合图已就绪（completed、grid_image_path 指向落盘 PNG）的宫格记录。"""
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01", "E1S02", "E1S03"],
        rows=2,
        cols=2,
        grid_size="2K",
        provider="openai",
        model="gpt-image-2",
        video_aspect_ratio="9:16",
        prompt="p",
    )
    grid.status = "completed"
    grid.grid_image_path = f"grids/{grid.id}.png"
    Image.new("RGB", (400, 400), color=(30, 60, 90)).save(project_with_script / "grids" / f"{grid.id}.png")
    (project_with_script / "grids" / f"{grid.id}.json").write_text(
        json.dumps(grid.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    return grid


def _mock_pm(project_with_script, script_data=None):
    pm = MagicMock()
    pm.get_project_path.return_value = project_with_script
    pm.load_project.return_value = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
    pm.load_project_readonly.return_value = pm.load_project.return_value
    pm.load_script.return_value = (
        script_data
        if script_data is not None
        else json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    )

    def _batch_update(*_args, on_commit=None, prepare_on_commit=None, **_kwargs):
        commit = prepare_on_commit(pm.load_script.return_value) if prepare_on_commit is not None else on_commit
        if commit is not None:
            commit(project_with_script / "scripts" / "episode_1.json")
        return pm.load_script.return_value

    pm.batch_update_scene_assets.side_effect = _batch_update

    @contextlib.contextmanager
    def _locked_snapshot(_project_name, _script_filename):
        yield pm.load_project.return_value, pm.load_script.return_value

    pm.locked_project_script_snapshot.side_effect = _locked_snapshot
    return pm


def _register_grid(project_path, grid):
    from lib.artifact_activation import register_current_resource_artifact

    assert register_current_resource_artifact(
        project_path,
        resource_type="grids",
        resource_id=grid.id,
    )


class TestApplyGridSplit:
    async def test_split_writes_cells_and_versions(self, project_with_script, grid_with_image):
        """切分覆写各分镜格：文件名 scene_{id}.png（无 first/last 后缀）、逐格入版本史、
        剧本回写 storyboard_image / grid_id / grid_cell_index、split_at 落章。"""
        from lib.version_manager import VersionManager

        grid = grid_with_image
        storyboards_dir = project_with_script / "storyboards"
        # 预置旧分镜格：覆写前必须补登版本，否则旧字节丢失
        (storyboards_dir / "scene_E1S01.png").write_bytes(b"old-bytes")
        _register_grid(project_with_script, grid)

        pm = _mock_pm(project_with_script)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("server.services.generation_tasks.emit_generation_success_batch", return_value={"grids/x.png": 1}),
        ):
            result = await apply_grid_split("test-project", grid)

        for sid in ("E1S01", "E1S02", "E1S03"):
            assert (storyboards_dir / f"scene_{sid}.png").exists(), f"missing scene_{sid}.png"
            assert not (storyboards_dir / f"scene_{sid}_first.png").exists()
            assert not (storyboards_dir / f"scene_{sid}_last.png").exists()
        assert result.updated_scene_ids == ["E1S01", "E1S02", "E1S03"]
        assert result.missing_scene_ids == []

        # 逐格版本：旧文件补登 + 覆写后新版本（source=grid_split）
        versions = VersionManager(project_with_script)
        e1s01 = versions.get_versions("storyboards", "E1S01")
        assert len(e1s01["versions"]) >= 2
        latest = e1s01["versions"][-1]
        assert latest.get("source") == "grid_split" or latest.get("metadata", {}).get("source") == "grid_split"

        pm.batch_update_scene_assets.assert_called_once()
        updates = pm.batch_update_scene_assets.call_args.kwargs["updates"]
        sb_paths = {sid: path for sid, asset_type, path in updates if asset_type == "storyboard_image"}
        assert sb_paths == {
            "E1S01": "storyboards/scene_E1S01.png",
            "E1S02": "storyboards/scene_E1S02.png",
            "E1S03": "storyboards/scene_E1S03.png",
        }
        asset_types = {asset_type for _, asset_type, _ in updates}
        assert asset_types == {"storyboard_image", "grid_id", "grid_cell_index"}

        saved = json.loads((project_with_script / "grids" / f"{grid.id}.json").read_text(encoding="utf-8"))
        assert saved["split_at"] is not None

    async def test_split_skips_missing_scene_ids(self, project_with_script, grid_with_image, caplog):
        """grid plan 生成后剧本被改动（删/拆分镜）→ frame_chain 中已不存在的 next_scene_id
        跳过 cell PNG 保存 + warning + 不让 batch_update 抛 KeyError 整批回滚
        （避免 cell PNG 已落盘但 script 无引用的 orphan PNG）。"""
        grid = grid_with_image
        script_data = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        script_data["segments"] = [seg for seg in script_data["segments"] if seg["segment_id"] == "E1S01"]
        _register_grid(project_with_script, grid)

        pm = _mock_pm(project_with_script, script_data)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("server.services.generation_tasks.emit_generation_success_batch", return_value={}),
            caplog.at_level(logging.WARNING, logger="server.services.grid_split"),
        ):
            result = await apply_grid_split("test-project", grid)

        storyboards_dir = project_with_script / "storyboards"
        assert (storyboards_dir / "scene_E1S01.png").exists()
        assert not (storyboards_dir / "scene_E1S02.png").exists()
        assert not (storyboards_dir / "scene_E1S03.png").exists()
        assert result.missing_scene_ids == ["E1S02", "E1S03"]

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("E1S02" in m and "E1S03" in m for m in warnings)

        assert pm.batch_update_scene_assets.called
        updates = pm.batch_update_scene_assets.call_args.kwargs["updates"]
        assert {sid for sid, _, _ in updates} == {"E1S01"}

    async def test_split_only_scene_ids_excludes_out_of_scope_ids_before_the_missing_check(
        self, project_with_script, grid_with_image, caplog
    ):
        """only_scene_ids 过滤必须先于 valid_ids 检查生效：调用方只想要 E1S01 这一格
        时，剧本里已不存在的 E1S02/E1S03（不在目标集合内）不能被当成该调用的缺口
        计入 missing_scene_ids——那会让调用方以为它们是该调用漏掉的，实际上从未
        被请求过。"""
        grid = grid_with_image
        script_data = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        script_data["segments"] = [seg for seg in script_data["segments"] if seg["segment_id"] == "E1S01"]
        _register_grid(project_with_script, grid)

        pm = _mock_pm(project_with_script, script_data)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("server.services.generation_tasks.emit_generation_success_batch", return_value={}),
            caplog.at_level(logging.WARNING, logger="server.services.grid_split"),
        ):
            result = await apply_grid_split("test-project", grid, only_scene_ids=frozenset({"E1S01"}))

        assert result.updated_scene_ids == ["E1S01"]
        assert result.missing_scene_ids == []

    async def test_split_requires_grid_image(self, project_with_script, grid_with_image):
        grid = grid_with_image
        (project_with_script / "grids" / f"{grid.id}.png").unlink()

        pm = _mock_pm(project_with_script)
        with patch("server.services.grid_split.get_project_manager", return_value=pm):
            with pytest.raises(GridImageNotReadyError):
                await apply_grid_split("test-project", grid)

    async def test_split_emits_grid_split_event(self, project_with_script, grid_with_image):
        grid = grid_with_image
        _register_grid(project_with_script, grid)
        pm = _mock_pm(project_with_script)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("server.services.generation_tasks.emit_generation_success_batch", return_value={"a": 1}) as emit,
        ):
            result = await apply_grid_split("test-project", grid)

        emit.assert_called_once()
        assert emit.call_args.kwargs["task_type"] == "grid_split"
        assert emit.call_args.kwargs["resource_id"] == grid.id
        assert result.asset_fingerprints == {"a": 1}

    async def test_registration_failure_restores_every_split_sidecar(self, project_with_script, grid_with_image):
        from lib.version_manager import VersionManager

        project_file = project_with_script / "project.json"
        _register_grid(project_with_script, grid_with_image)
        script_file = project_with_script / "scripts" / "episode_1.json"
        grid_file = project_with_script / "grids" / f"{grid_with_image.id}.json"
        storyboards = project_with_script / "storyboards"
        for index, scene_id in enumerate(("E1S01", "E1S02", "E1S03"), start=1):
            (storyboards / f"scene_{scene_id}.png").write_bytes(f"old-{index}".encode())

        versions = VersionManager(project_with_script)
        versions.add_version(
            "storyboards",
            "E1S01",
            "old",
            source_file=storyboards / "scene_E1S01.png",
        )
        snapshots = {
            "project": project_file.read_bytes(),
            "script": script_file.read_bytes(),
            "grid": grid_file.read_bytes(),
            "versions": versions.versions_file.read_bytes(),
            **{
                scene_id: (storyboards / f"scene_{scene_id}.png").read_bytes()
                for scene_id in ("E1S01", "E1S02", "E1S03")
            },
        }
        pm = ProjectManager(project_with_script.parent)

        def _fail_register(*_args, **_kwargs):
            raise RuntimeError("manifest commit failed")

        with patch("server.services.grid_split.get_project_manager", return_value=pm):
            with pytest.raises(RuntimeError, match="manifest commit failed"):
                await apply_grid_split("test-project", grid_with_image, register_entries=_fail_register)

        assert project_file.read_bytes() == snapshots["project"]
        assert script_file.read_bytes() == snapshots["script"]
        assert grid_file.read_bytes() == snapshots["grid"]
        assert versions.versions_file.read_bytes() == snapshots["versions"]
        for scene_id in ("E1S01", "E1S02", "E1S03"):
            assert (storyboards / f"scene_{scene_id}.png").read_bytes() == snapshots[scene_id]

    async def test_successful_split_registers_every_cell_from_one_frozen_grid_basis(
        self,
        project_with_script,
        grid_with_image,
    ):
        from lib.artifact_activation import ArtifactCurrencyResolver
        from lib.artifact_manifest import ArtifactKey, ArtifactStatus
        from lib.version_manager import VersionManager
        from server.routers import versions as versions_router

        _register_grid(project_with_script, grid_with_image)
        pm = ProjectManager(project_with_script.parent)

        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("server.services.generation_tasks.emit_generation_success_batch", return_value={}),
        ):
            await apply_grid_split("test-project", grid_with_image)

        versions = VersionManager(project_with_script)
        selected_version = versions.get_current_version("storyboards", "E1S01")
        assert selected_version is not None
        selected_record = next(
            record
            for record in versions.get_versions("storyboards", "E1S01")["versions"]
            if record["version"] == selected_version
        )
        assert "artifact_image_basis" in selected_record, selected_record
        with patch("server.routers.versions.get_project_manager", return_value=pm):
            restored = await versions_router.restore_version(
                "test-project",
                "storyboards",
                "E1S01",
                selected_version,
            )
        assert restored["success"] is True

        resolver = ArtifactCurrencyResolver(project_with_script)
        for scene_id in ("E1S01", "E1S02", "E1S03"):
            comparison = resolver.compare(
                ArtifactKey.episode_storyboard(1, scene_id),
                artifact_path=f"storyboards/scene_{scene_id}.png",
            )
            assert comparison.status is ArtifactStatus.CURRENT

    async def test_split_refuses_an_unclaimed_grid_without_side_effects(
        self,
        project_with_script,
        grid_with_image,
    ):
        project_file = project_with_script / "project.json"
        script_file = project_with_script / "scripts" / "episode_1.json"
        grid_file = project_with_script / "grids" / f"{grid_with_image.id}.json"
        before = {
            "project": project_file.read_bytes(),
            "script": script_file.read_bytes(),
            "grid": grid_file.read_bytes(),
            "storyboards": tuple(sorted((project_with_script / "storyboards").iterdir())),
        }
        pm = ProjectManager(project_with_script.parent)

        with patch("server.services.grid_split.get_project_manager", return_value=pm):
            with pytest.raises(GridImageNotReadyError, match="registered"):
                await apply_grid_split("test-project", grid_with_image)

        assert project_file.read_bytes() == before["project"]
        assert script_file.read_bytes() == before["script"]
        assert grid_file.read_bytes() == before["grid"]
        assert tuple(sorted((project_with_script / "storyboards").iterdir())) == before["storyboards"]

    async def test_split_rechecks_grid_claim_before_the_final_commit(
        self,
        project_with_script,
        grid_with_image,
        monkeypatch,
    ):
        """开切时登记还在、临commit前被撤销 → 最终事务复核仍拒绝，且不留任何副作用。"""
        from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter

        _register_grid(project_with_script, grid_with_image)
        pm = ProjectManager(project_with_script.parent)
        original_batch_update = pm.batch_update_scene_assets
        script_file = project_with_script / "scripts" / "episode_1.json"
        grid_file = project_with_script / "grids" / f"{grid_with_image.id}.json"
        script_before = script_file.read_bytes()
        grid_before = grid_file.read_bytes()

        def _revoke_then_commit(*args, **kwargs):
            ProjectArtifactManifestAdapter(project_with_script).delete_entry(
                ArtifactKey.episode_grid(1, grid_with_image.id)
            )
            return original_batch_update(*args, **kwargs)

        monkeypatch.setattr(pm, "batch_update_scene_assets", _revoke_then_commit)
        with patch("server.services.grid_split.get_project_manager", return_value=pm):
            with pytest.raises(GridImageNotReadyError, match="registered"):
                await apply_grid_split("test-project", grid_with_image)

        assert script_file.read_bytes() == script_before
        assert grid_file.read_bytes() == grid_before
        assert not (project_with_script / "versions" / "versions.json").exists()
        assert not tuple((project_with_script / "storyboards").glob("scene_*.png"))

    async def test_split_cells_from_a_stale_claim_remain_stale(self, project_with_script, grid_with_image):
        from lib.artifact_activation import ArtifactCurrencyResolver
        from lib.artifact_manifest import ArtifactKey, ArtifactStatus

        _register_grid(project_with_script, grid_with_image)
        script_file = project_with_script / "scripts" / "episode_1.json"
        script = json.loads(script_file.read_text(encoding="utf-8"))
        script["segments"][0]["image_prompt"] = "changed after the composite was generated"
        script_file.write_text(json.dumps(script), encoding="utf-8")
        source_key = ArtifactKey.episode_grid(1, grid_with_image.id)
        assert (
            ArtifactCurrencyResolver(project_with_script)
            .compare(source_key, artifact_path=f"grids/{grid_with_image.id}.png")
            .status
            is ArtifactStatus.STALE
        )
        pm = ProjectManager(project_with_script.parent)

        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("server.services.generation_tasks.emit_generation_success_batch", return_value={}),
        ):
            await apply_grid_split("test-project", grid_with_image)

        resolver = ArtifactCurrencyResolver(project_with_script)
        for scene_id in ("E1S01", "E1S02", "E1S03"):
            comparison = resolver.compare(
                ArtifactKey.episode_storyboard(1, scene_id),
                artifact_path=f"storyboards/scene_{scene_id}.png",
            )
            assert comparison.status is ArtifactStatus.STALE

    async def test_source_claim_change_rolls_back_the_whole_split(self, project_with_script, grid_with_image):
        from lib.artifact_manifest import (
            ArtifactKey,
            ArtifactManifestError,
            ProjectArtifactManifestAdapter,
        )
        from server.services import grid_split as grid_split_module

        _register_grid(project_with_script, grid_with_image)
        project_file = project_with_script / "project.json"
        script_file = project_with_script / "scripts" / "episode_1.json"
        grid_file = project_with_script / "grids" / f"{grid_with_image.id}.json"
        versions_file = project_with_script / "versions" / "versions.json"
        before = {
            "project": project_file.read_bytes(),
            "script": script_file.read_bytes(),
            "grid": grid_file.read_bytes(),
        }
        source_key = ArtifactKey.episode_grid(1, grid_with_image.id)
        original_register = grid_split_module._register_split_entries_atomically

        def _replace_source_then_register(project_path, *, entries, expected_entries):
            assert ProjectArtifactManifestAdapter(project_path).delete_entry(source_key)
            original_register(project_path, entries=entries, expected_entries=expected_entries)

        pm = ProjectManager(project_with_script.parent)
        with patch("server.services.grid_split.get_project_manager", return_value=pm):
            with pytest.raises(ArtifactManifestError, match="changed during batch registration"):
                await apply_grid_split(
                    "test-project",
                    grid_with_image,
                    register_entries=_replace_source_then_register,
                )

        assert project_file.read_bytes() == before["project"]
        assert script_file.read_bytes() == before["script"]
        assert grid_file.read_bytes() == before["grid"]
        assert not versions_file.exists()
        assert not tuple((project_with_script / "storyboards").glob("scene_*.png"))
        adapter = ProjectArtifactManifestAdapter(project_with_script)
        assert adapter.get_entry(source_key) is None
        for scene_id in ("E1S01", "E1S02", "E1S03"):
            assert adapter.get_entry(ArtifactKey.episode_storyboard(1, scene_id)) is None

    async def test_reference_change_while_building_cell_basis_rolls_back_the_whole_split(
        self,
        project_with_script,
        grid_with_image,
    ):
        from lib.artifact_activation import ArtifactCurrencyResolver, register_current_resource_artifact
        from lib.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
        from lib.grid.models import ReferenceImage
        from server.services import grid_split as grid_split_module

        project_file = project_with_script / "project.json"
        project = json.loads(project_file.read_text(encoding="utf-8"))
        project["characters"] = {
            "Alice": {
                "description": "hero",
                "character_sheet": "characters/Alice.png",
            }
        }
        project_file.write_text(json.dumps(project), encoding="utf-8")
        sheet_path = project_with_script / "characters" / "Alice.png"
        sheet_path.parent.mkdir()
        Image.new("RGB", (4, 4), color=(10, 20, 30)).save(sheet_path)
        grid_with_image.reference_images = [
            ReferenceImage(path="characters/Alice.png", name="Alice", ref_type="character")
        ]
        GridManager(project_with_script).save(grid_with_image)
        _register_grid(project_with_script, grid_with_image)
        assert register_current_resource_artifact(
            project_with_script,
            resource_type="characters",
            resource_id="Alice",
        )

        source_key = ArtifactKey.episode_grid(1, grid_with_image.id)
        assert (
            ArtifactCurrencyResolver(project_with_script)
            .compare(source_key, artifact_path=f"grids/{grid_with_image.id}.png")
            .status
            is ArtifactStatus.CURRENT
        )
        script_file = project_with_script / "scripts" / "episode_1.json"
        grid_file = project_with_script / "grids" / f"{grid_with_image.id}.json"
        before = {
            "project": project_file.read_bytes(),
            "script": script_file.read_bytes(),
            "grid": grid_file.read_bytes(),
        }
        original_build = grid_split_module.build_grid_member_storyboard_visual_basis
        replaced = False

        def _replace_reference_then_build(**kwargs):
            nonlocal replaced
            if not replaced:
                Image.new("RGB", (4, 4), color=(90, 80, 70)).save(sheet_path)
                replaced = True
            return original_build(**kwargs)

        pm = ProjectManager(project_with_script.parent)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch(
                "server.services.grid_split.build_grid_member_storyboard_visual_basis",
                side_effect=_replace_reference_then_build,
            ),
        ):
            with pytest.raises(GridImageNotReadyError, match="changed while being split"):
                await apply_grid_split("test-project", grid_with_image)

        assert replaced is True
        assert project_file.read_bytes() == before["project"]
        assert script_file.read_bytes() == before["script"]
        assert grid_file.read_bytes() == before["grid"]
        assert not (project_with_script / "versions" / "versions.json").exists()
        assert not tuple((project_with_script / "storyboards").glob("scene_*.png"))
        adapter = ProjectArtifactManifestAdapter(project_with_script)
        assert adapter.get_entry(source_key) is not None
        for scene_id in ("E1S01", "E1S02", "E1S03"):
            assert adapter.get_entry(ArtifactKey.episode_storyboard(1, scene_id)) is None

    async def test_stale_source_composite_change_rolls_back_the_whole_split(
        self,
        project_with_script,
        grid_with_image,
    ):
        from lib.artifact_activation import ArtifactCurrencyResolver
        from lib.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
        from server.services import grid_split as grid_split_module

        _register_grid(project_with_script, grid_with_image)
        script_file = project_with_script / "scripts" / "episode_1.json"
        script = json.loads(script_file.read_text(encoding="utf-8"))
        script["segments"][0]["image_prompt"] = "changed after the composite was produced"
        script_file.write_text(json.dumps(script), encoding="utf-8")
        source_key = ArtifactKey.episode_grid(1, grid_with_image.id)
        assert (
            ArtifactCurrencyResolver(project_with_script)
            .compare(source_key, artifact_path=f"grids/{grid_with_image.id}.png")
            .status
            is ArtifactStatus.STALE
        )

        project_file = project_with_script / "project.json"
        grid_file = project_with_script / "grids" / f"{grid_with_image.id}.json"
        composite_file = project_with_script / "grids" / f"{grid_with_image.id}.png"
        source_entry = ProjectArtifactManifestAdapter(project_with_script).get_entry(source_key)
        before = {
            "project": project_file.read_bytes(),
            "script": script_file.read_bytes(),
            "grid": grid_file.read_bytes(),
        }
        original_build = grid_split_module.build_stale_grid_member_storyboard_visual_basis
        replaced = False

        def _replace_composite_then_build(**kwargs):
            nonlocal replaced
            if not replaced:
                Image.new("RGB", (400, 400), color=(90, 80, 70)).save(composite_file)
                replaced = True
            return original_build(**kwargs)

        pm = ProjectManager(project_with_script.parent)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch(
                "server.services.grid_split.build_stale_grid_member_storyboard_visual_basis",
                side_effect=_replace_composite_then_build,
            ),
        ):
            with pytest.raises(GridImageNotReadyError, match="changed while being split"):
                await apply_grid_split("test-project", grid_with_image)

        assert replaced is True
        assert project_file.read_bytes() == before["project"]
        assert script_file.read_bytes() == before["script"]
        assert grid_file.read_bytes() == before["grid"]
        assert not (project_with_script / "versions" / "versions.json").exists()
        assert not tuple((project_with_script / "storyboards").glob("scene_*.png"))
        assert ProjectArtifactManifestAdapter(project_with_script).get_entry(source_key) == source_entry

    async def test_cell_save_failure_removes_every_staged_path(self, project_with_script, grid_with_image):
        original_save = Image.Image.save

        def _write_partial_cell_then_fail(image, target, *args, **kwargs):
            if ".grid-split" in str(target):
                target.write_bytes(b"partial-cell")
                raise RuntimeError("cell save failed")
            return original_save(image, target, *args, **kwargs)

        _register_grid(project_with_script, grid_with_image)
        pm = ProjectManager(project_with_script.parent)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch.object(Image.Image, "save", _write_partial_cell_then_fail),
        ):
            with pytest.raises(RuntimeError, match="cell save failed"):
                await apply_grid_split("test-project", grid_with_image)

        assert not tuple((project_with_script / "storyboards").glob(".*.grid-split.png"))

    async def test_script_resolution_failure_removes_the_composite_snapshot(
        self,
        project_with_script,
        grid_with_image,
    ):
        _register_grid(project_with_script, grid_with_image)
        grids_dir = project_with_script / "grids"
        pm = ProjectManager(project_with_script.parent)

        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("lib.script_editor.resolve_items", side_effect=RuntimeError("script resolution failed")),
        ):
            with pytest.raises(RuntimeError, match="script resolution failed"):
                await apply_grid_split("test-project", grid_with_image)

        assert not tuple(grids_dir.glob(f".{grid_with_image.id}.*.split-source.png"))


class TestSplitAspectRatio:
    """切分按联合图产出时冻结的比例裁切，而非项目当下的 aspect_ratio。"""

    async def _split_and_measure(self, project_with_script, grid) -> tuple[int, int]:
        GridManager(project_with_script).save(grid)
        _register_grid(project_with_script, grid)
        pm = _mock_pm(project_with_script)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("server.services.generation_tasks.emit_generation_success_batch", return_value={}),
        ):
            await apply_grid_split("test-project", grid)
        with Image.open(project_with_script / "storyboards" / "scene_E1S01.png") as cell:
            return cell.size

    async def test_uses_frozen_ratio_after_project_ratio_changed(self, project_with_script, grid_with_image):
        """项目从 16:9 改到 9:16 后再切历史联合图，各格仍按 16:9 裁切。

        读项目当下设置会把横版格中心裁成竖版，每格丢掉大半宽度。
        """
        grid = grid_with_image
        grid.video_aspect_ratio = "16:9"

        width, height = await self._split_and_measure(project_with_script, grid)

        assert width > height, f"应按记录冻结的 16:9 裁成横版，实际 {width}x{height}"
        assert abs(width / height - 16 / 9) < 0.05

    async def test_legacy_record_without_ratio_falls_back_to_project(self, project_with_script, grid_with_image):
        """存量记录没有该字段，退回项目当前设置（9:16），保持既有行为。"""
        grid = grid_with_image
        grid.video_aspect_ratio = None

        width, height = await self._split_and_measure(project_with_script, grid)

        assert height > width, f"应回退到项目的 9:16，实际 {width}x{height}"
        assert abs(height / width - 16 / 9) < 0.05
