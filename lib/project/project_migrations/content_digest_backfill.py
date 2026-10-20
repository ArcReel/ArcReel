"""v17→v18 的内容指纹补录：给存量视频与配音记下产物自身的内容指纹（``docs/adr/0062``）。

v18 起视频与音频产物登记时，清单条目记下正式文件的内容事实（指纹、字节数、纳秒 mtime），版本记录
记下快照的指纹。存量产物在这一步补录：已登记条目的正式文件与选中版本快照各取一次指纹，两者一致才
同时写入版本记录与清单条目；读不出、不一致或找不到选中快照的不补录，判定与补录前相同。

这一步跑在缺口补录之后，规划器刚对同一批文件取过指纹，按 stat 标识命中进程内摘要缓存，不再读第二遍。
字节数与 mtime 取自取指纹时打开的那个描述符。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from lib.artifacts.artifact_manifest import (
    CONTENT_FACT_ARTIFACT_KINDS,
    ArtifactContentFacts,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestEntry,
    ArtifactObservation,
    ProjectArtifactManifestAdapter,
    same_entry_records,
)
from lib.artifacts.version_manager import VERSION_CONTENT_DIGEST, VersionManager
from lib.infra.json_io import atomic_write_bytes, atomic_write_json
from lib.project.resource_paths import resource_relative_path


def backfill_content_digests(project_dir: Path) -> None:
    """补录已登记视频与配音的内容指纹。调用方持有项目元数据锁，并已在改写前备份清单与版本记录。

    先写版本记录、再提交清单；清单提交失败时把版本记录写回改写前的字节，重跑从头再来。
    """

    project_dir = Path(project_dir)
    versions_path = project_dir / "versions" / "versions.json"
    if not versions_path.is_file():
        return
    versions_bytes = versions_path.read_bytes()
    versions = json.loads(versions_bytes)
    if not isinstance(versions, dict):
        raise ValueError("versions/versions.json must contain an object")
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    proven: dict[ArtifactKey, ArtifactManifestEntry] = {}
    amended = False
    for key, entry in stored.items():
        if key.kind not in CONTENT_FACT_ARTIFACT_KINDS:
            continue
        resource_type = _resource_type(key, entry.artifact_path)
        if resource_type is None:
            continue
        record = _selected_record(versions, resource_type, str(key.components[1]))
        snapshot_path = record.get("file") if record is not None else None
        if record is None or not VersionManager.is_managed_snapshot_path(resource_type, snapshot_path):
            continue
        formal = _read_content(adapter, entry.artifact_path)
        snapshot = _read_content(adapter, str(snapshot_path))
        if formal is None or snapshot is None:
            continue
        formal_stat = formal.file_stat
        assert formal_stat is not None
        digest = cast(str, formal.content_digest)
        if digest != snapshot.content_digest:
            continue
        amended = amended or record.get(VERSION_CONTENT_DIGEST) != digest
        record[VERSION_CONTENT_DIGEST] = digest
        proven[key] = replace(
            entry,
            content=ArtifactContentFacts(digest=digest, size=formal_stat.st_size, mtime_ns=formal_stat.st_mtime_ns),
        )
    if not proven:
        return
    if amended:
        atomic_write_json(versions_path, versions)
    try:
        expected = {key: stored[key] for key in proven}
        # 没写入也可能是上一次中断的重跑已经写过同样的事实；只有清单真被改动过才算冲突。
        if not adapter.replace_entries_if_matches_atomically(
            expected=expected, replacements=proven
        ) and not same_entry_records({key: adapter.get_entry(key) for key in proven}, proven):
            raise RuntimeError("artifact manifest changed while backfilling content digests")
    except BaseException:
        if amended:
            atomic_write_bytes(versions_path, versions_bytes)
        raise


def _resource_type(key: ArtifactKey, artifact_path: str) -> str | None:
    """条目所属的版本资源类型：配音固定为 audio；视频按规范路径区分分镜视频与参考视频。"""

    resource_id = str(key.components[1])
    candidates = ("audio",) if key.kind is ArtifactKind.EPISODE_AUDIO else ("videos", "reference_videos")
    return next((name for name in candidates if resource_relative_path(name, resource_id) == artifact_path), None)


def _selected_record(versions: dict[str, Any], resource_type: str, resource_id: str) -> dict[str, Any] | None:
    bucket = versions.get(resource_type)
    resource = bucket.get(resource_id) if isinstance(bucket, dict) else None
    if not isinstance(resource, dict):
        return None
    selected_version = resource.get("current_version")
    records = resource.get("versions")
    if type(selected_version) is not int or not isinstance(records, list):
        return None
    selected = [record for record in records if isinstance(record, dict) and record.get("version") == selected_version]
    return selected[0] if len(selected) == 1 else None


def _read_content(adapter: ProjectArtifactManifestAdapter, artifact_path: str) -> ArtifactObservation | None:
    observation = adapter.inspect_artifact_content(artifact_path)
    if observation.blocker is not None or not observation.present or observation.content_digest is None:
        return None
    return observation


__all__ = ["backfill_content_digests"]
