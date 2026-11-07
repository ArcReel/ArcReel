"""Persist/register local renders: basis snapshot → render to project-local temporary file → acceptance → atomic formal-file replacement and registration.

Locally rendered artifacts such as final videos and Jianying drafts share this flow (``docs/adr/0095``):

1. Callers snapshot generation bases at task start and pass ``basis``.
2. Render into hidden temporary files beside formal files; intermediates use similarly hidden work directories.
3. Acceptance callbacks inspect temporary files and raise on failure, leaving formal files/registrations untouched.
4. On acceptance, remove old registration, atomically replace the formal file, write version records first,
   and finally register against the snapshot basis.

If registration fails after replacement, formal contents are new but unregistered, reading as missing
rather than falsely current. Each identity retains only its latest file; versions in adjacent render
records increment per registration. Rendered artifacts live under project ``renders/``, excluded from archives."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.async_thread import run_sync_transaction
from lib.infra.json_io import atomic_write_json
from lib.project.project_activity import record_project_activity

RENDERS_DIRNAME = "renders"
"""Project root directory for local renders: rerenderable anytime, excluded from project archives."""

_RECORD_SUFFIX = ".render.json"


def timeline_renders_dir(episode: int, timeline_id: str) -> str:
    """Project-relative directory for all renders of one editing timeline; timeline IDs are never reused and deletion removes the directory."""
    return f"{RENDERS_DIRNAME}/episode_{episode}/{timeline_id}"


@dataclass(frozen=True, slots=True)
class RenderRecord:
    """Render record beside a formal file: version, registration time, and registered basis."""

    version: int
    rendered_at: str
    basis_digest: str


@dataclass(frozen=True, slots=True)
class RenderedArtifact[AcceptanceT]:
    key: ArtifactKey
    artifact_path: str
    record: RenderRecord
    acceptance: AcceptanceT


type RenderStep = Callable[[Path, Path], Awaitable[None]]
"""``render(output, workspace)``: write artifacts to ``output``, placing intermediates only in ``workspace``."""

type AcceptStep[AcceptanceT] = Callable[[Path], Awaitable[AcceptanceT]]
"""``accept(output)``: inspect the temporary file and return acceptance results, raising on failure."""


def _record_path(formal: Path) -> Path:
    return formal.with_name(formal.stem + _RECORD_SUFFIX)


def read_render_record(project_dir: Path, artifact_path: str) -> RenderRecord | None:
    """Read the adjacent render record; return None if absent or unparseable."""
    try:
        payload = json.loads(_record_path(project_dir / artifact_path).read_text(encoding="utf-8"))
        return RenderRecord(
            version=int(payload["version"]),
            rendered_at=str(payload["rendered_at"]),
            basis_digest=str(payload["basis_digest"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _replace_and_register(
    project_dir: Path, key: ArtifactKey, artifact_path: str, rendered: Path, basis: ArtifactBasis
) -> RenderRecord:
    formal = project_dir / artifact_path
    with project_metadata_lock(project_dir):
        previous = read_render_record(project_dir, artifact_path)
        adapter = ProjectArtifactManifestAdapter(project_dir)
        claimed = adapter.get_entry(key)
        manifest = ArtifactManifest(adapter)
        manifest.forget_entry_transactionally(key)
        try:
            os.replace(rendered, formal)
        except BaseException:
            # Formal file was not replaced; the old file still matches its registration.
            if claimed is not None and adapter.get_entry(key) is None:
                adapter.put_entry(key, claimed)
            raise
        record = RenderRecord(
            version=(previous.version if previous is not None else 0) + 1,
            rendered_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            basis_digest=basis.digest,
        )
        atomic_write_json(
            _record_path(formal),
            {"version": record.version, "rendered_at": record.rendered_at, "basis_digest": record.basis_digest},
        )
        manifest.register(key, artifact_path=artifact_path, basis=basis)
        record_project_activity(project_dir)
    return record


def cleanup_interrupted_render_staging(project_dir: Path) -> None:
    """Clean render staging after restart; callers must ensure no render remains running for the project in this process."""
    root = project_dir / RENDERS_DIRNAME
    for path in root.rglob(".*"):
        if re.fullmatch(r"(?:\..+\.[0-9a-f]{32}\.(?:work|partial\.[^.]+)|\.project\.[^.]+\.tmp)", path.name):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)  # hard-delete: temp -- interrupted-render .work directory cleaned after restart
            else:
                path.unlink(
                    missing_ok=True
                )  # hard-delete: temp -- interrupted-render temporary file cleaned after restart


def _discard(output: Path, workspace: Path) -> None:
    output.unlink(missing_ok=True)  # hard-delete: temp -- unadopted render output
    shutil.rmtree(workspace, ignore_errors=True)  # hard-delete: temp -- render work directory


async def commit_rendered_artifact[AcceptanceT](
    project_dir: Path,
    *,
    key: ArtifactKey,
    artifact_path: str,
    basis: ArtifactBasis,
    render: RenderStep,
    accept: AcceptStep[AcceptanceT],
) -> RenderedArtifact[AcceptanceT]:
    """Render, accept, and register one local render against a snapshot basis; clean temporary files/work directories on failure at any step."""
    formal = project_dir / artifact_path
    await asyncio.to_thread(formal.parent.mkdir, parents=True, exist_ok=True)
    token = uuid4().hex
    output = formal.with_name(f".{formal.stem}.{token}.partial{formal.suffix}")
    workspace = formal.with_name(f".{formal.stem}.{token}.work")
    try:
        await run_sync_transaction(workspace.mkdir)
        await render(output, workspace)
        acceptance = await accept(output)
        record = await run_sync_transaction(_replace_and_register, project_dir, key, artifact_path, output, basis)
    finally:
        await asyncio.to_thread(_discard, output, workspace)
    return RenderedArtifact(key=key, artifact_path=artifact_path, record=record, acceptance=acceptance)


__all__ = [
    "RENDERS_DIRNAME",
    "AcceptStep",
    "RenderRecord",
    "RenderStep",
    "RenderedArtifact",
    "cleanup_interrupted_render_staging",
    "commit_rendered_artifact",
    "read_render_record",
]
