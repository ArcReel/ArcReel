"""v3→v4: legacy task-specific text backends → task tiers (docs/adr/0051).

Map text_backend_script → text_backend_complex; text_backend_overview / text_backend_style →
text_backend_simple. If both exist, style wins because style tasks require vision; the reverse could
select a model lacking image inputs. Delete old keys after migration."""

from __future__ import annotations

from pathlib import Path

from lib.infra.json_io import atomic_write_json, load_json

_LEGACY_TEXT_TASK_KEYS = ("text_backend_script", "text_backend_overview", "text_backend_style")


def _clean_str(value: object) -> str | None:
    """Return nonempty strings unchanged; missing/null/empty/nonstring values count as unset."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def migrate_project_dict(project: dict) -> dict:
    """Pure, idempotent v3→v4 project-dict conversion.

    Preserve existing tier values so retries do not revert later user configuration.
    Leave schema_version to file-level commit."""
    data = dict(project)

    script = _clean_str(data.get("text_backend_script"))
    if script and not _clean_str(data.get("text_backend_complex")):
        data["text_backend_complex"] = script

    simple = _clean_str(data.get("text_backend_style")) or _clean_str(data.get("text_backend_overview"))
    if simple and not _clean_str(data.get("text_backend_simple")):
        data["text_backend_simple"] = simple

    for key in _LEGACY_TEXT_TASK_KEYS:
        data.pop(key, None)

    return data


def migrate_v3_to_v4(project_dir: Path) -> None:
    """File-level v3→v4 migration: one atomic write, retryable after crashes with old/new values and no partial state."""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    # Match runner int normalization: legacy project.json may store version strings.
    if int(data.get("schema_version") or 0) >= 4:
        return
    migrated = migrate_project_dict(data)
    migrated["schema_version"] = 4
    atomic_write_json(pj, migrated)
