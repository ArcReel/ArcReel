"""GridManager: file-based CRUD for GridGeneration records."""

import json
import logging
from pathlib import Path

from lib.grid.models import GridGeneration

logger = logging.getLogger(__name__)


class GridManager:
    """File-based CRUD for GridGeneration records, stored in {project}/grids/."""

    def __init__(self, project_path: Path):
        self._dir = project_path / "grids"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, grid_id: str) -> Path:
        return self._dir / f"{grid_id}.json"

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

    def list_all(self) -> list[GridGeneration]:
        """Return all grids sorted by created_at ascending."""
        grids = []
        for p in self._dir.glob("grid_*.json"):
            try:
                grids.append(GridGeneration.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Skipping invalid grid file %s: %s", p.name, e)
        return sorted(grids, key=lambda g: g.created_at)
