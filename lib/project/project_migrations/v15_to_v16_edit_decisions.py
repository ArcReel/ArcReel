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

**旁白交付方式成为项目配置（ADR 0089）**

- 产物清单登记了旁白配音、且至少一条登记的选中音频版本记录带完整 TTS 设置的项目判为 TTS 配音，
  ``project.json`` 写入这些记录里 ``created_at`` 最新的一条所用的模型、音色与语速作为 TTS 快照。
- 其余项目判为后期配音，既有的音频后端、音色与语速字段原样保留。
- 只写 ``project.json``，随本步最后一次写入落盘；备份由 runner 负责。选中版本记录没有 TTS 设置的
  旧音频本来就不被登记，判定不改变它们的处置。

**视频时效不再以旁白时长为输入**

- 按旧口径与新口径各规划一次目标态，只改写改前时新且目标已变的视频登记；本就过期的登记保留。
  改前目标态在本步任何写入之前规划，兼作只读预检；视频的依据不含转场，先于转场子步规划不改变结果。
- 选中版本额外记录新的时效时长基准，实际付费档位与执行请求摘要不变。版本记录先于清单落盘，
  清单先于项目版本；中断后重跑仍能认出旧登记与已改写的版本记录。

**上传的分镜图与视频按上传字节登记（ADR 0062 修订）**

- 选中版本是与产物字节一致的手动上传的分镜图与视频，按改后目标态登记为只由上传字节决定的依据：
  此前不登记的上传视频、被删去登记的上传分镜图补登，按生成输入登记的上传分镜图改写。
- 只写清单，先于项目版本落盘；重跑时已登记的条目与目标一致而被跳过。

**集 ID 与播出顺序分离（ADR 0096）**

- ``project.json`` 写入项目历史最高号（``lib.episode.episode_ids``），取账本最大集 ID、项目目录里
  仍带集 ID 的名字（剧本、草稿目录、源文留底、媒体名里的 ``E{N}`` 前缀、呈现与字幕目录）、产物
  清单里的集 ID，以及 runner 注入的任务与调用记录查询结果中的最大值。账本现有顺序已按集 ID 升序，
  即为播出顺序，不重排。
- 下集大纲改取播出顺序中紧接的那一集，且没有规划数据时给标题（``episode_outline_context``）。
  它是脚本规划依据的输入：与视频时效同法，改前改后各规划一次，只改写改前时新且目标已变的脚本规划
  登记。脚本规划没有版本记录，只改清单。

除上传产物的补登外，本步只改写既有登记、不增删；迁移结果按改写后完整目标态的跳过项与实际清单计数生成。
它不解决此前的跳过原因，runner 合并链上更早一步或已有迁移报告的跳过项。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import assert_artifact_target_state_plan_unchanged
from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifacts.artifact_planner import ArtifactTargetStatePlan, TargetStatePlanner
from lib.artifacts.artifact_version_provenance import (
    VIDEO_CURRENCY_DURATION_FIELD,
    parse_typed_audio_settings,
    parse_typed_media_version_target,
)
from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import selected_manual_upload_snapshot
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.visual_artifact_provenance import (
    build_uploaded_storyboard_basis,
    build_uploaded_video_basis,
    visual_file_digest,
)
from lib.episode.episode_ids import episode_ids_on_disk, raise_episode_id_high_water
from lib.infra.json_io import atomic_write_json
from lib.infra.path_safety import try_safe_join
from lib.project.project_migration_report import ArtifactBackfillOutcome
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.project.project_schema import parse_project_schema_version
from lib.script.script_skeleton import SKELETONS
from lib.speech.narration_config import (
    NARRATION_DELIVERY_FIELD,
    POST_PRODUCTION,
    TTS_SPEED_FIELD,
    USE_TTS,
    TtsSynthesisSettings,
    tts_snapshot_fields,
)
from lib.speech.speech_artifact_provenance import (
    RenditionVariant,
    SelectedMediaEvidence,
    build_legacy_transition_presentation_basis,
    build_presentation_basis,
    build_video_duration_basis,
)
from lib.speech.speech_presentation import presentation_artifact_paths

TARGET_SCHEMA_VERSION = 16

#: 按项目名查任务与调用记录里出现过的最大集 ID（没有记录为 0）。数据库在项目目录之外，由应用装配处注入。
RecordedEpisodeIds = Callable[[str], int]

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
# 子步：视频时效与旁白下限解耦
# ---------------------------------------------------------------------------


def _plan_before_rewrite(project_dir: Path, project: Mapping[str, Any]) -> ArtifactTargetStatePlan:
    """改前目标态。在本步任何写入之前规划，兼作只读预检：输入损坏时项目目录不被改动。"""

    project_bytes = (project_dir / "project.json").read_bytes()
    plan = TargetStatePlanner(
        project_dir,
        project_bytes=json.dumps(project).encode(),
        allow_stale_formal_targets=True,
        legacy_audio_entries=ProjectArtifactManifestAdapter(project_dir).snapshot_entries(),
    ).plan()
    assert_artifact_target_state_plan_unchanged(project_dir, plan, expected_project_bytes=project_bytes)
    return plan


def _plan_after_rewrite(project_dir: Path, migrated: Mapping[str, Any]) -> ArtifactTargetStatePlan:
    project_bytes = (project_dir / "project.json").read_bytes()
    after = TargetStatePlanner(
        project_dir, project_bytes=json.dumps(migrated).encode(), allow_stale_formal_targets=True
    ).plan()
    assert_artifact_target_state_plan_unchanged(project_dir, after, expected_project_bytes=project_bytes)
    return after


def _rebase_video_duration_entries(
    project_dir: Path, before: ArtifactTargetStatePlan, after: ArtifactTargetStatePlan
) -> None:
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    versions_path = project_dir / "versions" / "versions.json"
    versions = _load_object(versions_path) or {}
    replacements: dict[ArtifactKey, ArtifactManifestEntry] = {}
    changed = False
    for key, target in after.entries.items():
        if (
            key.kind is not ArtifactKind.EPISODE_VIDEO
            or key not in before.entries
            or stored.get(key) != before.entries[key]
            or before.entries[key] == target
        ):
            continue
        resource_id = str(key.components[-1])
        resource_type = "reference_videos" if target.artifact_path.startswith("reference_videos/") else "videos"
        record = _selected_version_record(versions, resource_type, resource_id)
        if record is None:
            continue
        try:
            frozen = parse_typed_media_version_target(resource_type, record)
            facts = VideoArtifactCurrencyFacts.from_dict(record.get("artifact_video_currency"))
        except (TypeError, ValueError):
            continue
        if frozen.basis.digest == target.basis_digest:
            replacements[key] = target
            continue
        if frozen.basis.digest != stored[key].basis_digest:
            continue
        for tier in facts.duration_tiers:
            basis = compose_video_artifact_basis(
                visual=facts.visual_basis, speech=facts.speech_basis, duration=build_video_duration_basis(tier)
            )
            if basis.digest == target.basis_digest:
                record[VIDEO_CURRENCY_DURATION_FIELD] = tier
                replacements[key] = target
                changed = True
                break
    if not replacements:
        return
    if changed:
        ensure_versioned_backup(versions_path, TARGET_SCHEMA_VERSION - 1)
        atomic_write_json(versions_path, versions)
    ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
    if not adapter.replace_entries_if_matches_atomically(
        expected={key: stored[key] for key in replacements}, replacements=replacements
    ):
        raise RuntimeError("artifact manifest changed while rebasing video duration entries")


# ---------------------------------------------------------------------------
# 子步：上传的分镜图与视频按上传字节登记
# ---------------------------------------------------------------------------

#: 可上传、且由选中的上传版本决定依据的产物种类与它们的版本资源类型。
_UPLOAD_RESOURCE_TYPES: Mapping[ArtifactKind, tuple[str, ...]] = {
    ArtifactKind.EPISODE_STORYBOARD: ("storyboards",),
    ArtifactKind.EPISODE_VIDEO: ("videos", "reference_videos"),
}


def _uploaded_basis_digest(project_dir: Path, kind: ArtifactKind, artifact_path: str) -> str | None:
    path = try_safe_join(project_dir, artifact_path)
    if path is None or not path.is_file():
        return None
    content_digest = visual_file_digest(path)
    if kind is ArtifactKind.EPISODE_STORYBOARD:
        return build_uploaded_storyboard_basis(content_digest=content_digest).digest
    return build_uploaded_video_basis(content_digest=content_digest).digest


def _register_uploaded_media(project_dir: Path, migrated: Mapping[str, Any]) -> None:
    project_bytes = (project_dir / "project.json").read_bytes()
    target = TargetStatePlanner(project_dir, project_bytes=json.dumps(migrated).encode()).plan()
    assert_artifact_target_state_plan_unchanged(project_dir, target, expected_project_bytes=project_bytes)
    versions = _load_object(project_dir / "versions" / "versions.json") or {}
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    replacements: dict[ArtifactKey, ArtifactManifestEntry] = {}
    for key, entry in target.entries.items():
        resource_types = _UPLOAD_RESOURCE_TYPES.get(key.kind)
        if resource_types is None or stored.get(key) == entry:
            continue
        resource_id = str(key.components[-1])
        if not any(
            selected_manual_upload_snapshot(_history(versions, resource_type, resource_id), resource_type)
            for resource_type in resource_types
        ):
            continue
        if _uploaded_basis_digest(project_dir, key.kind, entry.artifact_path) == entry.basis_digest:
            replacements[key] = entry
    if not replacements:
        return
    ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
    if not adapter.replace_entries_if_matches_atomically(
        expected={key: stored.get(key) for key in replacements}, replacements=replacements
    ):
        raise RuntimeError("artifact manifest changed while registering uploaded media")


def _history(versions: Mapping[str, Any], resource_type: str, resource_id: str) -> object:
    bucket = versions.get(resource_type)
    return bucket.get(resource_id) if isinstance(bucket, Mapping) else None


# ---------------------------------------------------------------------------
# 子步：集 ID 与播出顺序分离
# ---------------------------------------------------------------------------


def _with_episode_id_high_water(
    project_dir: Path, project: Mapping[str, Any], recorded_episode_ids: RecordedEpisodeIds | None
) -> dict[str, Any]:
    migrated = dict(project)
    manifest_ids = [
        key.episode_number
        for key in ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
        if key.episode_number is not None
    ]
    recorded = recorded_episode_ids(project_dir.name) if recorded_episode_ids is not None else 0
    raise_episode_id_high_water(migrated, *episode_ids_on_disk(project_dir), *manifest_ids, recorded)
    return migrated


def _rebase_script_plan_entries(
    project_dir: Path, before: ArtifactTargetStatePlan, after: ArtifactTargetStatePlan
) -> None:
    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    replacements = {
        key: target
        for key, target in after.entries.items()
        if key.kind is ArtifactKind.EPISODE_SCRIPT_PLAN
        and key in before.entries
        and stored.get(key) == before.entries[key]
        and before.entries[key] != target
    }
    if not replacements:
        return
    ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
    if not adapter.replace_entries_if_matches_atomically(
        expected={key: stored[key] for key in replacements}, replacements=replacements
    ):
        raise RuntimeError("artifact manifest changed while rebasing script plan entries")


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
# 子步：旁白交付方式成为项目配置
# ---------------------------------------------------------------------------


def _selected_version_record(
    versions: Mapping[str, Any], resource_type: str, resource_id: str
) -> dict[str, Any] | None:
    bucket = versions.get(resource_type)
    resource = bucket.get(resource_id) if isinstance(bucket, Mapping) else None
    if not isinstance(resource, Mapping):
        return None
    selected_version = resource.get("current_version")
    records = resource.get("versions")
    if type(selected_version) is not int or not isinstance(records, list):
        return None
    selected = [record for record in records if isinstance(record, dict) and record.get("version") == selected_version]
    return selected[0] if len(selected) == 1 else None


def _latest_registered_tts_settings(project_dir: Path) -> TtsSynthesisSettings | None:
    """清单登记的旁白配音里，选中版本记录 ``created_at`` 最新的那条所用的 TTS 设置。"""

    audio_keys = [
        key
        for key in ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
        if key.kind is ArtifactKind.EPISODE_AUDIO
    ]
    if not audio_keys:
        return None
    versions = _load_object(project_dir / "versions" / "versions.json") or {}
    candidates: list[tuple[str, int, str, TtsSynthesisSettings]] = []
    for key in audio_keys:
        episode, resource_id = key.components
        if type(episode) is not int or not isinstance(resource_id, str):
            continue
        record = _selected_version_record(versions, "audio", resource_id)
        if record is None:
            continue
        try:
            target = parse_typed_media_version_target("audio", record)
            settings = parse_typed_audio_settings(record)
        except (TypeError, ValueError):
            continue
        if target.episode == episode:
            candidates.append((target.created_at or "", episode, resource_id, settings))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[:3])[3]


def _narration_delivery_fields(project_dir: Path, project: Mapping[str, Any]) -> dict[str, Any]:
    """迁移后的 ``project.json``：写入旁白交付方式，TTS 配音项目换上 TTS 快照。"""

    settings = _latest_registered_tts_settings(project_dir)
    if settings is None:
        return {**project, NARRATION_DELIVERY_FIELD: POST_PRODUCTION}
    migrated = {**project, NARRATION_DELIVERY_FIELD: USE_TTS, **tts_snapshot_fields(settings)}
    if settings.speed is None:
        del migrated[TTS_SPEED_FIELD]
    return migrated


# ---------------------------------------------------------------------------


def migrate_v15_to_v16(
    project_dir: Path, *, recorded_episode_ids: RecordedEpisodeIds | None = None
) -> ArtifactBackfillOutcome | None:
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
        before = _plan_before_rewrite(project_dir, project)
        _move_transitions_out_of_scripts(project_dir, project)
        migrated_project = {
            **_with_episode_id_high_water(
                project_dir, _narration_delivery_fields(project_dir, project), recorded_episode_ids
            ),
            "schema_version": TARGET_SCHEMA_VERSION,
        }
        after = _plan_after_rewrite(project_dir, migrated_project)
        _rebase_video_duration_entries(project_dir, before, after)
        _rebase_script_plan_entries(project_dir, before, after)
        _register_uploaded_media(project_dir, migrated_project)
        target = TargetStatePlanner(project_dir, project_bytes=json.dumps(migrated_project).encode()).plan()
        atomic_write_json(project_file, migrated_project)
        return ArtifactBackfillOutcome.from_entries(
            ProjectArtifactManifestAdapter(project_dir).snapshot_entries(),
            target.skipped,
            preserve_previous_skips=True,
        )


__all__ = ["TARGET_SCHEMA_VERSION", "RecordedEpisodeIds", "migrate_v15_to_v16"]
