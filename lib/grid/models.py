"""Grid data models for grid-image-to-video feature."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass
class ReferenceImage:
    """Metadata for a reference image used during grid generation."""

    path: str  # Relative path from project root (e.g. "characters/hero/sheet.png")
    name: str  # Display name (character or clue name)
    ref_type: str  # "character" or "clue"

    def to_dict(self) -> dict:
        return {"path": self.path, "name": self.name, "ref_type": self.ref_type}

    @classmethod
    def from_dict(cls, data: dict) -> ReferenceImage:
        return cls(
            path=data["path"],
            name=data["name"],
            ref_type=data.get("ref_type", "character"),
        )


@dataclass
class FrameCell:
    """Represents a single cell in a grid frame chain."""

    index: int
    row: int
    col: int
    frame_type: Literal["first", "transition", "placeholder"]
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
    def from_dict(cls, data: dict) -> FrameCell:
        return cls(
            index=data["index"],
            row=data["row"],
            col=data["col"],
            frame_type=data["frame_type"],
            prev_scene_id=data.get("prev_scene_id"),
            next_scene_id=data.get("next_scene_id"),
            image_path=data.get("image_path"),
        )


def build_frame_chain(scene_ids: list[str], rows: int, cols: int) -> list[FrameCell]:
    """Build a frame chain from scene IDs to fill a rows×cols grid.

    - Cell 0: frame_type="first", next_scene_id=scene_ids[0]
    - Cell 1..N-1: frame_type="transition", prev/next scene IDs
    - Remaining cells: frame_type="placeholder"
    """
    total = rows * cols
    chain: list[FrameCell] = []

    for idx in range(total):
        row = idx // cols
        col = idx % cols

        if idx == 0:
            chain.append(
                FrameCell(
                    index=idx,
                    row=row,
                    col=col,
                    frame_type="first",
                    prev_scene_id=None,
                    next_scene_id=scene_ids[0] if scene_ids else None,
                )
            )
        elif idx < len(scene_ids):
            chain.append(
                FrameCell(
                    index=idx,
                    row=row,
                    col=col,
                    frame_type="transition",
                    prev_scene_id=scene_ids[idx - 1],
                    next_scene_id=scene_ids[idx],
                )
            )
        else:
            chain.append(
                FrameCell(
                    index=idx,
                    row=row,
                    col=col,
                    frame_type="placeholder",
                )
            )

    return chain


@dataclass
class GridGeneration:
    """Represents a grid image generation job."""

    id: str
    episode: int
    script_file: str
    scene_ids: list[str]
    grid_image_path: str | None
    rows: int
    cols: int
    cell_count: int
    frame_chain: list[FrameCell]
    status: Literal["pending", "generating", "splitting", "completed", "failed"]
    prompt: str | None
    provider: str
    model: str
    grid_size: str
    created_at: str
    error_message: str | None = None
    reference_images: list[ReferenceImage] | None = None

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
            "reference_images": [r.to_dict() for r in self.reference_images] if self.reference_images else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GridGeneration:
        return cls(
            id=data["id"],
            episode=data["episode"],
            script_file=data["script_file"],
            scene_ids=data["scene_ids"],
            grid_image_path=data.get("grid_image_path"),
            rows=data["rows"],
            cols=data["cols"],
            cell_count=data["cell_count"],
            frame_chain=[FrameCell.from_dict(c) for c in data.get("frame_chain", [])],
            status=data["status"],
            prompt=data.get("prompt"),
            provider=data["provider"],
            model=data["model"],
            grid_size=data["grid_size"],
            created_at=data["created_at"],
            error_message=data.get("error_message"),
            reference_images=[ReferenceImage.from_dict(r) for r in data["reference_images"]]
            if data.get("reference_images")
            else None,
        )

    @classmethod
    def create(
        cls,
        episode: int,
        script_file: str,
        scene_ids: list[str],
        rows: int,
        cols: int,
        grid_size: str,
        provider: str,
        model: str,
        prompt: str | None = None,
    ) -> GridGeneration:
        """Create a new GridGeneration with a generated id and pending status."""
        grid_id = f"grid_{uuid.uuid4().hex[:12]}"
        frame_chain = build_frame_chain(scene_ids, rows, cols)
        return cls(
            id=grid_id,
            episode=episode,
            script_file=script_file,
            scene_ids=scene_ids,
            grid_image_path=None,
            rows=rows,
            cols=cols,
            cell_count=rows * cols,
            frame_chain=frame_chain,
            status="pending",
            prompt=prompt,
            provider=provider,
            model=model,
            grid_size=grid_size,
            created_at=datetime.now().isoformat(),
            error_message=None,
        )
