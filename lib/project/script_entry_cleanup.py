"""脚本条目离开正式脚本时，名下的媒体与版本历史移进项目回收站（``docs/adr/0107``）。

两种离开：结构移除（批量编辑与 Agent 的 remove、覆盖正式脚本时旧脚本里不再有的条目），以及身份替换
（重插新条目、覆盖正式脚本时新条目沿用了旧编号）。替换时留着旧条目的版本历史或当前文件，下一次生成会把旧内容
接成新条目的历史，回滚就能回到不属于它的画面，所以两种都把旧条目的文件整个移走。

每个条目一条带结构快照的删除记录，快照里是改写前的剧本，恢复见 :mod:`lib.project.script_entry_trash`。
结构移除的条目即使没有文件也建记录（恢复的是条目本身）；覆盖正式脚本时旧脚本里的条目都算结构移除。
批量编辑里只被替换、不在改前脚本里的编号没有条目可恢复，没有文件时不建记录。

单列一个模块是因为它同时需要 :mod:`lib.artifacts.version_manager`（历史）与
:mod:`lib.project.resource_paths`（当前文件路径）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Iterable, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import formal_write_transaction
from lib.artifacts.version_manager import VersionManager
from lib.infra.path_safety import try_safe_join
from lib.project import project_trash
from lib.project.resource_paths import END_FRAME_RESOURCE_TYPE, resource_relative_path
from lib.script.item_excerpt import position_snapshot
from lib.speech.narration_config import POST_PRODUCTION, USE_TTS
from lib.speech.speech_presentation import presentation_artifact_paths

#: 删除记录的对象类型；对象描述 ``{"kind", "id": 条目编号, "episode", "script_file", "item_kind": 条目数组键}``，
#: 条目在改写前的剧本里时另带删除时的称呼快照 ``item_position`` / ``item_excerpt``，回收站凭它们称呼条目。
SCRIPT_ENTRY_KIND = "script_entry"

#: 按条目编号命名的媒体资源类型：分镜图、尾帧快照、分镜视频、参考生视频单元视频、旁白配音。
ENTRY_MEDIA_RESOURCE_TYPES: tuple[str, ...] = (
    "storyboards",
    END_FRAME_RESOURCE_TYPE,
    "videos",
    "reference_videos",
    "audio",
)


def entry_media_paths(entry_id: str) -> list[str]:
    """按条目编号命名的媒体与视频缩略图（项目内相对 POSIX 路径），不论是否存在。"""
    return [
        *(resource_relative_path(resource_type, entry_id) for resource_type in ENTRY_MEDIA_RESOURCE_TYPES),
        f"thumbnails/scene_{entry_id}.jpg",
        f"reference_videos/thumbnails/{entry_id}.jpg",
    ]


def entry_derived_paths(episode: int, entry_id: str) -> list[str]:
    """条目的字幕草稿与呈现（两种配音方式各一份），不论是否存在；集号或编号不成立时没有。"""
    try:
        return [
            rel
            for variant in (POST_PRODUCTION, USE_TTS)
            for rel in presentation_artifact_paths(episode, entry_id, variant)
        ]
    except ValueError:
        return []


def entry_files(project_dir: Path, episode: int, entry_id: str) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """``project_dir`` 里属于这个条目的普通文件与版本记录；删除时取当前项目，恢复时取迁移后的临时项目。

    编号来自盘上的剧本，不可信任：解析不出项目内路径的编号没有文件。
    """
    versions = VersionManager(project_dir)
    history = versions.resource_records((resource_type, entry_id) for resource_type in ENTRY_MEDIA_RESOURCE_TYPES)
    candidates = [
        *entry_media_paths(entry_id),
        *entry_derived_paths(episode, entry_id),
        *versions.snapshot_files(history),
    ]
    files = [
        rel
        for rel in dict.fromkeys(candidates)
        if (path := try_safe_join(project_dir, rel)) is not None and path.is_file() and not path.is_symlink()
    ]
    return files, history


def trash_entries(
    project_dir: Path,
    *,
    episode: int,
    script_file: str,
    entries: Mapping[str, str],
    before: Mapping[str, bytes],
    removed: Collection[str] = (),
    then: Callable[[], None] | None = None,
) -> list[project_trash.TrashRecord]:
    """把这些条目（编号 → 条目数组键）名下的文件移进项目回收站，每个条目一条删除记录，最后执行 ``then``。

    调用方持该剧本的锁与项目锁，在剧本写入的同一临界区里、产物清单撤登记之前调用（``then`` 传入撤登记），
    快照里的清单与版本历史才带着条目的登记；``before`` 是同一临界区里已改写的结构文件（剧本、``project.json``）
    的改前内容。``removed`` 是其中被结构移除的条目，没有文件也建记录。任一步失败时撤回已建的记录、版本历史回到调用前，异常照常抛出，由调用方的写事务回滚剧本。
    """
    versions = VersionManager(project_dir)
    records: list[project_trash.TrashRecord] = []
    script_before = _script_before(before, script_file)
    with formal_write_transaction(versions.versions_file):
        try:
            for entry_id, item_kind in entries.items():
                files, history = entry_files(project_dir, episode, entry_id)
                if not files and entry_id not in removed:
                    continue
                obj = {
                    "kind": SCRIPT_ENTRY_KIND,
                    "id": entry_id,
                    "episode": episode,
                    "script_file": script_file,
                    "item_kind": item_kind,
                    **position_snapshot(script_before, entry_id),
                }
                records.append(project_trash.record_deletion(project_dir, obj, files, snapshot=True, before=before))
                # 快照文件已随记录移走，只摘版本记录。
                versions.detach_resources(_resources(history))
            if then is not None:
                then()
        except BaseException:
            for record in reversed(records):
                project_trash.withdraw(project_dir, record.id)
            raise
    return records


def _script_before(before: Mapping[str, bytes], script_file: str) -> Mapping[str, Any]:
    """改写前的剧本；读不出时为空，记录不带称呼快照。"""
    try:
        script = json.loads(before.get(f"scripts/{script_file}", b"{}"))
    except ValueError:
        return {}
    return script if isinstance(script, Mapping) else {}


def _resources(history: Mapping[str, Mapping[str, Any]]) -> Iterable[tuple[str, str]]:
    return ((resource_type, resource_id) for resource_type, bucket in history.items() for resource_id in bucket)
