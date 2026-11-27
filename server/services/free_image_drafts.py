"""Project-scoped free image drafts."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import formal_write_transaction
from lib.infra.json_io import atomic_write_json, load_json
from lib.infra.path_safety import safe_join
from lib.project import project_trash
from lib.project.project_manager import get_project_manager


def _directory(project_name: str) -> Path:
    return safe_join(get_project_manager().get_project_path(project_name), "image_drafts")


def list_drafts(project_name: str) -> list[dict[str, Any]]:
    items = []
    for path in _directory(project_name).glob("*.json"):
        try:
            items.append(load_json(path))
        except FileNotFoundError:
            continue  # A concurrent deletion moved this draft to the trash.
    return sorted(items, key=lambda item: item["created_at"], reverse=True)


def commit_draft(
    project_name: str,
    staged: Path,
    output: Path,
    *,
    resource_id: str,
    prompt: str,
    model: str,
    provider: str,
    aspect_ratio: str,
    resolution: str | None,
) -> dict[str, Any]:
    pm = get_project_manager()
    with pm.locked_project_snapshot(project_name):
        project_dir = pm.get_project_path(project_name)
        record = output.with_suffix(".json")
        draft = {
            "id": resource_id,
            "prompt": prompt,
            "model": model,
            "provider": provider,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "file_path": output.relative_to(project_dir).as_posix(),
            "created_at": datetime.now(UTC).isoformat(),
        }
        with formal_write_transaction(output, record):
            staged.replace(output)
            atomic_write_json(record, draft)
        return draft


def read_draft_image(project_name: str, draft_id: str) -> bytes:
    pm = get_project_manager()
    with pm.locked_project_snapshot(project_name):
        directory = _directory(project_name)
        load_json(safe_join(directory, f"{draft_id}.json"))
        return safe_join(directory, f"{draft_id}.png").read_bytes()


def delete_draft(project_name: str, draft_id: str) -> None:
    pm = get_project_manager()
    with pm.locked_project_snapshot(project_name):
        project_dir = pm.get_project_path(project_name)
        directory = _directory(project_name)
        record = safe_join(directory, f"{draft_id}.json")
        image = safe_join(directory, f"{draft_id}.png")
        if not record.is_file():
            raise FileNotFoundError(record)
        files = [path.relative_to(project_dir).as_posix() for path in (record, image) if path.is_file()]
        project_trash.record_deletion(project_dir, {"kind": "file", "path": files[0]}, files)
