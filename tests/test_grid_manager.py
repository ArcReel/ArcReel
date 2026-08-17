"""Tests for GridManager file-based CRUD."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from lib.artifact_manifest import MANIFEST_FILENAME, ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.grid.models import GridGeneration
from lib.grid_manager import GridManager

pytestmark = pytest.mark.unit


def _make_grid(**kwargs) -> GridGeneration:
    defaults = dict(
        episode=1,
        script_file="ep1.json",
        scene_ids=["S1", "S2", "S3", "S4"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="test",
        model="m",
        video_aspect_ratio="9:16",
    )
    defaults.update(kwargs)
    return GridGeneration.create(**defaults)


class TestGridManager:
    def test_save_and_load(self, tmp_path):
        gm = GridManager(tmp_path)
        grid = _make_grid()
        gm.save(grid)
        loaded = gm.get(grid.id)
        assert loaded is not None
        assert loaded.id == grid.id
        assert loaded.scene_ids == ["S1", "S2", "S3", "S4"]
        assert len(loaded.frame_chain) == 4

    def test_list_grids(self, tmp_path):
        gm = GridManager(tmp_path)
        for _ in range(3):
            gm.save(_make_grid())
        assert len(gm.list_all()) == 3

    def test_update_status(self, tmp_path):
        gm = GridManager(tmp_path)
        grid = _make_grid()
        gm.save(grid)
        grid.status = "completed"
        gm.save(grid)
        assert gm.get(grid.id).status == "completed"

    def test_get_nonexistent(self, tmp_path):
        assert GridManager(tmp_path).get("grid_000000000000") is None

    def test_malformed_id_rejected(self, tmp_path):
        """grid_id 直接来自 URL 路径参数：格式不符即拒，不落到文件系统。"""
        import pytest

        gm = GridManager(tmp_path)
        for bad in (
            "nonexistent",
            "../../etc/passwd",
            "grid_../../evil",
            "grid_ABCDEF123456",
            "grid_123",
            "grid_000000000000\n",
        ):
            with pytest.raises(ValueError, match="非法宫格 ID"):
                gm.get(bad)
            with pytest.raises(ValueError, match="非法宫格 ID"):
                gm.delete(bad)

    def test_grids_dir_created(self, tmp_path):
        """GridManager creates the grids/ subdirectory automatically."""
        new_dir = tmp_path / "project"
        GridManager(new_dir)
        assert (new_dir / "grids").is_dir()

    def test_list_all_sorted_by_created_at(self, tmp_path):
        """list_all returns grids in ascending created_at order."""
        gm = GridManager(tmp_path)
        grids = [_make_grid() for _ in range(3)]
        for g in grids:
            gm.save(g)
        loaded = gm.list_all()
        assert [g.id for g in loaded] == [g.id for g in sorted(grids, key=lambda g: g.created_at)]

    def test_delete_waits_for_an_in_flight_record_update(self, tmp_path):
        gm = GridManager(tmp_path)
        grid = _make_grid()
        gm.save(grid)
        image = gm.image_path(grid.id)
        image.write_bytes(b"grid")
        update_started = threading.Event()
        release_update = threading.Event()
        delete_started = threading.Event()

        def _pause_update(current: GridGeneration) -> None:
            update_started.set()
            assert release_update.wait(timeout=5)
            current.status = "completed"

        def _delete() -> bool:
            delete_started.set()
            return gm.delete(grid.id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            update_future = pool.submit(gm.update, grid.id, _pause_update)
            assert update_started.wait(timeout=5)
            delete_future = pool.submit(_delete)
            assert delete_started.wait(timeout=5)
            try:
                with pytest.raises(TimeoutError):
                    delete_future.result(timeout=0.1)
            finally:
                release_update.set()
            assert update_future.result(timeout=5) is not None
            assert delete_future.result(timeout=5) is True

        assert gm.get(grid.id) is None
        assert not image.exists()


class TestLegacyRecordMigration:
    """两段式生命周期之前落盘的记录没有 split_at 字段，读回时按旧 status 推断切分态。"""

    def _legacy_payload(self, status: str) -> dict:
        payload = _make_grid().to_dict()
        payload["status"] = status
        del payload["split_at"]
        return payload

    def test_legacy_completed_reads_as_already_split(self):
        """旧流程只在切格落盘后才写 completed，这类记录等价于已切分。

        读成未切分会让前端提示待切分，用户照做就用旧联合图覆盖了之后单独重生成过的分镜图。
        """
        payload = self._legacy_payload("completed")
        grid = GridGeneration.from_dict(payload)
        assert grid.status == "completed"
        assert grid.split_at == payload["created_at"]

    def test_legacy_splitting_reads_as_unsplit(self):
        """splitting 是「联合图已落盘、尚未落格」的中间态，迁移后仍待切分。"""
        grid = GridGeneration.from_dict(self._legacy_payload("splitting"))
        assert grid.status == "completed"
        assert grid.split_at is None

    def test_explicit_null_split_at_stays_unsplit(self):
        """新记录显式写 null 表示未切分，不被旧记录的迁移规则波及。"""
        payload = _make_grid().to_dict()
        payload["status"] = "completed"
        payload["split_at"] = None
        assert GridGeneration.from_dict(payload).split_at is None


class TestCleanupSuperseded:
    """重生成清理规则：同脚本同集、scene_ids 是当前组子集、非在途的旧记录被删。

    HTTP 路由与 SDK 工具 (generate_grid) 共用 GridManager.cleanup_superseded，
    本类锁定规则的唯一实现。
    """

    def _save(self, gm: GridManager, *, status: str = "completed", **kwargs) -> GridGeneration:
        grid = _make_grid(**kwargs)
        grid.status = status
        gm.save(grid)
        return grid

    def test_deletes_superseded_completed_records(self, tmp_path):
        gm = GridManager(tmp_path)
        old = self._save(gm, scene_ids=["S1", "S2"])
        deleted = gm.cleanup_superseded("ep1.json", 1, {"S1", "S2", "S3", "S4"})
        assert deleted == 1
        assert gm.get(old.id) is None

    def test_deletes_superseded_manifest_claim_with_record_and_image(self, tmp_path):
        (tmp_path / "project.json").write_text(json.dumps({"schema_version": 8}), encoding="utf-8")
        gm = GridManager(tmp_path)
        old = self._save(gm, scene_ids=["S1", "S2"])
        image = gm.image_path(old.id)
        image.write_bytes(b"grid-image")
        key = ArtifactKey.episode_grid(old.episode, old.id)
        adapter = ProjectArtifactManifestAdapter(tmp_path)
        adapter.put_entry(
            key,
            ArtifactManifestEntry(
                artifact_path=image.relative_to(tmp_path).as_posix(),
                basis_digest=f"sha256-v1:{'a' * 64}",
            ),
        )

        assert gm.cleanup_superseded("ep1.json", 1, {"S1", "S2"}) == 1

        assert gm.get(old.id) is None
        assert not image.exists()
        assert adapter.get_entry(key) is None

    def test_manifest_failure_restores_superseded_record_and_image(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "project.json").write_text(json.dumps({"schema_version": 8}), encoding="utf-8")
        gm = GridManager(tmp_path)
        old = self._save(gm, scene_ids=["S1", "S2"])
        record = tmp_path / "grids" / f"{old.id}.json"
        image = gm.image_path(old.id)
        image.write_bytes(b"grid-image")
        before_record = record.read_bytes()
        key = ArtifactKey.episode_grid(old.episode, old.id)
        adapter = ProjectArtifactManifestAdapter(tmp_path)
        entry = ArtifactManifestEntry(
            artifact_path=image.relative_to(tmp_path).as_posix(),
            basis_digest=f"sha256-v1:{'b' * 64}",
        )
        adapter.put_entry(key, entry)
        before_manifest = (tmp_path / MANIFEST_FILENAME).read_bytes()

        def _fail_registration(*_args, **_kwargs):
            raise RuntimeError("manifest unavailable")

        monkeypatch.setattr("lib.artifact_activation.register_artifact_entries_atomically", _fail_registration)

        with pytest.raises(RuntimeError, match="manifest unavailable"):
            gm.cleanup_superseded("ep1.json", 1, {"S1", "S2"})

        assert record.read_bytes() == before_record
        assert image.read_bytes() == b"grid-image"
        assert (tmp_path / MANIFEST_FILENAME).read_bytes() == before_manifest
        assert adapter.get_entry(key) == entry

    def test_returns_zero_when_nothing_to_delete(self, tmp_path):
        assert GridManager(tmp_path).cleanup_superseded("ep1.json", 1, {"S1"}) == 0

    def test_skips_inflight_records(self, tmp_path):
        """pending/generating 的记录必须保留：worker 执行时还要找得到资源。"""
        gm = GridManager(tmp_path)
        pending = self._save(gm, status="pending", scene_ids=["S1", "S2"])
        generating = self._save(gm, status="generating", scene_ids=["S3"])
        deleted = gm.cleanup_superseded("ep1.json", 1, {"S1", "S2", "S3"})
        assert deleted == 0
        assert gm.get(pending.id) is not None
        assert gm.get(generating.id) is not None

    def test_skips_records_with_non_subset_scene_ids(self, tmp_path):
        """scene_ids 不是当前组子集的记录属于其它组/代，不得误删。"""
        gm = GridManager(tmp_path)
        overlap = self._save(gm, scene_ids=["S1", "S9"])
        outside = self._save(gm, scene_ids=["S9"])
        deleted = gm.cleanup_superseded("ep1.json", 1, {"S1", "S2", "S3", "S4"})
        assert deleted == 0
        assert gm.get(overlap.id) is not None
        assert gm.get(outside.id) is not None

    def test_skips_records_of_other_script_or_episode(self, tmp_path):
        gm = GridManager(tmp_path)
        other_script = self._save(gm, script_file="ep2.json", scene_ids=["S1", "S2"])
        other_episode = self._save(gm, episode=2, scene_ids=["S1", "S2"])
        deleted = gm.cleanup_superseded("ep1.json", 1, {"S1", "S2", "S3", "S4"})
        assert deleted == 0
        assert gm.get(other_script.id) is not None
        assert gm.get(other_episode.id) is not None

    def test_dedupes_many_generations(self, tmp_path):
        """反复重生成后，同一组只留下最新一批（在途记录除外）。"""
        gm = GridManager(tmp_path)
        for _ in range(3):
            self._save(gm, scene_ids=["S1", "S2", "S3", "S4"])
        self._save(gm, status="generating", scene_ids=["S1", "S2", "S3", "S4"])
        deleted = gm.cleanup_superseded("ep1.json", 1, {"S1", "S2", "S3", "S4"})
        assert deleted == 3
        assert len(gm.list_all()) == 1
