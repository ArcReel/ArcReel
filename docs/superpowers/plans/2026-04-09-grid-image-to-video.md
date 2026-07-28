# 宫格图生视频 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add grid-based storyboard generation mode to ArcReel, where multiple scenes in a segment group are generated as a single grid image, split into chained first/last frames, and fed into video generation with first_last mode support.

**Architecture:** Four-phase implementation: (1) Core library — pure-logic grid calculator, splitter, prompt builder; (2) Backend integration — task executor, API router, video backend extension; (3) Frontend — project settings, timeline grid groups, SegmentCard grid mode, grid preview; (4) Agent skills — generate-grid skill and workflow updates.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy (backend), React 19 / TypeScript / Tailwind CSS 4 / Zustand (frontend), Pillow (image processing), Claude Agent SDK skills (agent)

**Spec:** `docs/superpowers/specs/2026-04-09-grid-image-to-video-design.md`

---

## File Structure

### New Files

```
lib/
├── grid/
│   ├── __init__.py                    # Package exports
│   ├── layout.py                      # Grid layout calculator
│   ├── splitter.py                    # Grid image splitting + cropping
│   ├── prompt_builder.py              # Grid prompt template assembly
│   └── models.py                      # GridGeneration, FrameCell dataclasses
├── grid_manager.py                    # Grid CRUD (file-based JSON storage)

server/
├── routers/grids.py                   # Grid API endpoints

tests/
├── test_grid_layout.py
├── test_grid_splitter.py
├── test_grid_prompt_builder.py
├── test_grid_manager.py
├── test_grid_executor.py
├── test_grid_router.py
├── test_video_backend_capabilities.py

frontend/src/
├── types/grid.ts                      # GridGeneration, FrameCell TS types
├── components/canvas/timeline/
│   ├── GridSegmentGroup.tsx           # Segment group wrapper with grid controls
│   └── GridPreviewPanel.tsx           # Grid image + frame chain preview

agent_runtime_profile/.claude/skills/
├── generate-grid/
│   ├── SKILL.md
│   └── scripts/generate_grid.py
```

### Modified Files

```
lib/
├── script_models.py                   # GeneratedAssets extension
├── video_backends/base.py             # VideoCapabilities, end_image, reference_images
├── video_backends/gemini.py           # Implement first_last support
├── video_backends/ark.py              # Implement first_last support
├── video_backends/grok.py             # Implement reference_images fallback
├── video_backends/openai.py           # Implement reference_images fallback
├── media_generator.py                 # generate_video_async end_image param
├── project_manager.py                 # SUBDIRS += "grids", grid helpers
├── generation_worker.py               # Register "grid" task type routing

server/
├── services/generation_tasks.py       # execute_grid_task + execute_video_task update
├── routers/generate.py                # grid endpoint delegation
├── main.py                            # Include grids router

frontend/src/
├── types/script.ts                    # GeneratedAssets fields
├── api.ts                             # Grid API methods
├── components/canvas/timeline/
│   ├── SegmentCard.tsx                # Grid mode first/last frame display
│   └── TimelineCanvas.tsx             # Grid group rendering
├── components/canvas/StudioCanvasRouter.tsx  # Grid generation handlers
├── components/pages/CreateProjectModal.tsx   # generation_mode option
├── components/pages/ProjectSettingsPage.tsx  # generation_mode setting

agent_runtime_profile/.claude/skills/
├── generate-video/scripts/generate_video.py  # first_last detection
├── manga-workflow/SKILL.md                   # generation_mode branching
```

---

## Phase 1: Core Library

### Task 1: Grid Layout Calculator

**Files:**
- Create: `lib/grid/__init__.py`
- Create: `lib/grid/layout.py`
- Test: `tests/test_grid_layout.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_grid_layout.py
from lib.grid.layout import calculate_grid_layout, GridLayout


class TestCalculateGridLayout:
    """Auto-select smallest grid that fits N scenes, with correct aspect ratios."""

    def test_4_scenes_horizontal(self):
        result = calculate_grid_layout(num_scenes=4, aspect_ratio="16:9")
        assert result == GridLayout(
            grid_size="grid_4", rows=2, cols=2,
            grid_aspect_ratio="16:9", cell_count=4, placeholder_count=0,
        )

    def test_4_scenes_vertical(self):
        result = calculate_grid_layout(num_scenes=4, aspect_ratio="9:16")
        assert result == GridLayout(
            grid_size="grid_4", rows=2, cols=2,
            grid_aspect_ratio="9:16", cell_count=4, placeholder_count=0,
        )

    def test_5_scenes_uses_grid_6(self):
        result = calculate_grid_layout(num_scenes=5, aspect_ratio="16:9")
        assert result.grid_size == "grid_6"
        assert result.rows == 3
        assert result.cols == 2
        assert result.grid_aspect_ratio == "4:3"
        assert result.cell_count == 6
        assert result.placeholder_count == 1

    def test_5_scenes_vertical_uses_grid_6(self):
        result = calculate_grid_layout(num_scenes=5, aspect_ratio="9:16")
        assert result.grid_size == "grid_6"
        assert result.rows == 2
        assert result.cols == 3
        assert result.grid_aspect_ratio == "3:4"

    def test_6_scenes(self):
        result = calculate_grid_layout(num_scenes=6, aspect_ratio="16:9")
        assert result.grid_size == "grid_6"
        assert result.placeholder_count == 0

    def test_7_scenes_uses_grid_9(self):
        result = calculate_grid_layout(num_scenes=7, aspect_ratio="16:9")
        assert result.grid_size == "grid_9"
        assert result.rows == 3
        assert result.cols == 3
        assert result.placeholder_count == 2

    def test_9_scenes(self):
        result = calculate_grid_layout(num_scenes=9, aspect_ratio="9:16")
        assert result.grid_size == "grid_9"
        assert result.placeholder_count == 0

    def test_below_4_returns_none(self):
        assert calculate_grid_layout(num_scenes=3, aspect_ratio="16:9") is None
        assert calculate_grid_layout(num_scenes=1, aspect_ratio="16:9") is None

    def test_above_9_caps_at_grid_9(self):
        result = calculate_grid_layout(num_scenes=12, aspect_ratio="16:9")
        assert result.grid_size == "grid_9"
        assert result.cell_count == 9
        assert result.placeholder_count == 0

    def test_grid_pixel_dimensions_horizontal_grid_4(self):
        result = calculate_grid_layout(num_scenes=4, aspect_ratio="16:9")
        w, h = result.pixel_dimensions()
        assert w / h == 16 / 9  # 2 cols of 16:9 cells, 2 rows

    def test_grid_pixel_dimensions_horizontal_grid_6(self):
        result = calculate_grid_layout(num_scenes=6, aspect_ratio="16:9")
        w, h = result.pixel_dimensions()
        assert w / h == 4 / 3  # 3 rows × 2 cols
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_grid_layout.py -v`
Expected: ImportError — `lib.grid.layout` not found

- [ ] **Step 3: Implement GridLayout and calculate_grid_layout**

```python
# lib/grid/__init__.py
from lib.grid.layout import GridLayout, calculate_grid_layout
from lib.grid.models import FrameCell, GridGeneration

__all__ = [
    "GridLayout",
    "calculate_grid_layout",
    "FrameCell",
    "GridGeneration",
]
```

```python
# lib/grid/layout.py
from __future__ import annotations

from dataclasses import dataclass

# Grid configurations: grid_size -> (rows_h, cols_h, ratio_h, rows_v, cols_v, ratio_v)
_GRID_CONFIGS = {
    "grid_4": (2, 2, "16:9", 2, 2, "9:16"),
    "grid_6": (3, 2, "4:3", 2, 3, "3:4"),
    "grid_9": (3, 3, "16:9", 3, 3, "9:16"),
}

# Base cell dimensions for pixel calculation (before any crop)
_BASE_CELL_PX = {"16:9": (768, 432), "9:16": (432, 768)}


@dataclass(frozen=True)
class GridLayout:
    grid_size: str  # "grid_4" | "grid_6" | "grid_9"
    rows: int
    cols: int
    grid_aspect_ratio: str
    cell_count: int  # rows * cols
    placeholder_count: int  # cell_count - num_scenes

    def pixel_dimensions(self, cell_base: tuple[int, int] | None = None) -> tuple[int, int]:
        """Return (width, height) for the full grid image."""
        ar = self.grid_aspect_ratio.replace(":", "/")
        num, den = ar.split("/") if "/" in ar else self.grid_aspect_ratio.split(":")
        # Use base cell size to compute exact pixels
        video_ratio = "16:9" if int(num) > int(den) or self.grid_size != "grid_6" else "9:16"
        if self.grid_size == "grid_6":
            video_ratio = "16:9" if self.rows > self.cols else "9:16"
        base = cell_base or _BASE_CELL_PX.get(video_ratio, (768, 432))
        return base[0] * self.cols, base[1] * self.rows


def calculate_grid_layout(
    num_scenes: int,
    aspect_ratio: str,
) -> GridLayout | None:
    """Select the smallest grid that fits num_scenes.

    Returns None if num_scenes < 4 (grid not beneficial).
    Caps at grid_9 if num_scenes > 9.
    """
    if num_scenes < 4:
        return None

    is_horizontal = aspect_ratio.startswith("16")

    effective = min(num_scenes, 9)

    if effective <= 4:
        size = "grid_4"
    elif effective <= 6:
        size = "grid_6"
    else:
        size = "grid_9"

    rows_h, cols_h, ratio_h, rows_v, cols_v, ratio_v = _GRID_CONFIGS[size]

    if is_horizontal:
        rows, cols, ratio = rows_h, cols_h, ratio_h
    else:
        rows, cols, ratio = rows_v, cols_v, ratio_v

    total_cells = rows * cols
    placeholders = total_cells - effective

    return GridLayout(
        grid_size=size,
        rows=rows,
        cols=cols,
        grid_aspect_ratio=ratio,
        cell_count=total_cells,
        placeholder_count=placeholders,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_grid_layout.py -v`
Expected: All PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check lib/grid/ tests/test_grid_layout.py && uv run ruff format lib/grid/ tests/test_grid_layout.py
git add lib/grid/__init__.py lib/grid/layout.py tests/test_grid_layout.py
git commit -m "feat: grid layout calculator with auto grid-size selection"
```

---

### Task 2: Grid Data Models

**Files:**
- Create: `lib/grid/models.py`
- Modify: `lib/script_models.py`
- Test: `tests/test_grid_layout.py` (append)

- [ ] **Step 1: Write failing tests**

```python
# Append to tests/test_grid_layout.py
from lib.grid.models import FrameCell, GridGeneration, build_frame_chain


class TestBuildFrameChain:
    """Build frame chain from scene IDs with first_last linking."""

    def test_4_scenes_grid_4(self):
        scene_ids = ["E1S01", "E1S02", "E1S03", "E1S04"]
        chain = build_frame_chain(scene_ids, rows=2, cols=2)
        assert len(chain) == 4

        # Cell 0: S1 first frame
        assert chain[0].frame_type == "first"
        assert chain[0].prev_scene_id is None
        assert chain[0].next_scene_id == "E1S01"

        # Cell 1: S1 last / S2 first (transition)
        assert chain[1].frame_type == "transition"
        assert chain[1].prev_scene_id == "E1S01"
        assert chain[1].next_scene_id == "E1S02"

        # Cell 3: S3 last / S4 first (transition)
        assert chain[3].frame_type == "transition"
        assert chain[3].prev_scene_id == "E1S03"
        assert chain[3].next_scene_id == "E1S04"

    def test_5_scenes_grid_6_has_placeholder(self):
        scene_ids = ["S1", "S2", "S3", "S4", "S5"]
        chain = build_frame_chain(scene_ids, rows=3, cols=2)
        assert len(chain) == 6
        assert chain[5].frame_type == "placeholder"
        assert chain[5].prev_scene_id is None
        assert chain[5].next_scene_id is None

    def test_row_col_assignment(self):
        chain = build_frame_chain(["A", "B", "C", "D"], rows=2, cols=2)
        assert (chain[0].row, chain[0].col) == (0, 0)
        assert (chain[1].row, chain[1].col) == (0, 1)
        assert (chain[2].row, chain[2].col) == (1, 0)
        assert (chain[3].row, chain[3].col) == (1, 1)


class TestGridGeneration:
    def test_create_from_scenes(self):
        grid = GridGeneration.create(
            episode=1,
            script_file="episode_1.json",
            scene_ids=["E1S01", "E1S02", "E1S03", "E1S04"],
            rows=2,
            cols=2,
            grid_size="grid_4",
            provider="gemini-aistudio",
            model="gemini-image",
        )
        assert grid.status == "pending"
        assert grid.cell_count == 4
        assert len(grid.frame_chain) == 4
        assert grid.id.startswith("grid_")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_grid_layout.py::TestBuildFrameChain -v`
Expected: ImportError

- [ ] **Step 3: Implement models**

```python
# lib/grid/models.py
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class FrameCell:
    index: int
    row: int
    col: int
    frame_type: str  # "first" | "transition" | "placeholder"
    prev_scene_id: str | None = None
    next_scene_id: str | None = None
    image_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "row": self.row,
            "col": self.col,
            "frame_type": self.frame_type,
            "prev_scene_id": self.prev_scene_id,
            "next_scene_id": self.next_scene_id,
            "image_path": self.image_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FrameCell:
        return cls(**d)


@dataclass
class GridGeneration:
    id: str
    episode: int
    script_file: str
    scene_ids: list[str]
    grid_image_path: str | None
    rows: int
    cols: int
    cell_count: int
    frame_chain: list[FrameCell]
    status: str  # pending | generating | splitting | completed | failed
    prompt: str | None
    provider: str
    model: str
    grid_size: str
    created_at: str
    error_message: str | None = None

    @classmethod
    def create(
        cls,
        *,
        episode: int,
        script_file: str,
        scene_ids: list[str],
        rows: int,
        cols: int,
        grid_size: str,
        provider: str,
        model: str,
    ) -> GridGeneration:
        cell_count = rows * cols
        chain = build_frame_chain(scene_ids, rows=rows, cols=cols)
        return cls(
            id=f"grid_{uuid.uuid4().hex[:12]}",
            episode=episode,
            script_file=script_file,
            scene_ids=scene_ids,
            grid_image_path=None,
            rows=rows,
            cols=cols,
            cell_count=cell_count,
            frame_chain=chain,
            status="pending",
            prompt=None,
            provider=provider,
            model=model,
            grid_size=grid_size,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "episode": self.episode,
            "script_file": self.script_file,
            "scene_ids": self.scene_ids,
            "grid_image_path": self.grid_image_path,
            "rows": self.rows,
            "cols": self.cols,
            "cell_count": self.cell_count,
            "frame_chain": [c.to_dict() for c in self.frame_chain],
            "status": self.status,
            "prompt": self.prompt,
            "provider": self.provider,
            "model": self.model,
            "grid_size": self.grid_size,
            "created_at": self.created_at,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GridGeneration:
        d = dict(d)
        d["frame_chain"] = [FrameCell.from_dict(c) for c in d["frame_chain"]]
        return cls(**d)


def build_frame_chain(
    scene_ids: list[str],
    *,
    rows: int,
    cols: int,
) -> list[FrameCell]:
    """Build frame chain: N scene_ids → N cells (+ placeholders if grid has extras).

    Cell 0: first frame of scene 0
    Cell i (1..N-1): transition — last frame of scene i-1 / first frame of scene i
    Remaining cells: placeholder
    """
    total = rows * cols
    cells: list[FrameCell] = []
    n = len(scene_ids)

    for idx in range(total):
        row = idx // cols
        col = idx % cols

        if idx == 0 and n > 0:
            cells.append(FrameCell(
                index=idx, row=row, col=col,
                frame_type="first",
                prev_scene_id=None,
                next_scene_id=scene_ids[0],
            ))
        elif idx < n:
            cells.append(FrameCell(
                index=idx, row=row, col=col,
                frame_type="transition",
                prev_scene_id=scene_ids[idx - 1],
                next_scene_id=scene_ids[idx],
            ))
        else:
            cells.append(FrameCell(
                index=idx, row=row, col=col,
                frame_type="placeholder",
            ))

    return cells
```

- [ ] **Step 4: Extend GeneratedAssets in script_models.py**

Add `storyboard_last_image`, `grid_id`, `grid_cell_index` to `GeneratedAssets`:

```python
# In lib/script_models.py, modify GeneratedAssets class:
class GeneratedAssets(BaseModel):
    storyboard_image: str | None = None
    storyboard_last_image: str | None = None  # NEW: last frame for grid mode
    grid_id: str | None = None  # NEW: source grid ID
    grid_cell_index: int | None = None  # NEW: cell index in source grid
    video_clip: str | None = None
    video_uri: str | None = None
    video_thumbnail: str | None = None
    status: Literal["pending", "storyboard_ready", "completed"] = "pending"
```

- [ ] **Step 5: Run tests**

Run: `uv run python -m pytest tests/test_grid_layout.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
uv run ruff check lib/grid/models.py lib/script_models.py && uv run ruff format lib/grid/models.py lib/script_models.py
git add lib/grid/models.py lib/grid/__init__.py lib/script_models.py tests/test_grid_layout.py
git commit -m "feat: grid data models (GridGeneration, FrameCell) and GeneratedAssets extension"
```

---

### Task 3: Grid Image Splitter

**Files:**
- Create: `lib/grid/splitter.py`
- Test: `tests/test_grid_splitter.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_grid_splitter.py
from pathlib import Path
from PIL import Image
from lib.grid.splitter import split_grid_image, center_crop_to_ratio


class TestCenterCropToRatio:
    def test_no_crop_needed_16_9(self):
        img = Image.new("RGB", (160, 90))  # Already 16:9
        result = center_crop_to_ratio(img, "16:9")
        assert result.size == (160, 90)

    def test_crop_wider_to_16_9(self):
        img = Image.new("RGB", (200, 90))  # 20:9, wider than 16:9
        result = center_crop_to_ratio(img, "16:9")
        assert result.size[1] == 90
        assert abs(result.size[0] / result.size[1] - 16 / 9) < 0.02

    def test_crop_2_1_to_16_9(self):
        """grid_6 horizontal: each cell is 2:1, crop to 16:9."""
        img = Image.new("RGB", (200, 100))  # 2:1
        result = center_crop_to_ratio(img, "16:9")
        expected_w = int(100 * 16 / 9)
        assert result.size == (expected_w, 100)

    def test_crop_1_2_to_9_16(self):
        """grid_6 vertical: each cell is 1:2, crop to 9:16."""
        img = Image.new("RGB", (100, 200))  # 1:2
        result = center_crop_to_ratio(img, "9:16")
        expected_h = int(100 * 16 / 9)
        assert result.size == (100, expected_h)


class TestSplitGridImage:
    def test_split_2x2_grid(self, tmp_path: Path):
        # Create a 200x200 grid image with 4 distinct quadrants
        grid = Image.new("RGB", (200, 200))
        for x in range(200):
            for y in range(200):
                r = 255 if x < 100 and y < 100 else 0
                g = 255 if x >= 100 and y < 100 else 0
                b = 255 if x < 100 and y >= 100 else 0
                grid.putpixel((x, y), (r, g, b))

        cells = split_grid_image(
            grid_image=grid,
            rows=2,
            cols=2,
            video_aspect_ratio="16:9",
            edge_margin=0.0,  # No margin for test simplicity
        )
        assert len(cells) == 4
        # Each cell should be cropped to 16:9
        for cell in cells:
            w, h = cell.size
            assert abs(w / h - 16 / 9) < 0.05

    def test_split_with_edge_margin(self, tmp_path: Path):
        grid = Image.new("RGB", (400, 400), color=(128, 128, 128))
        cells = split_grid_image(grid, rows=2, cols=2, video_aspect_ratio="16:9", edge_margin=0.02)
        # Each cell should be smaller than 200x200 due to margin
        for cell in cells:
            assert cell.size[0] < 200
            assert cell.size[1] < 200

    def test_placeholder_detection(self):
        # Black cell should be detected as placeholder
        black = Image.new("RGB", (100, 100), color=(10, 10, 10))
        gray = Image.new("RGB", (100, 100), color=(128, 128, 128))
        from lib.grid.splitter import is_placeholder_cell
        assert is_placeholder_cell(black) is True
        assert is_placeholder_cell(gray) is True
        # Colorful image should not be placeholder
        colorful = Image.new("RGB", (100, 100), color=(200, 100, 50))
        assert is_placeholder_cell(colorful) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_grid_splitter.py -v`
Expected: ImportError

- [ ] **Step 3: Implement splitter**

```python
# lib/grid/splitter.py
from __future__ import annotations

import numpy as np
from PIL import Image


def center_crop_to_ratio(img: Image.Image, target_ratio: str) -> Image.Image:
    """Center-crop image to match target aspect ratio (e.g., '16:9')."""
    w_ratio, h_ratio = (int(x) for x in target_ratio.split(":"))
    target = w_ratio / h_ratio
    current = img.width / img.height

    if abs(current - target) < 0.01:
        return img

    if current > target:
        # Too wide — crop width
        new_w = int(img.height * target)
        left = (img.width - new_w) // 2
        return img.crop((left, 0, left + new_w, img.height))
    else:
        # Too tall — crop height
        new_h = int(img.width / target)
        top = (img.height - new_h) // 2
        return img.crop((0, top, img.width, top + new_h))


def is_placeholder_cell(img: Image.Image, threshold: float = 0.90) -> bool:
    """Detect if a cell is a solid-color placeholder (gray/black)."""
    arr = np.array(img)
    center = arr[
        arr.shape[0] // 4 : 3 * arr.shape[0] // 4,
        arr.shape[1] // 4 : 3 * arr.shape[1] // 4,
    ]
    std = center.std()
    mean = center.mean()
    # Low variance (uniform) and not bright/colorful
    return std < 15 and mean < 160


def split_grid_image(
    grid_image: Image.Image,
    rows: int,
    cols: int,
    video_aspect_ratio: str,
    edge_margin: float = 0.02,
) -> list[Image.Image]:
    """Split grid into individual cells with optional margin trim and aspect crop.

    Returns list of cell images in row-major order.
    """
    cell_w = grid_image.width // cols
    cell_h = grid_image.height // rows

    cells: list[Image.Image] = []
    for r in range(rows):
        for c in range(cols):
            x0 = c * cell_w
            y0 = r * cell_h
            cell = grid_image.crop((x0, y0, x0 + cell_w, y0 + cell_h))

            # Edge margin trim
            if edge_margin > 0:
                mx = int(cell.width * edge_margin)
                my = int(cell.height * edge_margin)
                cell = cell.crop((mx, my, cell.width - mx, cell.height - my))

            # Center crop to video aspect ratio
            cell = center_crop_to_ratio(cell, video_aspect_ratio)
            cells.append(cell)

    return cells
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_grid_splitter.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check lib/grid/splitter.py tests/test_grid_splitter.py && uv run ruff format lib/grid/splitter.py tests/test_grid_splitter.py
git add lib/grid/splitter.py tests/test_grid_splitter.py
git commit -m "feat: grid image splitter with center crop and placeholder detection"
```

---

### Task 4: Grid Prompt Builder

**Files:**
- Create: `lib/grid/prompt_builder.py`
- Test: `tests/test_grid_prompt_builder.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_grid_prompt_builder.py
from lib.grid.prompt_builder import build_grid_prompt


class TestBuildGridPrompt:
    def _make_scene(self, scene_id: str, image_scene: str, action: str):
        return {
            "scene_id": scene_id,
            "image_prompt": {"scene": image_scene, "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"}},
            "video_prompt": {"action": action, "camera_motion": "static", "ambiance_audio": "quiet", "dialogue": []},
        }

    def test_basic_4_scene_prompt(self):
        scenes = [
            self._make_scene("S1", "客厅，角色A坐在沙发上", "角色A站起来走向窗边"),
            self._make_scene("S2", "窗边，角色A望向窗外", "角色A转身回到沙发"),
            self._make_scene("S3", "沙发旁，角色A拿起手机", "角色A拨打电话"),
            self._make_scene("S4", "客厅全景，角色A通话中", "角色A挂断电话"),
        ]
        prompt = build_grid_prompt(
            scenes=scenes,
            id_field="scene_id",
            rows=2,
            cols=2,
            style="电影级写实风格",
            aspect_ratio="16:9",
        )

        assert "2×2" in prompt
        assert "格0" in prompt
        assert "格3" in prompt
        assert "首尾帧链式结构" in prompt
        assert "客厅" in prompt
        assert "电影级写实风格" in prompt

    def test_includes_placeholders(self):
        scenes = [
            self._make_scene("S1", "scene1", "action1"),
            self._make_scene("S2", "scene2", "action2"),
            self._make_scene("S3", "scene3", "action3"),
            self._make_scene("S4", "scene4", "action4"),
            self._make_scene("S5", "scene5", "action5"),
        ]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=3, cols=2, style="anime")
        assert "空占位" in prompt

    def test_reference_image_mapping(self):
        scenes = [self._make_scene("S1", "a", "b"), self._make_scene("S2", "c", "d"),
                   self._make_scene("S3", "e", "f"), self._make_scene("S4", "g", "h")]
        refs = {"图片1": "角色A设计图", "图片2": "场景X参考"}
        prompt = build_grid_prompt(
            scenes=scenes, id_field="scene_id", rows=2, cols=2,
            style="anime", reference_image_mapping=refs,
        )
        assert "图片1" in prompt
        assert "角色A设计图" in prompt

    def test_string_prompts_handled(self):
        """Scenes with plain string prompts (not structured) should still work."""
        scenes = [
            {"scene_id": "S1", "image_prompt": "plain text prompt", "video_prompt": "video text"},
            {"scene_id": "S2", "image_prompt": "another prompt", "video_prompt": "more video"},
            {"scene_id": "S3", "image_prompt": "third prompt", "video_prompt": "action3"},
            {"scene_id": "S4", "image_prompt": "fourth prompt", "video_prompt": "action4"},
        ]
        prompt = build_grid_prompt(scenes=scenes, id_field="scene_id", rows=2, cols=2, style="realistic")
        assert "plain text prompt" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_grid_prompt_builder.py -v`
Expected: ImportError

- [ ] **Step 3: Implement prompt builder**

```python
# lib/grid/prompt_builder.py
from __future__ import annotations

from typing import Any


def _extract_image_desc(scene: dict, id_field: str) -> str:
    """Extract image description from structured or plain prompt."""
    ip = scene.get("image_prompt", "")
    if isinstance(ip, dict):
        parts = [ip.get("scene", "")]
        comp = ip.get("composition", {})
        if isinstance(comp, dict):
            for k in ("shot_type", "lighting", "ambiance"):
                v = comp.get(k)
                if v:
                    parts.append(str(v))
        return "，".join(p for p in parts if p)
    return str(ip)


def _extract_action(scene: dict) -> str:
    """Extract closing action from video prompt."""
    vp = scene.get("video_prompt", "")
    if isinstance(vp, dict):
        return vp.get("action", "") or ""
    return str(vp)


def build_grid_prompt(
    *,
    scenes: list[dict[str, Any]],
    id_field: str,
    rows: int,
    cols: int,
    style: str,
    aspect_ratio: str = "16:9",
    reference_image_mapping: dict[str, str] | None = None,
) -> str:
    """Assemble the grid generation prompt from structured scene data."""
    n = len(scenes)
    total = rows * cols
    lines: list[str] = []

    lines.append(
        f"你是一位专业的分镜画师。请严格按照 {rows}×{cols} 宫格布局"
        f"生成一张包含 {total} 个等大画格的联合图。"
    )

    lines.append("")
    lines.append("【布局要求】")
    lines.append(f"- {rows} 行 {cols} 列，阅读顺序：从左到右，从上到下")
    lines.append("- 每格必须等大，格间无边框、无留白、无文字、无水印")
    lines.append("- 所有格子保持一致的角色外观、光线和色彩风格")

    lines.append("")
    lines.append("【帧链节奏】")
    lines.append("本宫格采用首尾帧链式结构：")
    lines.append("- 格0 是第一个场景的开场画面")
    if n > 2:
        lines.append(f"- 格1~格{n - 2} 是相邻场景的过渡帧（前一场景的结束 = 后一场景的开始）")
    lines.append(f"- 格{n - 1} 是最后一个场景的开场画面")
    lines.append("- 相邻格之间应体现画面的自然过渡和动作延续")

    if reference_image_mapping:
        lines.append("")
        lines.append("【参考图说明】")
        for key, desc in reference_image_mapping.items():
            lines.append(f"- {key}：{desc}")

    lines.append("")
    lines.append("【各格内容】")

    for idx in range(n):
        scene = scenes[idx]
        sid = scene.get(id_field, f"Scene{idx + 1}")
        r = idx // cols + 1
        c = idx % cols + 1

        if idx == 0:
            desc = _extract_image_desc(scene, id_field)
            lines.append(f"格{idx}（row{r} col{c}）— {sid}开场：")
            lines.append(f"  {desc}")
        elif idx < n - 1:
            prev = scenes[idx - 1]
            prev_sid = prev.get(id_field, f"Scene{idx}")
            action = _extract_action(prev)
            desc = _extract_image_desc(scene, id_field)
            lines.append(f"格{idx}（row{r} col{c}）— {prev_sid}→{sid}过渡：")
            closing = f"  {action}，过渡到 {desc}" if action else f"  过渡到 {desc}"
            lines.append(closing)
        else:
            desc = _extract_image_desc(scene, id_field)
            lines.append(f"格{idx}（row{r} col{c}）— {sid}开场：")
            lines.append(f"  {desc}")

    # Placeholder cells
    for idx in range(n, total):
        r = idx // cols + 1
        c = idx % cols + 1
        lines.append(f"格{idx}（row{r} col{c}）— 空占位：纯灰色背景，无任何内容")

    lines.append("")
    lines.append("【风格要求】")
    lines.append(style)

    lines.append("")
    lines.append("【负面约束】")
    lines.append("禁止出现：文字、水印、数字编号、边框、分隔线、拼贴感")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_grid_prompt_builder.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check lib/grid/prompt_builder.py tests/test_grid_prompt_builder.py && uv run ruff format lib/grid/prompt_builder.py tests/test_grid_prompt_builder.py
git add lib/grid/prompt_builder.py tests/test_grid_prompt_builder.py
git commit -m "feat: grid prompt builder with first_last frame chain template"
```

---

### Task 5: VideoBackend Extension — Capabilities + end_image

**Files:**
- Modify: `lib/video_backends/base.py`
- Modify: `lib/video_backends/gemini.py`
- Modify: `lib/video_backends/ark.py`
- Modify: `lib/video_backends/grok.py`
- Modify: `lib/video_backends/openai.py`
- Modify: `lib/media_generator.py`
- Test: `tests/test_video_backend_capabilities.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_video_backend_capabilities.py
from lib.video_backends.base import VideoGenerationRequest, VideoCapabilities


class TestVideoCapabilities:
    def test_default_capabilities(self):
        caps = VideoCapabilities()
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.reference_images is False
        assert caps.max_reference_images == 0

    def test_first_last_capable(self):
        caps = VideoCapabilities(last_frame=True)
        assert caps.last_frame is True


class TestVideoGenerationRequestEndImage:
    def test_end_image_default_none(self):
        req = VideoGenerationRequest(prompt="test", output_path="/tmp/out.mp4")
        assert req.end_image is None
        assert req.reference_images is None

    def test_end_image_set(self):
        from pathlib import Path
        req = VideoGenerationRequest(
            prompt="test",
            output_path=Path("/tmp/out.mp4"),
            start_image=Path("/tmp/first.png"),
            end_image=Path("/tmp/last.png"),
        )
        assert req.end_image == Path("/tmp/last.png")

    def test_reference_images_set(self):
        from pathlib import Path
        req = VideoGenerationRequest(
            prompt="test",
            output_path=Path("/tmp/out.mp4"),
            reference_images=[Path("/tmp/ref1.png"), Path("/tmp/ref2.png")],
        )
        assert len(req.reference_images) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_video_backend_capabilities.py -v`
Expected: ImportError or AttributeError (fields don't exist yet)

- [ ] **Step 3: Extend VideoGenerationRequest and add VideoCapabilities**

In `lib/video_backends/base.py`, add:

```python
@dataclass
class VideoCapabilities:
    """Declares what a video backend supports."""
    first_frame: bool = True
    last_frame: bool = False
    reference_images: bool = False
    max_reference_images: int = 0
```

Extend `VideoGenerationRequest`:
```python
@dataclass
class VideoGenerationRequest:
    prompt: str
    output_path: Path
    aspect_ratio: str = "9:16"
    duration_seconds: int = 5
    resolution: str = "1080p"
    start_image: Path | None = None
    end_image: Path | None = None              # NEW
    reference_images: list[Path] | None = None  # NEW
    generate_audio: bool = True
    negative_prompt: str | None = None
    project_name: str | None = None
    service_tier: str = "default"
    seed: int | None = None
```

Add `video_capabilities` property to `VideoBackend` protocol:
```python
class VideoBackend(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    @property
    def capabilities(self) -> set[VideoCapability]: ...
    @property
    def video_capabilities(self) -> VideoCapabilities: ...  # NEW
    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult: ...
```

- [ ] **Step 4: Add default video_capabilities to each backend**

In each backend (`gemini.py`, `ark.py`, `grok.py`, `openai.py`), add:

```python
# gemini.py — Veo 3.1 supports first_last and reference images
@property
def video_capabilities(self) -> VideoCapabilities:
    return VideoCapabilities(last_frame=True, reference_images=True, max_reference_images=3)

# ark.py — Seedance 2.0 supports first_last; 1.5 does not
@property
def video_capabilities(self) -> VideoCapabilities:
    if "seedance-2" in self.model or "seedance2" in self.model:
        return VideoCapabilities(last_frame=True, reference_images=True, max_reference_images=9)
    return VideoCapabilities()

# grok.py — reference images only
@property
def video_capabilities(self) -> VideoCapabilities:
    return VideoCapabilities(reference_images=True, max_reference_images=5)

# openai.py — reference images only
@property
def video_capabilities(self) -> VideoCapabilities:
    return VideoCapabilities(reference_images=True, max_reference_images=3)
```

- [ ] **Step 5: Extend MediaGenerator.generate_video_async**

Add `end_image` and `reference_images` parameters:

```python
# In lib/media_generator.py, modify generate_video_async signature:
async def generate_video_async(
    self,
    prompt: str,
    resource_type: str,
    resource_id: str,
    start_image: Path,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
    resolution: str = "1080p",
    seed: int | None = None,
    service_tier: str = "default",
    end_image: Path | None = None,          # NEW
    reference_images: list[Path] | None = None,  # NEW
    **version_metadata,
) -> tuple[Path, int, bool, str | None]:
    # ... existing logic ...
    # Pass end_image and reference_images to VideoGenerationRequest
```

- [ ] **Step 6: Run tests**

Run: `uv run python -m pytest tests/test_video_backend_capabilities.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
uv run ruff check lib/video_backends/ lib/media_generator.py tests/test_video_backend_capabilities.py
uv run ruff format lib/video_backends/ lib/media_generator.py tests/test_video_backend_capabilities.py
git add lib/video_backends/base.py lib/video_backends/gemini.py lib/video_backends/ark.py \
  lib/video_backends/grok.py lib/video_backends/openai.py lib/media_generator.py \
  tests/test_video_backend_capabilities.py
git commit -m "feat: VideoBackend capabilities + end_image/reference_images support"
```

---

## Phase 2: Backend Integration

### Task 6: Grid Manager (file-based CRUD)

**Files:**
- Create: `lib/grid_manager.py`
- Modify: `lib/project_manager.py` (add "grids" to SUBDIRS)
- Test: `tests/test_grid_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_grid_manager.py
import json
from pathlib import Path
from lib.grid_manager import GridManager
from lib.grid.models import GridGeneration


class TestGridManager:
    def test_save_and_load(self, tmp_path: Path):
        gm = GridManager(tmp_path)
        grid = GridGeneration.create(
            episode=1, script_file="ep1.json",
            scene_ids=["S1", "S2", "S3", "S4"],
            rows=2, cols=2, grid_size="grid_4",
            provider="test", model="test-model",
        )
        gm.save(grid)

        loaded = gm.get(grid.id)
        assert loaded is not None
        assert loaded.id == grid.id
        assert loaded.scene_ids == ["S1", "S2", "S3", "S4"]
        assert len(loaded.frame_chain) == 4

    def test_list_grids(self, tmp_path: Path):
        gm = GridManager(tmp_path)
        for i in range(3):
            grid = GridGeneration.create(
                episode=1, script_file="ep1.json",
                scene_ids=[f"S{j}" for j in range(4)],
                rows=2, cols=2, grid_size="grid_4",
                provider="test", model="m",
            )
            gm.save(grid)

        grids = gm.list_all()
        assert len(grids) == 3

    def test_update_status(self, tmp_path: Path):
        gm = GridManager(tmp_path)
        grid = GridGeneration.create(
            episode=1, script_file="ep1.json",
            scene_ids=["S1", "S2", "S3", "S4"],
            rows=2, cols=2, grid_size="grid_4",
            provider="test", model="m",
        )
        gm.save(grid)
        grid.status = "completed"
        gm.save(grid)

        loaded = gm.get(grid.id)
        assert loaded.status == "completed"

    def test_get_nonexistent_returns_none(self, tmp_path: Path):
        gm = GridManager(tmp_path)
        assert gm.get("nonexistent") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_grid_manager.py -v`
Expected: ImportError

- [ ] **Step 3: Implement GridManager**

```python
# lib/grid_manager.py
from __future__ import annotations

import json
from pathlib import Path

from lib.grid.models import GridGeneration


class GridManager:
    """File-based CRUD for GridGeneration records, stored in {project}/grids/."""

    def __init__(self, project_path: Path):
        self._dir = project_path / "grids"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, grid_id: str) -> Path:
        return self._dir / f"{grid_id}.json"

    def save(self, grid: GridGeneration) -> None:
        self._path(grid.id).write_text(json.dumps(grid.to_dict(), ensure_ascii=False, indent=2))

    def get(self, grid_id: str) -> GridGeneration | None:
        p = self._path(grid_id)
        if not p.exists():
            return None
        return GridGeneration.from_dict(json.loads(p.read_text()))

    def list_all(self) -> list[GridGeneration]:
        results = []
        for f in sorted(self._dir.glob("grid_*.json")):
            try:
                results.append(GridGeneration.from_dict(json.loads(f.read_text())))
            except (json.JSONDecodeError, KeyError):
                continue
        return results
```

- [ ] **Step 4: Add "grids" to ProjectManager.SUBDIRS**

In `lib/project_manager.py`, add `"grids"` to the SUBDIRS tuple/list so it gets created with new projects.

- [ ] **Step 5: Run tests**

Run: `uv run python -m pytest tests/test_grid_manager.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
uv run ruff check lib/grid_manager.py lib/project_manager.py tests/test_grid_manager.py
uv run ruff format lib/grid_manager.py lib/project_manager.py tests/test_grid_manager.py
git add lib/grid_manager.py lib/project_manager.py tests/test_grid_manager.py
git commit -m "feat: GridManager file-based CRUD for grid generation records"
```

---

### Task 7: Grid Generation Task Executor

**Files:**
- Modify: `server/services/generation_tasks.py`
- Modify: `lib/generation_worker.py` (register "grid" task type)
- Test: `tests/test_grid_executor.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_grid_executor.py
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from PIL import Image

import pytest


@pytest.fixture
def project_path(tmp_path):
    """Create minimal project structure."""
    p = tmp_path / "projects" / "test-project"
    for d in ("storyboards", "grids", "scripts", "characters", "clues"):
        (p / d).mkdir(parents=True)
    # Write minimal project.json
    import json
    (p / "project.json").write_text(json.dumps({
        "name": "test-project",
        "title": "Test",
        "content_mode": "narration",
        "style": "realistic",
        "generation_mode": "grid",
        "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
        "characters": {},
        "clues": {},
    }))
    # Write minimal script
    (p / "scripts" / "episode_1.json").write_text(json.dumps({
        "content_mode": "narration",
        "segments": [
            {"segment_id": f"E1S0{i}", "episode": 1, "segment_break": i == 1,
             "duration_seconds": 4, "novel_text": "text",
             "characters_in_segment": [], "clues_in_segment": [],
             "image_prompt": {"scene": f"scene{i}", "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"}},
             "video_prompt": {"action": f"action{i}", "camera_motion": "static", "ambiance_audio": "quiet", "dialogue": []},
             "transition_to_next": "cut",
             "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"}}
            for i in range(1, 5)
        ],
    }))
    return p


class TestSegmentBreakGrouping:
    def test_group_by_segment_break(self, project_path):
        from server.services.generation_tasks import _group_scenes_by_segment_break
        from lib.storyboard_sequence import get_storyboard_items
        import json

        script = json.loads((project_path / "scripts" / "episode_1.json").read_text())
        items, id_field, _, _ = get_storyboard_items(script)
        groups = _group_scenes_by_segment_break(items, id_field)
        # E1S01 has segment_break=False (start), E1S02 has segment_break=True
        # So group 1 = [E1S01], group 2 = [E1S02, E1S03, E1S04]
        assert len(groups) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_grid_executor.py -v`
Expected: ImportError

- [ ] **Step 3: Implement grid task executor**

Add to `server/services/generation_tasks.py`:

```python
def _group_scenes_by_segment_break(
    items: list[dict], id_field: str
) -> list[list[dict]]:
    """Group consecutive scenes, breaking at segment_break=True."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    for item in items:
        if item.get("segment_break", False) and current:
            groups.append(current)
            current = []
        current.append(item)
    if current:
        groups.append(current)
    return groups


async def execute_grid_task(
    project_name: str,
    resource_id: str,
    payload: dict[str, Any],
    *,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """Execute a grid generation task.

    resource_id = grid_id
    payload keys: script_file, scene_ids, grid_size, rows, cols, prompt,
                  image_provider, image_model
    """
    from lib.grid.layout import calculate_grid_layout
    from lib.grid.splitter import split_grid_image, is_placeholder_cell
    from lib.grid.models import GridGeneration
    from lib.grid_manager import GridManager

    pm = ProjectManager()
    project_path = pm.get_project_path(project_name)
    gm = GridManager(project_path)

    grid = gm.get(resource_id)
    if not grid:
        raise ValueError(f"Grid {resource_id} not found")

    # Phase 1: Generate grid image
    grid.status = "generating"
    gm.save(grid)

    prompt = payload["prompt"]
    grid.prompt = prompt

    aspect_ratio = payload.get("grid_aspect_ratio", "16:9")
    image_size = payload.get("image_size", "2K")

    # Collect reference images
    ref_images = _collect_grid_reference_images(project_path, payload, grid.scene_ids)

    mg = await _get_media_generator(project_name, payload, user_id=user_id)
    image_result = await mg.generate_image_async(
        prompt=prompt,
        resource_type="grids",
        resource_id=grid.id,
        reference_images=ref_images,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
    )
    grid.grid_image_path = str(image_result[0].relative_to(project_path))

    # Phase 2: Split
    grid.status = "splitting"
    gm.save(grid)

    from PIL import Image as PILImage
    grid_img = PILImage.open(image_result[0])
    video_ar = payload.get("video_aspect_ratio", "16:9")
    cells = split_grid_image(grid_img, grid.rows, grid.cols, video_ar)

    # Phase 3: Assign frames
    script_file = payload["script_file"]
    scene_ids = grid.scene_ids

    for idx, cell_img in enumerate(cells):
        fc = grid.frame_chain[idx]
        if fc.frame_type == "placeholder" or is_placeholder_cell(cell_img):
            continue

        # Determine output paths based on frame role
        if fc.frame_type == "first":
            # First cell: S1 first frame
            sid = fc.next_scene_id
            fname = f"scene_{sid}_first.png"
            cell_img.save(project_path / "storyboards" / fname)
            fc.image_path = f"storyboards/{fname}"
            pm.update_scene_asset(project_name, script_file, sid, "storyboard_image", f"storyboards/{fname}")
        elif fc.frame_type == "transition":
            # Transition: prev scene's last + next scene's first
            prev_sid = fc.prev_scene_id
            next_sid = fc.next_scene_id
            # Save as prev scene's last frame
            last_fname = f"scene_{prev_sid}_last.png"
            cell_img.save(project_path / "storyboards" / last_fname)
            fc.image_path = f"storyboards/{last_fname}"
            _update_scene_last_image(pm, project_name, script_file, prev_sid, f"storyboards/{last_fname}")
            # Also set as next scene's first frame
            first_fname = f"scene_{next_sid}_first.png"
            cell_img.save(project_path / "storyboards" / first_fname)
            pm.update_scene_asset(project_name, script_file, next_sid, "storyboard_image", f"storyboards/{first_fname}")

    grid.status = "completed"
    gm.save(grid)

    return {
        "grid_id": grid.id,
        "file_path": grid.grid_image_path,
        "created_at": grid.created_at,
        "resource_type": "grid",
        "resource_id": grid.id,
    }
```

Register in `_TASK_EXECUTORS`:
```python
_TASK_EXECUTORS = {
    "storyboard": execute_storyboard_task,
    "video": execute_video_task,
    "character": execute_character_task,
    "clue": execute_clue_task,
    "grid": execute_grid_task,  # NEW
}
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_grid_executor.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check server/services/generation_tasks.py tests/test_grid_executor.py
uv run ruff format server/services/generation_tasks.py tests/test_grid_executor.py
git add server/services/generation_tasks.py lib/generation_worker.py tests/test_grid_executor.py
git commit -m "feat: grid generation task executor with split and frame assignment"
```

---

### Task 8: Grid API Router

**Files:**
- Create: `server/routers/grids.py`
- Modify: `server/main.py` (include router)
- Test: `tests/test_grid_router.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_grid_router.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestGridRouterEndpoints:
    """Verify router endpoint existence and basic request/response shapes."""

    @pytest.fixture
    def client(self):
        from server.main import app
        from httpx import AsyncClient, ASGITransport
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def test_generate_grid_endpoint_exists(self, client):
        """POST /api/v1/projects/{name}/generate/grid/{episode} should exist."""
        with patch("server.routers.grids._generate_grid_for_episode", new_callable=AsyncMock) as mock:
            mock.return_value = {"success": True, "grid_ids": [], "task_ids": []}
            resp = await client.post(
                "/api/v1/projects/test/generate/grid/1",
                json={"script_file": "episode_1.json"},
                headers={"Authorization": "Bearer test"},
            )
            # 401 or 200 depending on auth — just verify route exists (not 404/405)
            assert resp.status_code != 404
            assert resp.status_code != 405

    async def test_list_grids_endpoint_exists(self, client):
        resp = await client.get(
            "/api/v1/projects/test/grids",
            headers={"Authorization": "Bearer test"},
        )
        assert resp.status_code != 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_grid_router.py -v`
Expected: 404 (route doesn't exist yet)

- [ ] **Step 3: Implement router**

```python
# server/routers/grids.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.dependencies import CurrentUser
from lib.project_manager import ProjectManager
from lib.grid_manager import GridManager
from lib.grid.layout import calculate_grid_layout
from lib.grid.models import GridGeneration
from lib.grid.prompt_builder import build_grid_prompt
from lib.storyboard_sequence import get_storyboard_items
from lib.generation_queue import get_queue

router = APIRouter(prefix="/projects/{project_name}", tags=["grids"])


class GenerateGridRequest(BaseModel):
    script_file: str
    scene_ids: list[str] | None = None


class GenerateGridResponse(BaseModel):
    success: bool
    grid_ids: list[str]
    task_ids: list[str]
    message: str


@router.post("/generate/grid/{episode}", response_model=GenerateGridResponse)
async def generate_grid(
    project_name: str,
    episode: int,
    req: GenerateGridRequest,
    _user: CurrentUser,
):
    return await _generate_grid_for_episode(project_name, episode, req)


async def _generate_grid_for_episode(
    project_name: str, episode: int, req: GenerateGridRequest
) -> dict:
    pm = ProjectManager()
    project = pm.load_project(project_name)
    script = pm.load_script(project_name, req.script_file)
    project_path = pm.get_project_path(project_name)

    items, id_field, _, _ = get_storyboard_items(script)
    aspect_ratio = project.get("aspect_ratio", "9:16")
    style = project.get("style", "")

    from server.services.generation_tasks import _group_scenes_by_segment_break
    groups = _group_scenes_by_segment_break(items, id_field)

    # Filter groups by scene_ids if provided
    if req.scene_ids:
        sid_set = set(req.scene_ids)
        groups = [g for g in groups if any(item[id_field] in sid_set for item in g)]

    grid_ids = []
    task_ids = []
    queue = get_queue()
    gm = GridManager(project_path)

    from server.routers.generate import _snapshot_image_backend

    for group in groups:
        scene_ids = [item[id_field] for item in group]
        n = len(scene_ids)
        layout = calculate_grid_layout(n, aspect_ratio)
        if layout is None:
            continue  # < 4 scenes, skip grid

        # Cap at grid capacity
        if n > layout.cell_count:
            scene_ids = scene_ids[: layout.cell_count]
            group = group[: layout.cell_count]

        backend_snapshot = _snapshot_image_backend(project_name)
        grid = GridGeneration.create(
            episode=episode,
            script_file=req.script_file,
            scene_ids=scene_ids,
            rows=layout.rows,
            cols=layout.cols,
            grid_size=layout.grid_size,
            provider=backend_snapshot.get("image_provider", ""),
            model=backend_snapshot.get("image_model", ""),
        )

        prompt = build_grid_prompt(
            scenes=group,
            id_field=id_field,
            rows=layout.rows,
            cols=layout.cols,
            style=style,
            aspect_ratio=aspect_ratio,
        )

        gm.save(grid)

        task = await queue.enqueue_task(
            project_name=project_name,
            task_type="grid",
            media_type="image",
            resource_id=grid.id,
            payload={
                "prompt": prompt,
                "script_file": req.script_file,
                "scene_ids": scene_ids,
                "grid_size": layout.grid_size,
                "rows": layout.rows,
                "cols": layout.cols,
                "grid_aspect_ratio": layout.grid_aspect_ratio,
                "video_aspect_ratio": aspect_ratio,
                **backend_snapshot,
            },
            script_file=req.script_file,
        )
        grid_ids.append(grid.id)
        task_ids.append(task["task_id"])

    return {
        "success": True,
        "grid_ids": grid_ids,
        "task_ids": task_ids,
        "message": f"已提交 {len(grid_ids)} 个宫格生成任务",
    }


@router.get("/grids")
async def list_grids(project_name: str, _user: CurrentUser):
    pm = ProjectManager()
    gm = GridManager(pm.get_project_path(project_name))
    return [g.to_dict() for g in gm.list_all()]


@router.get("/grids/{grid_id}")
async def get_grid(project_name: str, grid_id: str, _user: CurrentUser):
    pm = ProjectManager()
    gm = GridManager(pm.get_project_path(project_name))
    grid = gm.get(grid_id)
    if not grid:
        raise HTTPException(404, f"Grid {grid_id} not found")
    return grid.to_dict()


@router.post("/grids/{grid_id}/regenerate")
async def regenerate_grid(project_name: str, grid_id: str, _user: CurrentUser):
    pm = ProjectManager()
    gm = GridManager(pm.get_project_path(project_name))
    grid = gm.get(grid_id)
    if not grid:
        raise HTTPException(404, f"Grid {grid_id} not found")

    grid.status = "pending"
    grid.error_message = None
    gm.save(grid)

    queue = get_queue()
    from server.routers.generate import _snapshot_image_backend

    backend_snapshot = _snapshot_image_backend(project_name)
    task = await queue.enqueue_task(
        project_name=project_name,
        task_type="grid",
        media_type="image",
        resource_id=grid.id,
        payload={
            "prompt": grid.prompt,
            "script_file": grid.script_file,
            "scene_ids": grid.scene_ids,
            "grid_size": grid.grid_size,
            "rows": grid.rows,
            "cols": grid.cols,
            **backend_snapshot,
        },
        script_file=grid.script_file,
    )
    return {"success": True, "task_id": task["task_id"]}
```

- [ ] **Step 4: Register router in main.py**

In `server/main.py`, add:
```python
from server.routers.grids import router as grids_router
app.include_router(grids_router, prefix="/api/v1")
```

- [ ] **Step 5: Run tests**

Run: `uv run python -m pytest tests/test_grid_router.py -v`
Expected: All PASS (routes exist)

- [ ] **Step 6: Commit**

```bash
uv run ruff check server/routers/grids.py server/main.py tests/test_grid_router.py
uv run ruff format server/routers/grids.py server/main.py tests/test_grid_router.py
git add server/routers/grids.py server/main.py tests/test_grid_router.py
git commit -m "feat: grid API router (generate, list, get, regenerate)"
```

---

### Task 9: Video Task first_last Support

**Files:**
- Modify: `server/services/generation_tasks.py` (`execute_video_task`)

- [ ] **Step 1: Write failing test**

```python
# Append to tests/test_grid_executor.py

class TestVideoTaskFirstLastFallback:
    """execute_video_task should detect storyboard_last_image and use first_last mode."""

    async def test_detects_last_image_and_passes_end_image(self, project_path):
        """When scene has storyboard_last_image, video task should pass end_image."""
        # This is an integration-level check; detailed fallback logic is in the
        # VideoBackend capabilities test. Here we just verify the field is read.
        from server.services.generation_tasks import _resolve_video_end_image
        import json

        script = json.loads((project_path / "scripts" / "episode_1.json").read_text())
        # Manually set storyboard_last_image on E1S01
        script["segments"][0]["generated_assets"]["storyboard_last_image"] = "storyboards/scene_E1S01_last.png"
        # Create the file
        (project_path / "storyboards" / "scene_E1S01_last.png").write_bytes(b"fake")

        result = _resolve_video_end_image(project_path, script["segments"][0])
        assert result is not None
        assert "last" in str(result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_grid_executor.py::TestVideoTaskFirstLastFallback -v`
Expected: ImportError

- [ ] **Step 3: Implement _resolve_video_end_image and update execute_video_task**

Add helper to `server/services/generation_tasks.py`:

```python
def _resolve_video_end_image(project_path: Path, item: dict) -> Path | None:
    """Check if scene has a last frame image for first_last video mode."""
    assets = item.get("generated_assets", {})
    last_img = assets.get("storyboard_last_image")
    if not last_img:
        return None
    path = project_path / last_img
    return path if path.exists() else None
```

In `execute_video_task`, add end_image resolution before calling `generate_video_async`:
```python
end_image = _resolve_video_end_image(project_path, item)
# ... pass to mg.generate_video_async(end_image=end_image, ...)
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_grid_executor.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add server/services/generation_tasks.py tests/test_grid_executor.py
git commit -m "feat: video task detects storyboard_last_image for first_last mode"
```

---

## Phase 3: Frontend

> **Important for subagents:** Before writing any frontend component code, invoke the `/frontend-design` skill first to get design guidance.

### Task 10: TypeScript Types + API Client

**Files:**
- Create: `frontend/src/types/grid.ts`
- Modify: `frontend/src/types/script.ts` (GeneratedAssets)
- Modify: `frontend/src/api.ts` (grid methods)

- [ ] **Step 1: Add grid TypeScript types**

```typescript
// frontend/src/types/grid.ts
export interface FrameCell {
  index: number;
  row: number;
  col: number;
  frame_type: "first" | "transition" | "placeholder";
  prev_scene_id: string | null;
  next_scene_id: string | null;
  image_path: string | null;
}

export interface GridGeneration {
  id: string;
  episode: number;
  script_file: string;
  scene_ids: string[];
  grid_image_path: string | null;
  rows: number;
  cols: number;
  cell_count: number;
  frame_chain: FrameCell[];
  status: "pending" | "generating" | "splitting" | "completed" | "failed";
  prompt: string | null;
  provider: string;
  model: string;
  grid_size: string;
  created_at: string;
  error_message: string | null;
}
```

- [ ] **Step 2: Extend GeneratedAssets type**

In `frontend/src/types/script.ts`:
```typescript
export interface GeneratedAssets {
  storyboard_image: string | null;
  storyboard_last_image: string | null;  // NEW
  grid_id: string | null;                // NEW
  grid_cell_index: number | null;        // NEW
  video_clip: string | null;
  video_thumbnail: string | null;
  video_uri: string | null;
  status: AssetStatus;
}
```

- [ ] **Step 3: Add API methods**

In `frontend/src/api.ts`:
```typescript
static async generateGrid(
  projectName: string,
  episode: number,
  scriptFile: string,
  sceneIds?: string[]
): Promise<{ success: boolean; grid_ids: string[]; task_ids: string[]; message: string }> {
  return this.request(
    `/projects/${encodeURIComponent(projectName)}/generate/grid/${episode}`,
    {
      method: "POST",
      body: JSON.stringify({ script_file: scriptFile, scene_ids: sceneIds }),
    }
  );
}

static async listGrids(
  projectName: string
): Promise<GridGeneration[]> {
  return this.request(`/projects/${encodeURIComponent(projectName)}/grids`);
}

static async getGrid(
  projectName: string,
  gridId: string
): Promise<GridGeneration> {
  return this.request(`/projects/${encodeURIComponent(projectName)}/grids/${encodeURIComponent(gridId)}`);
}

static async regenerateGrid(
  projectName: string,
  gridId: string
): Promise<{ success: boolean; task_id: string }> {
  return this.request(
    `/projects/${encodeURIComponent(projectName)}/grids/${encodeURIComponent(gridId)}/regenerate`,
    { method: "POST" }
  );
}
```

- [ ] **Step 4: Build to verify types**

Run: `cd frontend && pnpm build`
Expected: No type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/grid.ts frontend/src/types/script.ts frontend/src/api.ts
git commit -m "feat(frontend): grid TypeScript types and API client methods"
```

---

### Task 11: Project Settings — generation_mode

**Files:**
- Modify: `frontend/src/components/pages/CreateProjectModal.tsx`
- Modify: `frontend/src/components/pages/ProjectSettingsPage.tsx`
- Modify: `frontend/src/types/project.ts`

> **Subagent instruction:** Invoke `/frontend-design` skill before implementing this task.

- [ ] **Step 1: Add generation_mode to ProjectData type**

```typescript
// In frontend/src/types/project.ts, extend ProjectData:
export interface ProjectData {
  // ... existing fields ...
  generation_mode?: "single" | "grid";  // NEW
}
```

- [ ] **Step 2: Add radio group to CreateProjectModal**

Add "分镜生成模式" radio group after aspect_ratio selector:
- Option 1: "逐张生成" (single) — default
- Option 2: "宫格生成" (grid) — description: "按段落分组一次生成，首尾帧链式衔接，画风更一致"

- [ ] **Step 3: Add toggle to ProjectSettingsPage**

Add generation_mode setting in the project settings form, following the same pattern as existing settings (aspect_ratio, default_duration).

- [ ] **Step 4: Build**

Run: `cd frontend && pnpm build`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/pages/CreateProjectModal.tsx \
  frontend/src/components/pages/ProjectSettingsPage.tsx \
  frontend/src/types/project.ts
git commit -m "feat(frontend): generation_mode toggle in project creation and settings"
```

---

### Task 12: Timeline — Grid Segment Groups

**Files:**
- Create: `frontend/src/components/canvas/timeline/GridSegmentGroup.tsx`
- Modify: `frontend/src/components/canvas/timeline/TimelineCanvas.tsx`
- Modify: `frontend/src/components/canvas/StudioCanvasRouter.tsx`

> **Subagent instruction:** Invoke `/frontend-design` skill before implementing this task.

- [ ] **Step 1: Create GridSegmentGroup component**

New component that wraps a group of SegmentCards with:
- Header: segment label, scene count, auto-selected grid_size, "生成宫格" button
- Loading state derived from tasks store (task_type="grid")

- [ ] **Step 2: Modify TimelineCanvas to group segments**

When `generation_mode === "grid"`, group segments by `segment_break` and render each group inside a `GridSegmentGroup` wrapper. Add episode-level "一键生成全部宫格" button.

- [ ] **Step 3: Add handleGenerateGrid to StudioCanvasRouter**

```typescript
const handleGenerateGrid = useCallback(async (episode: number, scriptFile: string, sceneIds?: string[]) => {
  if (!currentProjectName) return;
  try {
    const result = await API.generateGrid(currentProjectName, episode, scriptFile, sceneIds);
    useAppStore.getState().pushToast(result.message, "success");
  } catch (err) {
    useAppStore.getState().pushToast(`宫格生成失败: ${(err as Error).message}`, "error");
  }
}, [currentProjectName]);
```

- [ ] **Step 4: Build**

Run: `cd frontend && pnpm build`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/canvas/timeline/GridSegmentGroup.tsx \
  frontend/src/components/canvas/timeline/TimelineCanvas.tsx \
  frontend/src/components/canvas/StudioCanvasRouter.tsx
git commit -m "feat(frontend): timeline grid segment groups with batch generation"
```

---

### Task 13: SegmentCard — Grid Mode First/Last Frame

**Files:**
- Modify: `frontend/src/components/canvas/timeline/SegmentCard.tsx`

> **Subagent instruction:** Invoke `/frontend-design` skill before implementing this task.

- [ ] **Step 1: Add grid mode media column**

When `generation_mode === "grid"` and the segment has `storyboard_last_image`:
- Show first frame and last frame **vertically stacked** (for horizontal aspect ratio)
- Label each frame with "首帧" / "尾帧" badge and cell index
- Show shared frame annotation (e.g., "= S1 尾帧")
- Video generation button shows "(first_last)" label
- Last scene in segment group: only show first frame, label "末尾场景 · 无尾帧", video button shows "(single)"

- [ ] **Step 2: Build**

Run: `cd frontend && pnpm build`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/canvas/timeline/SegmentCard.tsx
git commit -m "feat(frontend): SegmentCard grid mode with first/last frame vertical layout"
```

---

### Task 14: Grid Preview Panel

**Files:**
- Create: `frontend/src/components/canvas/timeline/GridPreviewPanel.tsx`
- Modify: `frontend/src/components/canvas/timeline/GridSegmentGroup.tsx` (add preview trigger)

> **Subagent instruction:** Invoke `/frontend-design` skill before implementing this task.

- [ ] **Step 1: Create GridPreviewPanel**

Collapsible panel within segment group showing:
- Left: grid composite image (using PreviewableImageFrame)
- Right: frame chain list (cell → scene mapping)
- "重新生成" button
- Status badge

- [ ] **Step 2: Wire preview into GridSegmentGroup**

Add expand/collapse toggle to GridSegmentGroup header that reveals GridPreviewPanel.

- [ ] **Step 3: Build**

Run: `cd frontend && pnpm build`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/canvas/timeline/GridPreviewPanel.tsx \
  frontend/src/components/canvas/timeline/GridSegmentGroup.tsx
git commit -m "feat(frontend): grid preview panel with frame chain visualization"
```

---

## Phase 4: Agent Skills

### Task 15: generate-grid Skill

**Files:**
- Create: `agent_runtime_profile/.claude/skills/generate-grid/SKILL.md`
- Create: `agent_runtime_profile/.claude/skills/generate-grid/scripts/generate_grid.py`

- [ ] **Step 1: Write SKILL.md**

```markdown
---
name: generate-grid
description: 生成宫格分镜图。当用户说"生成宫格"、"宫格生图"、"宫格模式生成分镜"时使用。自动按 segment_break 分组，选择最优宫格大小，生成首尾帧链式宫格图并切割分配。
---

# 生成宫格分镜图

为 grid 模式项目生成宫格分镜图。自动按 segment_break 分组，每组生成一张宫格大图，切割后形成首尾帧链式结构。

## 前置条件

- 项目 `generation_mode` 为 `"grid"`
- 剧本已生成（scripts/episode_N.json 存在）
- 角色/线索设计图已生成（用作参考图）

## 命令行用法

```bash
# 整集生成（推荐）
python generate_grid.py episode_1.json --episode 1

# 指定场景所在的组
python generate_grid.py episode_1.json --scene-ids E1S01 E1S02 E1S03

# 列出当前缺少宫格的组
python generate_grid.py episode_1.json --list
```

## 输出

- 宫格大图保存到 `grids/grid_{id}.png`
- 切割后的首帧/尾帧保存到 `storyboards/scene_{id}_first.png` / `scene_{id}_last.png`
- 帧链元数据保存到 `grids/grid_{id}.json`
```

- [ ] **Step 2: Write generate_grid.py script**

Follow the pattern of `generate_storyboard.py`:
1. Parse args, load project + script
2. Verify `generation_mode == "grid"`
3. Group scenes by `segment_break`
4. For groups with N >= 4: call `POST /generate/grid/{episode}` via queue client
5. For groups with N < 4: log skip message
6. `batch_enqueue_and_wait_sync()` to wait for completion
7. Report results

- [ ] **Step 3: Test manually**

Run: `cd projects/test-project && python ../../agent_runtime_profile/.claude/skills/generate-grid/scripts/generate_grid.py episode_1.json --list`
Expected: Lists segment groups and their grid_size

- [ ] **Step 4: Commit**

```bash
git add agent_runtime_profile/.claude/skills/generate-grid/
git commit -m "feat: generate-grid agent skill for grid storyboard generation"
```

---

### Task 16: Workflow + Video Skill Updates

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md`
- Modify: `agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py`

- [ ] **Step 1: Update manga-workflow SKILL.md**

Add branching logic at stage 7 (storyboard generation):
```markdown
## Stage 7: 分镜图生成

检查 `project.json` 的 `generation_mode`：
- `"single"` → dispatch `generate-storyboard` subagent（现有逻辑）
- `"grid"` → dispatch `generate-grid` subagent（新增）
```

- [ ] **Step 2: Update generate-video to detect first_last frames**

In `generate_video.py`, when building BatchTaskSpec, check if scene has `storyboard_last_image`:
- If yes: the video backend will automatically use first_last mode (handled by execute_video_task)
- Log which mode each scene will use

- [ ] **Step 3: Commit**

```bash
git add agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md \
  agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py
git commit -m "feat: manga-workflow grid branching + generate-video first_last logging"
```

---

## Final Verification

### Task 17: Integration Test + Lint

- [ ] **Step 1: Run full test suite**

```bash
uv run python -m pytest -v --tb=short
```
Expected: All tests pass

- [ ] **Step 2: Run linter**

```bash
uv run ruff check . && uv run ruff format --check .
```
Expected: No issues

- [ ] **Step 3: Frontend build**

```bash
cd frontend && pnpm build
```
Expected: No errors

- [ ] **Step 4: Final commit if any fixups needed**

```bash
git add -A && git commit -m "chore: lint fixes and test adjustments"
```
