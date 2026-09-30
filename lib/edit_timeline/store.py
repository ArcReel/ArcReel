"""剪辑时间线的文件存储：``edit_timelines/episode_{N}/{timeline_id}.json``，每条一个文件。

剪辑时间线是正式内容，随项目归档导出，不进产物清单。同一集的新建、改名与写入修订在该集的
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
from lib.project.project_manager import ProjectManager

logger = logging.getLogger(__name__)

EDIT_TIMELINES_DIRNAME = "edit_timelines"


class EditTimelineStore:
    def __init__(self, projects: ProjectManager, project_name: str) -> None:
        self._projects = projects
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
        try:
            document = EditTimelineDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError) as exc:
            raise EditTimelineError("timeline_invalid", f"剪辑时间线文件无法解析：{path.name}", file=path.name) from exc
        if document.id != path.stem:
            raise EditTimelineError("timeline_invalid", f"剪辑时间线文件与其 ID 不一致：{path.name}", file=path.name)
        return document

    def list_documents(self, episode: int | None = None) -> list[EditTimelineDocument]:
        """列出剪辑时间线（按集、再按创建时间）；无法解析的文件跳过并记日志。"""
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
                    logger.warning("跳过无法解析的剪辑时间线文件: %s", path, exc_info=True)
                    continue
                if document.episode == number:
                    documents.append(document)
        documents.sort(key=lambda document: (document.episode, document.created_at, document.id))
        return documents

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


__all__ = ["EDIT_TIMELINES_DIRNAME", "EditTimelineStore"]
