"""Tests for GridManager file-based CRUD."""

import pytest

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
