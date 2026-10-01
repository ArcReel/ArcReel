"""剪辑时间线的文件存储：``edit_timelines/episode_{N}/{timeline_id}.json``，每条一个文件。

剪辑时间线是正式内容，随项目归档导出，不进产物清单。同一集的新建、改名、删除与写入修订在该集的
目录锁下串行，保证显示名在集内不重名、修订号连续。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.model import EditTimelineDocument, is_timeline_id
from lib.infra.json_io import atomic_write_json
from lib.project.project_change_hints import build_change_label, emit_project_change_batch
from lib.project.project_manager import ProjectManager

logger = logging.getLogger(__name__)

EDIT_TIMELINES_DIRNAME = "edit_timelines"


def _parse_document(path: Path) -> EditTimelineDocument:
    try:
        document = EditTimelineDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise EditTimelineError("timeline_invalid", f"剪辑时间线文件无法解析：{path.name}", file=path.name) from exc
    if document.id != path.stem:
        raise EditTimelineError("timeline_invalid", f"剪辑时间线文件与其 ID 不一致：{path.name}", file=path.name)
    return document


class EditTimelineStore:
    def __init__(self, projects: ProjectManager, project_name: str) -> None:
        self._projects = projects
        self._project_name = project_name
        self._root = projects.get_project_path(project_name) / EDIT_TIMELINES_DIRNAME

    def _episode_dir(self, episode: int) -> Path:
        return self._root / f"episode_{episode}"

    def _episode_dirs(self) -> list[tuple[int, Path]]:
        if not self._root.is_dir():
            return []
        found: list[tuple[int, Path]] = []
        for path in self._root.iterdir():
            prefix, _, number = path.name.partition("_")
            if path.is_dir() and prefix == "episode" and number.isdigit() and int(number) >= 1:
                found.append((int(number), path))
        return sorted(found)

    @staticmethod
    def _parse(path: Path) -> EditTimelineDocument:
        return _parse_document(path)

    def list_documents(self, episode: int | None = None, *, strict: bool = False) -> list[EditTimelineDocument]:
        """列出剪辑时间线（按集、再按创建时间）。

        无法解析的文件默认跳过并记日志；``strict`` 时抛出 ``timeline_invalid``。
        """
        documents: list[EditTimelineDocument] = []
        for number, directory in self._episode_dirs():
            if episode is not None and number != episode:
                continue
            for path in sorted(directory.glob("*.json")):
                if not is_timeline_id(path.stem):
                    continue
                try:
                    document = self._parse(path)
                except EditTimelineError:
                    if strict:
                        raise
                    logger.warning("跳过无法解析的剪辑时间线文件: %s", path, exc_info=True)
                    continue
                if document.episode == number:
                    documents.append(document)
        documents.sort(key=lambda document: (document.episode, document.created_at, document.id))
        return documents

    def has_documents(self, episode: int) -> bool:
        """该集目录下有没有剪辑时间线文件；只看文件名，不解析内容，供只要计数口径的广度视图使用。"""
        directory = self._episode_dir(episode)
        if not directory.is_dir():
            return False
        return any(is_timeline_id(path.stem) for path in directory.glob("*.json"))

    def find(self, timeline_id: str) -> EditTimelineDocument:
        if is_timeline_id(timeline_id):
            for number, directory in self._episode_dirs():
                path = directory / f"{timeline_id}.json"
                if path.is_file():
                    document = self._parse(path)
                    if document.episode != number:
                        raise EditTimelineError(
                            "timeline_invalid", f"剪辑时间线文件与所在集不一致：{path.name}", file=path.name
                        )
                    return document
        raise EditTimelineError("timeline_not_found", f"剪辑时间线「{timeline_id}」不存在", timeline_id=timeline_id)

    @contextmanager
    def locked_episode(self, episode: int) -> Generator[None]:
        """串行化同一集剪辑时间线的写入。"""
        with self._projects.file_lock(self._episode_dir(episode)):
            yield

    def write(self, document: EditTimelineDocument) -> None:
        """整份原子写入；调用方须持有该集的锁。"""
        path = self._episode_dir(document.episode) / f"{document.id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, document.model_dump(mode="json"))
        self._notify(document.episode)

    def delete(self, document: EditTimelineDocument) -> None:
        """删除一条剪辑时间线的文件；调用方须持有该集的锁。"""
        (self._episode_dir(document.episode) / f"{document.id}.json").unlink(missing_ok=True)
        self._notify(document.episode)

    def _notify(self, episode: int) -> None:
        emit_project_change_batch(
            self._project_name,
            [
                {
                    "entity_type": "episode",
                    "action": "updated",
                    "entity_id": str(episode),
                    "episode": episode,
                    **build_change_label("episode", episode=episode),
                    "focus": None,
                    "important": False,
                }
            ],
        )


def read_timeline_document(project_dir: Path, episode: int, timeline_id: str) -> EditTimelineDocument | None:
    """按集与 ID 直接读一条剪辑时间线；文件不存在、无法解析或与所在集不一致时返回 None。

    供只持有项目目录的读方（产物时效判定）使用，不取集锁。
    """
    if not is_timeline_id(timeline_id):
        return None
    path = project_dir / EDIT_TIMELINES_DIRNAME / f"episode_{episode}" / f"{timeline_id}.json"
    if not path.is_file():
        return None
    try:
        document = _parse_document(path)
    except EditTimelineError:
        return None
    return document if document.episode == episode else None


__all__ = ["EDIT_TIMELINES_DIRNAME", "EditTimelineStore", "read_timeline_document"]
