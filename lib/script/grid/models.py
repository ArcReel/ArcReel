"""Grid data models for grid-image-to-video feature."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal


@dataclass
class ReferenceImage:
    """Metadata for a reference image used during grid generation."""

    path: str  # Relative path from project root (e.g. "characters/hero/sheet.png")
    name: str  # Display name
    ref_type: str  # "character" | "scene" | "prop"

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
    # Composite lifecycle: completed only means the composite is ready; split_at records whether cells were saved.
    status: Literal["pending", "generating", "completed", "failed"]
    prompt: str | None
    provider: str
    model: str
    grid_size: str
    created_at: str
    error_message: str | None = None
    reference_images: list[ReferenceImage] | None = None
    # Last time cells were split from this composite; cleared when it changes (regeneration/upload/version restore).
    split_at: str | None = None
    # Target cell aspect ratio when this composite was produced. Project aspect_ratio can change
    # at any time, and splitting is decoupled from generation and may occur much later. Reading the
    # later settings would center-crop old composites to the new ratio (portrait splitting of a
    # landscape image loses most of its width). Freeze the ratio with the composite. Legacy records
    # use None and fall back to project settings when split.
    video_aspect_ratio: str | None = None

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
            "split_at": self.split_at,
            "video_aspect_ratio": self.video_aspect_ratio,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GridGeneration:
        # Generation and splitting once shared a task, so intermediate records can retain
        # status="splitting". Their composite is on disk, equivalent to ready but not yet split.
        raw_status = data["status"]
        status = "completed" if raw_status == "splitting" else raw_status
        # Old records lack split_at. The old flow wrote status="completed" only after saving
        # split cells, so these records count as split, using created_at as their split time.
        # Treating all of them as unsplit would prompt users to split again, overwriting later
        # individually regenerated storyboards with the old composite. New records with explicit
        # null, and status="splitting" (composite ready but no cells saved), remain unsplit.
        split_at = data["created_at"] if "split_at" not in data and raw_status == "completed" else data.get("split_at")
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
            status=status,
            prompt=data.get("prompt"),
            provider=data["provider"],
            model=data["model"],
            grid_size=data["grid_size"],
            created_at=data["created_at"],
            error_message=data.get("error_message"),
            reference_images=[ReferenceImage.from_dict(r) for r in data["reference_images"]]
            if data.get("reference_images")
            else None,
            split_at=split_at,
            video_aspect_ratio=data.get("video_aspect_ratio"),
        )

    def mark_composite_replaced(self) -> None:
        """Reset the record after a user action replaces the composite image (manual upload / version restore).

        - Clear ``split_at`` unconditionally: the old cell outputs no longer correspond to the
          current composite, and splitting must be explicitly performed again.
        - Reset ``status`` / ``error_message`` only when generation is not in flight. Manually
          supplying an image is a successful composite output, returning failed records to ready.
          Preserve pending/generating states while in flight; otherwise the record would falsely
          report idle and invalidate the in-flight gates for splitting/uploading.

        A generation task's own generating→completed finalization does not use this method:
        that advances the state machine rather than replacing an image.
        """
        self.grid_image_path = f"grids/{self.id}.png"
        self.split_at = None
        if self.status not in ("pending", "generating"):
            self.status = "completed"
            self.error_message = None

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
        video_aspect_ratio: str,
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
            created_at=datetime.now(UTC).isoformat(),
            error_message=None,
            video_aspect_ratio=video_aspect_ratio,
        )


def build_grid_task_payload(
    *,
    prompt: str | None,
    script_file: str,
    scene_ids: list[str],
    grid_size: str,
    rows: int,
    cols: int,
    grid_aspect_ratio: str,
    video_aspect_ratio: str,
) -> dict:
    """The sole constructor for grid-generation enqueue payloads, shared by HTTP routes and Agent tools.

    When each enqueue path inlines its own literal, changing fields on only one side can make
    the worker receive a payload missing fields on the other path. Centralize construction here.

    Enqueuing does not include provider information: ConfigResolver resolves the provider at
    execution time from the current project configuration (see docs/adr/0001).
    """
    return {
        "prompt": prompt,
        "script_file": script_file,
        "scene_ids": scene_ids,
        "grid_size": grid_size,
        "rows": rows,
        "cols": cols,
        "grid_aspect_ratio": grid_aspect_ratio,
        "video_aspect_ratio": video_aspect_ratio,
    }
