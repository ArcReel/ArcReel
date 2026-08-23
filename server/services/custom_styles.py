"""Shared operations for editing reusable custom styles."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.exc import IntegrityError

from lib.asset_types import validate_asset_name
from lib.db.repositories.asset_repo import AssetRepository
from lib.path_safety import PathTraversalError, safe_join

STYLE_ASSET_TYPE = "style"
MAX_STYLE_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_STYLE_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class CustomStyleEditError(Exception):
    """Base class for expected custom-style edit failures."""


class CustomStyleNotFoundError(CustomStyleEditError):
    pass


class CustomStyleNameConflictError(CustomStyleEditError):
    pass


class CustomStyleEmptyError(CustomStyleEditError):
    pass


class CustomStyleImageError(CustomStyleEditError):
    def __init__(self, reason: Literal["format", "size"]):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CustomStyleImage:
    content: bytes
    extension: str


@dataclass(frozen=True)
class CustomStyleRecord:
    id: str
    name: str
    description: str
    image_path: str | None
    source_project: str | None
    updated_at: datetime | None

    def serialize(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "image_path": self.image_path,
            "source_project": self.source_project,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def _record(style: Any) -> CustomStyleRecord:
    return CustomStyleRecord(
        id=style.id,
        name=style.name,
        description=style.description,
        image_path=style.image_path,
        source_project=style.source_project,
        updated_at=style.updated_at,
    )


def _delete_image(projects_root: Path, relative_path: str | None) -> None:
    if not relative_path:
        return
    try:
        safe_join(projects_root, relative_path).unlink(missing_ok=True)
    except (OSError, PathTraversalError):
        # The database update is already authoritative. A later cleanup can
        # remove an orphan if the filesystem is temporarily unavailable.
        return


def _write_image(projects_root: Path, image: CustomStyleImage) -> str:
    extension = image.extension.lower()
    if extension not in ALLOWED_STYLE_IMAGE_EXTENSIONS:
        raise CustomStyleImageError("format")
    if len(image.content) > MAX_STYLE_IMAGE_BYTES:
        raise CustomStyleImageError("size")
    root = projects_root / "_global_assets" / STYLE_ASSET_TYPE
    root.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{extension}"
    (root / filename).write_bytes(image.content)
    return f"_global_assets/{STYLE_ASSET_TYPE}/{filename}"


async def update_custom_style(
    style_id: str,
    *,
    name: str,
    description: str,
    replacement_image: CustomStyleImage | None = None,
    remove_image: bool = False,
    session_factory: Any,
    projects_root: Path,
) -> CustomStyleRecord:
    """Update one style-library item without mutating linked project snapshots."""

    normalized_name = validate_asset_name(name.strip())
    normalized_description = description.strip()
    if replacement_image is not None and remove_image:
        raise ValueError("replacement_image and remove_image are mutually exclusive")

    new_image_path: str | None = None
    if replacement_image is not None:
        new_image_path = await asyncio.to_thread(_write_image, projects_root, replacement_image)

    old_image_path: str | None = None
    try:
        async with session_factory() as session:
            repo = AssetRepository(session)
            style = await repo.get_by_id(style_id)
            if style is None or style.type != STYLE_ASSET_TYPE:
                raise CustomStyleNotFoundError(style_id)

            if normalized_name != style.name and await repo.exists(STYLE_ASSET_TYPE, normalized_name):
                raise CustomStyleNameConflictError(normalized_name)

            next_image_path = new_image_path if replacement_image is not None else style.image_path
            if remove_image:
                next_image_path = None
            if not normalized_description and not next_image_path:
                raise CustomStyleEmptyError(style_id)

            old_image_path = style.image_path if style.image_path != next_image_path else None
            try:
                style = await repo.update(
                    style_id,
                    name=normalized_name,
                    description=normalized_description,
                    image_path=next_image_path,
                )
                await session.commit()
                await session.refresh(style)
            except IntegrityError as exc:
                await session.rollback()
                raise CustomStyleNameConflictError(normalized_name) from exc
            result = _record(style)
    except Exception:
        if new_image_path:
            await asyncio.to_thread(_delete_image, projects_root, new_image_path)
        raise

    if old_image_path:
        await asyncio.to_thread(_delete_image, projects_root, old_image_path)
    return result


__all__ = [
    "ALLOWED_STYLE_IMAGE_EXTENSIONS",
    "MAX_STYLE_IMAGE_BYTES",
    "STYLE_ASSET_TYPE",
    "CustomStyleEditError",
    "CustomStyleEmptyError",
    "CustomStyleImage",
    "CustomStyleImageError",
    "CustomStyleNameConflictError",
    "CustomStyleNotFoundError",
    "CustomStyleRecord",
    "update_custom_style",
]
