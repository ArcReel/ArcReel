"""Host-neutral read-only tool that renders contact sheets for video units so an Agent can look at them.

Each unit's chosen video version is read from its version snapshot, sampled evenly and laid out by
``lib.video_review.contact_sheet``; the sheets travel back as image content blocks after the JSON.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from lib.artifacts.version_manager import UnmanagedSnapshotPathError, VersionManager
from lib.infra.ffmpeg import FfmpegUnavailableError
from lib.infra.media_probe import MediaProbeError
from lib.project.project_manager import is_reference_video_project
from lib.video_review.contact_sheet import ContactSheet, ContactSheetError, build_contact_sheets
from server.agent_toolset.declaration import ToolImage
from server.media_tools.context import tool_error, tool_problem
from server.media_tools.video_versions import project_video_unit_ids
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest

_OPERATION = "inspect_video_units"

MAX_FRAMES_PER_UNIT = 24
MAX_FRAMES_PER_CALL = 96
DEFAULT_FRAMES_PER_UNIT = 8
_EXTRACT_CONCURRENCY = 3

type UnitStatus = Literal["ok", "video_missing", "video_unreadable"]


class InspectVideoUnitsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_ids: list[str] = Field(
        min_length=1,
        description="要看的视频单元 ID 列表：分镜图生视频为分镜 ID（如 E1S01），参考生视频为 unit_id（如 E1U1）",
    )
    version: StrictInt | None = Field(
        default=None,
        ge=1,
        description="视频版本号，对列表中每个视频单元都取这个版本；省略时取各单元的 current 版本",
    )
    frames: StrictInt = Field(
        default=DEFAULT_FRAMES_PER_UNIT,
        ge=1,
        le=MAX_FRAMES_PER_UNIT,
        description=f"每个视频单元均匀抽取的帧数，默认 {DEFAULT_FRAMES_PER_UNIT}",
    )


@dataclass(frozen=True, slots=True)
class InspectedUnit:
    unit_id: str
    version: int | None
    status: UnitStatus
    sheets: tuple[ContactSheet, ...] = ()
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class InspectVideoUnitsResult:
    frames_per_unit: int
    units: tuple[InspectedUnit, ...]
    model_review: None = field(default=None)
    """预留给服务端原生视频 MLLM 审阅；目前恒为 None。"""


def _snapshot_of(
    project_path: Path, versions: VersionManager, resource_type: str, unit_id: str, version: int
) -> Path | None:
    for record in versions.get_versions(resource_type, unit_id).get("versions", []):
        if isinstance(record, Mapping) and record.get("version") == version:
            try:
                path = VersionManager.resolve_snapshot_path(project_path, resource_type, record.get("file"))
            except UnmanagedSnapshotPathError:
                return None
            return path if path.is_file() else None
    return None


def _available_versions(versions: VersionManager, resource_type: str, unit_id: str) -> list[int]:
    return [
        record["version"]
        for record in versions.get_versions(resource_type, unit_id).get("versions", [])
        if isinstance(record, Mapping) and isinstance(record.get("version"), int)
    ]


async def _inspect_unit(
    video: Path | None, *, unit_id: str, version: int | None, frames: int, limiter: asyncio.Semaphore
) -> InspectedUnit:
    if video is None or version is None:
        return InspectedUnit(unit_id=unit_id, version=version, status="video_missing")
    async with limiter:
        try:
            sheets = await build_contact_sheets(video, unit_id=unit_id, version=version, frames=frames)
        except (MediaProbeError, ContactSheetError) as exc:
            return InspectedUnit(unit_id=unit_id, version=version, status="video_unreadable", detail=str(exc))
    return InspectedUnit(unit_id=unit_id, version=version, status="ok", sheets=sheets)


async def inspect_video_units(
    request: ToolRequest[InspectVideoUnitsRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[InspectVideoUnitsResult]:
    unit_ids = list(dict.fromkeys(request.value.unit_ids))
    if len(unit_ids) > MAX_FRAMES_PER_CALL:
        return tool_problem(
            f"一次最多看 {MAX_FRAMES_PER_CALL} 帧，{len(unit_ids)} 个视频单元每个至少一帧已超出上限，请分批调用",
            code="frame_budget_exceeded",
            params={"max_frames_per_call": MAX_FRAMES_PER_CALL},
        )
    frames = min(request.value.frames, MAX_FRAMES_PER_CALL // len(unit_ids))
    try:
        project = services.projects.load_project(scope.project_name)
        known = project_video_unit_ids(services.projects, scope.project_name, project)
        missing = [unit_id for unit_id in unit_ids if unit_id not in known]
        if missing:
            return tool_problem(
                f"视频单元不存在：{', '.join(missing)}", code="video_unit_not_found", params={"unit_ids": missing}
            )

        resource_type = "reference_videos" if is_reference_video_project(project) else "videos"
        project_path = services.projects.get_project_path(scope.project_name)
        versions = VersionManager(project_path)
        targets: list[tuple[str, int | None, Path | None]] = []
        for unit_id in unit_ids:
            version = request.value.version
            if version is None:
                current = versions.get_current_version(resource_type, unit_id)
                version = current if current > 0 else None
            elif version not in (available := _available_versions(versions, resource_type, unit_id)):
                return tool_problem(
                    f"视频单元「{unit_id}」没有版本 v{version}；现有版本：{', '.join(map(str, available)) or '无'}",
                    code="version_not_found",
                    params={"unit_id": unit_id, "available_versions": available},
                )
            video = (
                await asyncio.to_thread(_snapshot_of, project_path, versions, resource_type, unit_id, version)
                if version is not None
                else None
            )
            targets.append((unit_id, version, video))

        limiter = asyncio.Semaphore(_EXTRACT_CONCURRENCY)
        units = await asyncio.gather(
            *(
                _inspect_unit(video, unit_id=unit_id, version=version, frames=frames, limiter=limiter)
                for unit_id, version, video in targets
            )
        )
        return ToolOutcome(value=InspectVideoUnitsResult(frames_per_unit=frames, units=tuple(units)))
    except FfmpegUnavailableError as exc:
        return tool_problem(f"随包 ffmpeg 不可用，无法出联系表：{exc}", code="ffmpeg_unavailable")
    except Exception as exc:
        return tool_error(_OPERATION, exc)


def _sheets_in_order(value: InspectVideoUnitsResult) -> list[ContactSheet]:
    return [sheet for unit in value.units for sheet in unit.sheets]


def inspect_video_units_projection(value: InspectVideoUnitsResult) -> dict[str, Any]:
    image_number = 0
    units: list[dict[str, Any]] = []
    for unit in value.units:
        sheets: list[dict[str, Any]] = []
        for sheet in unit.sheets:
            image_number += 1
            sheets.append({"image": image_number, "times": [round(frame.time_seconds, 3) for frame in sheet.frames]})
        entry: dict[str, Any] = {
            "unit_id": unit.unit_id,
            "version": unit.version,
            "status": unit.status,
            "sheets": sheets,
        }
        if unit.detail is not None:
            entry["detail"] = unit.detail
        units.append(entry)
    return {
        "inspect_video_units": {
            "frames_per_unit": value.frames_per_unit,
            "units": units,
            "model_review": value.model_review,
        }
    }


def inspect_video_units_images(value: InspectVideoUnitsResult) -> list[ToolImage]:
    return [ToolImage(data=sheet.jpeg, mime_type="image/jpeg") for sheet in _sheets_in_order(value)]


def inspect_video_units_summary(value: InspectVideoUnitsResult) -> str:
    sheets = len(_sheets_in_order(value))
    unseen = [unit.unit_id for unit in value.units if unit.status != "ok"]
    text = f"已为 {len(value.units) - len(unseen)} 个视频单元出 {sheets} 张联系表（每单元至多 {value.frames_per_unit} 帧），图片按 image 序号附在后面"
    return f"{text}；没有可看的画面：{', '.join(unseen)}" if unseen else text


__all__ = [
    "DEFAULT_FRAMES_PER_UNIT",
    "MAX_FRAMES_PER_CALL",
    "MAX_FRAMES_PER_UNIT",
    "InspectVideoUnitsRequest",
    "InspectVideoUnitsResult",
    "inspect_video_units",
    "inspect_video_units_images",
    "inspect_video_units_projection",
    "inspect_video_units_summary",
]
