"""GridManager: file-based CRUD for GridGeneration records."""

import json
import logging
import re
from pathlib import Path

from lib.grid.models import GridGeneration
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

    def save(self, grid: GridGeneration) -> None:
        """Write grid as JSON to {grid_id}.json."""
        path = self._path(grid.id)
        path.write_text(json.dumps(grid.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, grid_id: str) -> GridGeneration | None:
        """Read and return a GridGeneration by id, or None if not found."""
        path = self._path(grid_id)
        if not path.exists():
            return None
        return GridGeneration.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def delete(self, grid_id: str) -> bool:
        """Delete a grid record and its image file. Returns True if found and deleted."""
        path = self._path(grid_id)
        if not path.exists():
            return False
        # Also remove the grid image if it exists
        image_path = self._path(grid_id, ".png")
        if image_path.exists():
            image_path.unlink()
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
