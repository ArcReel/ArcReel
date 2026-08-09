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
    # 联合图生命周期：completed 仅表示联合图就绪，是否已切分落格由 split_at 表达
    status: Literal["pending", "generating", "completed", "failed"]
    prompt: str | None
    provider: str
    model: str
    grid_size: str
    created_at: str
    error_message: str | None = None
    reference_images: list[ReferenceImage] | None = None
    # 最近一次按当前联合图切分落格的时间；联合图内容变更（重新生成/上传/版本还原）时清空
    split_at: str | None = None

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
        }

    @classmethod
    def from_dict(cls, data: dict) -> GridGeneration:
        # 生成与切分曾是同一任务，中间态记录可能残留 status="splitting"；
        # 该态下联合图已落盘，按当前模型等价于「联合图就绪、未落格」。
        raw_status = data["status"]
        status = "completed" if raw_status == "splitting" else raw_status
        # 旧记录没有 split_at 字段。旧流程下 status="completed" 只在切格落盘之后才写入，
        # 因此这类记录等价于「已切分」，用 created_at 充当落格时间；若一律读成未切分，
        # 前端会提示待切分，用户照做就会用旧联合图覆盖之后单独重生成过的分镜图。
        # 显式为 null 的新记录、以及 status="splitting"（联合图已就绪但未落格）保持未切分。
        if "split_at" not in data and raw_status == "completed":
            split_at = data["created_at"]
        else:
            split_at = data.get("split_at")
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
        )

    def mark_composite_replaced(self) -> None:
        """联合图被用户动作换成新内容（手动上传 / 版本还原）后复位记录。

        - ``split_at`` 无条件清空：旧的落格结果不再对应当前联合图，落格须重新显式执行；
        - ``status`` / ``error_message`` 仅在生成不在途时复位——手动补图等价于一次成功的
          联合图产出，failed 记录就此回到就绪态；pending/generating 期间保留在途态，
          否则记录会谎报空闲，切分/上传的在途闸门随之失效。

        生成任务自身的 generating→completed 收尾不走本方法：那是状态机推进而非替换。
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
    """宫格生成任务入队 payload 的唯一构造点，HTTP 路由与 SDK 工具共用。

    两条入队路径各自内联字面量时，字段增删只改一侧就会让 worker 在另一条路径上
    读到缺字段的 payload，故收在此处。

    入队不携带 provider 信息——provider 在执行时由 ConfigResolver 按当前项目配置解析
    （见 docs/adr/0001）。
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
