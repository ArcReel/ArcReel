"""v15→v16 迁移：剪辑决策移出脚本条目。

本步由互不依赖的子步组成，``migrate_v15_to_v16`` 依次调用；同一发行版里并入本步的改动追加一个子步。

**转场移入剪辑时间线（ADR 0090）**

- 已绑定剧本的全部条目删除 ``transition_to_next``，非硬切值直接丢弃，不迁移到任何地方。
- 当前呈现模型的依据不再记转场。持久化的呈现模型文件（``presentations/``）删去转场、换上当前依据；
  清单登记恰是文件记录的旧依据的，改写为当前依据。旧依据本身包含脚本上的转场，只因转场与文件不同而
  过期的登记随之转为时新；因其他输入过期的登记与文件记录不同，原样保留。字幕依据从未记转场，不动。
  文件与自身记录的旧依据对不上的呈现模型此前就不被认领，本步不改它。

提交顺序是剧本 → 清单 → 呈现模型文件 → ``project.json``：中途崩溃时整步重跑，已改的剧本与文件
按「已无转场」跳过；清单改写先于文件，重跑时仍能从未改的文件算出旧依据，已改写的登记不再匹配而被
跳过。呈现模型文件是选中媒体的派生物，读时可重新物化，不另做备份。

本步只改写既有登记、不增删；迁移结果按改写后完整目标态的跳过项与实际清单计数生成。
它不解决此前的跳过原因，runner 合并链上更早一步或已有迁移报告的跳过项。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_planner import TargetStatePlanner
from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.json_io import atomic_write_json
from lib.infra.path_safety import try_safe_join
from lib.project.project_migration_report import ArtifactBackfillOutcome
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.project.project_schema import parse_project_schema_version
from lib.script.script_skeleton import SKELETONS
from lib.speech.speech_artifact_provenance import (
    RenditionVariant,
    SelectedMediaEvidence,
    build_legacy_transition_presentation_basis,
    build_presentation_basis,
)
from lib.speech.speech_presentation import presentation_artifact_paths

TARGET_SCHEMA_VERSION = 16

#: 剧本条目上退役的转场字段名。历史事实，写死在这一步。
_TRANSITION_FIELD = "transition_to_next"


def _load_object(path: Path) -> dict[str, Any] | None:
    """读一个 JSON 对象；缺失、损坏或非对象都返回 None（不在本步修复）。"""

    try:
        parsed = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# 子步：转场移出脚本
# ---------------------------------------------------------------------------


def _bound_scripts(project_dir: Path, project: Mapping[str, Any]) -> list[Path]:
    raw_episodes = project.get("episodes")
    scripts: list[Path] = []
    for entry in raw_episodes if isinstance(raw_episodes, list) else []:
        script_file = entry.get("script_file") if isinstance(entry, Mapping) else None
        if not isinstance(script_file, str) or not script_file:
            continue
        path = try_safe_join(project_dir, script_file)
        if path is not None and path.is_file() and path not in scripts:
            scripts.append(path)
    return scripts


def _drop_script_transitions(project_dir: Path, project: Mapping[str, Any]) -> None:
    for path in _bound_scripts(project_dir, project):
        script = _load_object(path)
        if script is None:
            continue
        changed = False
        for skeleton in SKELETONS:
            items = script.get(skeleton)
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and _TRANSITION_FIELD in item:
                    del item[_TRANSITION_FIELD]
                    changed = True
        if changed:
            ensure_versioned_backup(path, TARGET_SCHEMA_VERSION - 1)
            atomic_write_json(path, script)


@dataclass(frozen=True, slots=True)
class _PresentationRewrite:
    path: Path
    key: ArtifactKey
    #: 文件改写前记录的旧依据登记与改写后的当前依据登记。
    legacy: ArtifactManifestEntry
    current: ArtifactManifestEntry
    content: dict[str, Any]


def _media_evidence(raw: object) -> SelectedMediaEvidence:
    if not isinstance(raw, Mapping):
        raise ValueError("presentation media must be an object")
    content_digest = raw.get("content_digest")
    duration = raw.get("actual_duration_seconds")
    if not isinstance(content_digest, str) or not isinstance(duration, int | float):
        raise ValueError("presentation media evidence is malformed")
    return SelectedMediaEvidence(
        basis=ArtifactBasisDescriptor.from_dict(raw.get("basis")),
        content_digest=content_digest,
        actual_duration_seconds=duration,
    )


def _rendition_variant(raw: object) -> RenditionVariant | None:
    if raw == "post_production":
        return "post_production"
    if raw == "use_tts":
        return "use_tts"
    return None


def _legacy_presentation(project_dir: Path, path: Path) -> _PresentationRewrite | None:
    """算出一份带转场的呈现模型文件的改写；已无转场或与自身记录的旧依据对不上时返回 None。"""

    presentation = _load_object(path)
    if presentation is None or _TRANSITION_FIELD not in presentation:
        return None
    transition = presentation[_TRANSITION_FIELD]
    episode = presentation.get("episode")
    unit_id = presentation.get("unit_id")
    variant = _rendition_variant(presentation.get("variant"))
    video = presentation.get("video")
    raw_audio = presentation.get("narration_audio")
    if type(episode) is not int or not isinstance(unit_id, str) or variant is None or not isinstance(video, Mapping):
        return None
    provider_audio_enabled = video.get("audio_enabled")
    if not isinstance(provider_audio_enabled, bool):
        return None
    try:
        artifact_path = path.relative_to(project_dir).as_posix()
        if presentation_artifact_paths(episode, unit_id, variant)[1] != artifact_path:
            return None
        current = build_presentation_basis(
            variant=variant,
            video=_media_evidence(video),
            subtitle=ArtifactBasisDescriptor.from_dict(presentation.get("subtitle_basis")),
            narration_audio=_media_evidence(raw_audio) if raw_audio is not None else None,
            provider_audio_enabled=provider_audio_enabled,
        )
        legacy = build_legacy_transition_presentation_basis(current, transition)
    except (TypeError, ValueError):
        return None
    if presentation.get("presentation_basis") != ArtifactBasisDescriptor.from_basis(legacy).to_dict():
        return None
    content = {key: value for key, value in presentation.items() if key != _TRANSITION_FIELD}
    content["presentation_basis"] = ArtifactBasisDescriptor.from_basis(current).to_dict()
    return _PresentationRewrite(
        path=path,
        key=ArtifactKey.episode_presentation(episode, unit_id, variant),
        legacy=ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=legacy.digest),
        current=ArtifactManifestEntry(artifact_path=artifact_path, basis_digest=current.digest),
        content=content,
    )


def _rebase_presentations(project_dir: Path) -> None:
    rewrites = [
        rewrite
        for path in sorted((project_dir / "presentations").glob("episode_*/*.json"))
        if path.is_file() and (rewrite := _legacy_presentation(project_dir, path)) is not None
    ]
    if not rewrites:
        return
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    expected = {rewrite.key: rewrite.legacy for rewrite in rewrites if stored.get(rewrite.key) == rewrite.legacy}
    if expected:
        ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
        replacements = {rewrite.key: rewrite.current for rewrite in rewrites if rewrite.key in expected}
        if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=replacements):
            raise RuntimeError("artifact manifest changed while rebasing presentation entries")
    for rewrite in rewrites:
        atomic_write_json(rewrite.path, rewrite.content)


def _move_transitions_out_of_scripts(project_dir: Path, project: Mapping[str, Any]) -> None:
    _drop_script_transitions(project_dir, project)
    _rebase_presentations(project_dir)


# ---------------------------------------------------------------------------


def migrate_v15_to_v16(project_dir: Path) -> ArtifactBackfillOutcome | None:
    """v15→v16 文件级迁移。"""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return None
    project = json.loads(project_file.read_bytes())
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
        return None

    with project_metadata_lock(project_dir):
        _move_transitions_out_of_scripts(project_dir, project)
        migrated_project = {**project, "schema_version": TARGET_SCHEMA_VERSION}
        target = TargetStatePlanner(project_dir, project_bytes=json.dumps(migrated_project).encode()).plan()
        atomic_write_json(project_file, migrated_project)
        return ArtifactBackfillOutcome.from_entries(
            ProjectArtifactManifestAdapter(project_dir).snapshot_entries(),
            target.skipped,
            preserve_previous_skips=True,
        )


__all__ = ["TARGET_SCHEMA_VERSION", "migrate_v15_to_v16"]
