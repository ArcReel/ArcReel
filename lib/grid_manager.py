"""GridManager: file-based CRUD for GridGeneration records."""

import json
import logging
import re
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import portalocker

from lib.formal_write import formal_write_transaction
from lib.grid.models import GridGeneration
from lib.json_io import atomic_write_json
from lib.path_safety import safe_join

logger = logging.getLogger(__name__)

# 与 lib/grid/models.py::GridGeneration.create 的生成格式一致
_GRID_ID_RE = re.compile(r"grid_[0-9a-f]{12}")


class GridManager:
    """File-based CRUD for GridGeneration records, stored in {project}/grids/."""

    def __init__(self, project_path: Path):
        self._dir = project_path / "grids"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, grid_id: str, suffix: str = ".json") -> Path:
        """grids/ 下的记录路径。grid_id 来自 URL 路径参数，先卡格式白名单再过越界校验。

        用 ``fullmatch`` 而非 ``match()`` + ``$``：``$`` 会匹配字符串末尾换行符之前的
        位置，``grid_xxxxxxxxxxxx\\n`` 这类带尾随换行的输入能骗过 ``match()``，让换行符
        混入最终文件名。
        """
        if not isinstance(grid_id, str) or _GRID_ID_RE.fullmatch(grid_id) is None:
            raise ValueError(f"非法宫格 ID: {grid_id!r}")
        return safe_join(self._dir, f"{grid_id}{suffix}")

    def image_path(self, grid_id: str) -> Path:
        """grids/ 下的联合图路径。调用方一律经此取路径，不自行拼接，
        否则 ID 白名单与越界校验会被绕过。"""
        return self._path(grid_id, ".png")

    def save(self, grid: GridGeneration) -> None:
        """Write grid as JSON to {grid_id}.json."""
        path = self._path(grid.id)
        with self._record_lock(path):
            atomic_write_json(path, grid.to_dict())

    def get(self, grid_id: str) -> GridGeneration | None:
        """Read and return a GridGeneration by id, or None if not found."""
        path = self._path(grid_id)
        with self._record_lock(path):
            return self._get_unlocked(path)

    def update(
        self,
        grid_id: str,
        mutate: Callable[[GridGeneration], None],
        *,
        on_commit: Callable[[], None] | None = None,
        ignore_invalid: bool = False,
    ) -> GridGeneration | None:
        """Read, mutate, and save one record under its canonical file lock."""

        path = self._path(grid_id)
        with self._record_lock(path), formal_write_transaction(path):
            try:
                grid = self._get_unlocked(path)
            except Exception:  # noqa: BLE001 - optional best-effort restore semantics apply only to the read
                if not ignore_invalid:
                    raise
                return None
            if grid is None:
                return None
            mutate(grid)
            atomic_write_json(path, grid.to_dict())
            if on_commit is not None:
                on_commit()
            return grid

    @staticmethod
    def _get_unlocked(path: Path) -> GridGeneration | None:
        if not path.exists():
            return None
        return GridGeneration.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    @contextmanager
    def _record_lock(path: Path):
        lock_path = path.parent / f".{path.name}.lock"
        lock_path.touch(exist_ok=True)
        with portalocker.Lock(lock_path, flags=portalocker.LOCK_EX):
            yield

    def delete(self, grid_id: str) -> bool:
        """Delete a grid record and its image file. Returns True if found and deleted."""
        path = self._path(grid_id)
        if not path.exists():
            return False
        # Also remove the grid image if it exists
        image_file = self.image_path(grid_id)
        if image_file.exists():
            image_file.unlink()
        path.unlink()
        return True

    def list_all(self) -> list[GridGeneration]:
        """Return all grids sorted by created_at ascending."""
        grids = []
        for p in self._dir.glob("grid_*.json"):
            try:
                grids.append(GridGeneration.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Skipping invalid grid file %s: %s", p.name, e)
        return sorted(grids, key=lambda g: g.created_at)

    def cleanup_superseded(self, script_file: str, episode: int, scene_ids: set[str]) -> int:
        """Delete finished grid records superseded by a regenerate of ``scene_ids``.

        A record is superseded when it belongs to the same script and episode, its
        ``scene_ids`` are a subset of the freshly generated group, and it is not still
        in flight (pending/generating). In-flight records are kept so the generation
        worker can still find its resource.

        This is the single cleanup rule shared by the HTTP route and the SDK tool so
        both regenerate paths stop accumulating stale grid generations.

        Returns the number of deleted records.
        """
        deleted = 0
        for old in self.list_all():
            if (
                old.script_file == script_file
                and old.episode == episode
                and old.status not in ("pending", "generating")
                and old.scene_ids
                and set(old.scene_ids) <= scene_ids
            ):
                if self.delete(old.id):
                    deleted += 1
        return deleted
