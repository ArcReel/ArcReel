"""Episode creation/reordering/deletion (ADR 0096).

- **Create** (:func:`create_episode`): allocate IDs after specified episodes, defaulting broadcast order last. Empty titles display Episode N
  by broadcast positions; provided sources make standalone-source episodes, otherwise sourceless.
- **Reorder** (:func:`move_episode`): standalone/sourceless episodes can move anywhere, including between split episodes;
  placed split episodes always retain source order, rejecting contrary moves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lib.episode.episode_ids import allocate_episode_ids
from lib.episode.episode_ledger import normalize_source_text, parse_positive_episode_num
from lib.episode.episode_paths import episode_script_relpath
from lib.episode.episode_source_commands import write_episode_source_file
from lib.episode.episode_sources import SOURCE_ORIGIN_FIELD, SourceOrigin, cut_episode_placements, discover_sources
from lib.episode.source_kinds import SourceKind, record_episode_kind

if TYPE_CHECKING:
    from lib.project.project_manager import ProjectManager


class EpisodeManagementError(ValueError):
    """Rejected episode management; stable ``code`` maps entry-point statuses/wording. Ledger unchanged."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _ledger(project: dict[str, Any]) -> list[dict[str, Any]]:
    raw = project.get("episodes")
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(entry, dict) for entry in raw):
        raise EpisodeManagementError("ledger_invalid", "分集账本的形状异常")
    return raw


def _index_of(entries: list[dict[str, Any]], episode: int) -> int:
    index = next(
        (i for i, entry in enumerate(entries) if parse_positive_episode_num(entry.get("episode")) == episode), None
    )
    if index is None:
        raise EpisodeManagementError("episode_not_found", f"集（id={episode}）不在账本中")
    return index


def create_episode(
    pm: ProjectManager,
    project_name: str,
    *,
    after: int | None = None,
    title: str = "",
    hook: str = "",
    source_text: str | None = None,
    source_kind: SourceKind | None = None,
) -> int:
    """Create episodes, returning IDs. ``after`` selects preceding episodes, None appending broadcast order.

    Nonblank trimmed ``source_text`` writes episode sources, making standalone episodes; drama projects record ``source_kind``
    default novel. Otherwise sourceless, without kinds.
    """
    text = normalize_source_text(source_text) if source_text is not None else ""
    project_dir = pm.get_project_path(project_name)
    with pm.locked_source_registration(project_name) as (_source_dir, project, undo):
        if project.get("content_mode") == "ad":
            raise EpisodeManagementError("ad_episode_locked", "广告/短片项目只有一集，不能新建或删除集")
        entries = _ledger(project)
        insert_at = len(entries) if after is None else _index_of(entries, after) + 1
        (episode,) = allocate_episode_ids(project, 1)
        origin = SourceOrigin.NONE
        if text.strip():
            write_episode_source_file(project_dir, episode, text, archive_existing=True, undo=undo)
            origin = SourceOrigin.OWN
        entry: dict[str, Any] = {
            "episode": episode,
            "title": title.strip(),
            "script_file": episode_script_relpath(episode),
            SOURCE_ORIGIN_FIELD: origin.value,
        }
        if hook.strip():
            entry["hook"] = hook.strip()
        if origin is SourceOrigin.OWN:
            record_episode_kind(project, entry, source_kind)
        project["episodes"] = [*entries[:insert_at], entry, *entries[insert_at:]]
    return episode


def move_episode(pm: ProjectManager, project_name: str, episode: int, *, after: int | None) -> None:
    """Move after ``after``; ``after`` = None moves first; placed split episodes must preserve source order."""
    project_dir = pm.get_project_path(project_name)

    def _mutate(project: dict[str, Any]) -> None:
        entries = list(_ledger(project))
        if after == episode:
            # Moving after oneself is a no-op.
            _index_of(entries, episode)
            return
        moving = entries.pop(_index_of(entries, episode))
        insert_at = 0 if after is None else _index_of(entries, after) + 1
        entries.insert(insert_at, moving)
        placements = cut_episode_placements(project, discover_sources(project_dir, project))
        positions = [
            placements[num].position
            for entry in entries
            if (num := parse_positive_episode_num(entry.get("episode"))) is not None and num in placements
        ]
        if positions != sorted(positions):
            raise EpisodeManagementError("cut_order_locked", "切出集之间按源文位置排列，不能调换它们的先后")
        project["episodes"] = entries

    pm.update_project(project_name, _mutate)


__all__ = ["EpisodeManagementError", "create_episode", "move_episode"]
