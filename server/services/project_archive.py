from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from lib.artifact_activation import (
    ensure_imported_artifact_target_state,
    snapshot_preserved_artifact_manifest,
)
from lib.artifact_manifest import (
    ArtifactKey,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
    decode_artifact_manifest_payload,
    encode_artifact_manifest_payload,
)
from lib.asset_types import asset_name_comparison_key, normalize_asset_name
from lib.config.resolver import resolve_raw_supported_durations
from lib.content_digest import digest_stream, sha256_file
from lib.data_validator import DataValidator
from lib.episode_ledger import parse_positive_episode_num
from lib.formal_write import project_metadata_lock
from lib.json_io import load_json
from lib.path_safety import PathTraversalError, safe_join, try_safe_join
from lib.project_change_hints import emit_project_change_hint
from lib.project_manager import ProjectManager
from lib.project_migrations.runner import migrate_project_dir
from lib.project_migrations.v1_to_v2_normalize_providers import migrate_project_dict as normalize_legacy_providers
from lib.project_schema import project_schema_is_current
from lib.reference_video.draft_validation import dialogue_speakers
from lib.reference_video.duration_migration import migrate_unit_durations
from lib.reference_video.text_parser import extract_mentions
from lib.resource_paths import resource_extension, resource_relative_path
from lib.script_skeleton import SKELETONS, resolve_declared_kind, resolve_kind_items
from lib.source_loader.migration import migrate_project_source_encoding
from lib.validation_messages import MessageRef, ValidationMessage, ValidationResult

logger = logging.getLogger(__name__)

ARCHIVE_MANIFEST_NAME = "arcreel-export.json"
ARCHIVE_FORMAT_VERSION = 2
ARCHIVE_SCRIPT_SCHEMA_VERSION = 2
DEFAULT_IMPORT_FILENAME = "imported-project.zip"
_ARTIFACT_ACTIVATION_ERRORS = (ArtifactManifestError, OSError, UnicodeError, ValueError)
_EXPORT_SNAPSHOT_ATTEMPTS = 3


def _resolve_existing_asset(name: str, candidates: set[str]) -> str:
    """把剧本里的资产名解析为 *candidates* 中等价的真实名字；未命中原样返回。

    导入的剧本与 project.json 可以各自是 NFC/NFD 中的任一形态（登记闸口落 NFC，
    存量归档不迁移），按 ``lib.asset_types`` 的比对坐标系解析后才判得准成员关系：
    否则修复期会给已登记的资产补一条视觉同名的占位定义，或对其报 blocking 缺失。
    """
    if name in candidates:
        return name
    canonical = normalize_asset_name(name)
    for candidate in candidates:
        if normalize_asset_name(candidate) == canonical:
            return candidate
    return name


@dataclass(frozen=True)
class ArchiveMember:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]
    is_dir: bool


@dataclass(frozen=True)
class ArchiveDiagnostic:
    """一条归档诊断。``message`` 是 locale-neutral 的结构化消息，渲染发生在消费边界。"""

    code: str
    message: ValidationMessage
    location: str | None = None

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "message": self.message.render(translate),
        }
        if self.location:
            payload["location"] = self.location
        return payload


@dataclass
class ArchiveDiagnostics:
    blocking: list[ArchiveDiagnostic] = field(default_factory=list)
    auto_fixed: list[ArchiveDiagnostic] = field(default_factory=list)
    warnings: list[ArchiveDiagnostic] = field(default_factory=list)
    _seen: set[tuple[str, str, str, str | None]] = field(
        default_factory=set,
        init=False,
        repr=False,
    )

    def add(
        self,
        bucket: str,
        code: str,
        message: ValidationMessage,
        *,
        location: str | None = None,
    ) -> None:
        # 判重按默认语言渲染文本比对：同 key 不同 params 是不同诊断，须各自保留；
        # params 可能含列表 / 集合等不可哈希值，渲染结果是稳定且可哈希的等价指纹。
        key = (bucket, code, message.render(), location)
        if key in self._seen:
            return
        self._seen.add(key)
        getattr(self, bucket).append(
            ArchiveDiagnostic(
                code=code,
                message=message,
                location=location,
            )
        )

    def extend_validation(self, validation: ValidationResult) -> None:
        for error in validation.error_messages:
            self.add("blocking", "validation_error", error)
        for warning in validation.warning_messages:
            self.add("warnings", "validation_warning", warning)

    def to_export_payload(self, translate: Callable[..., str] | None = None) -> dict[str, list[dict[str, Any]]]:
        return {
            "blocking": [item.to_payload(translate) for item in self.blocking],
            "auto_fixed": [item.to_payload(translate) for item in self.auto_fixed],
            "warnings": [item.to_payload(translate) for item in self.warnings],
        }

    def to_import_success_payload(self, translate: Callable[..., str] | None = None) -> dict[str, list[dict[str, Any]]]:
        return {
            "auto_fixed": [item.to_payload(translate) for item in self.auto_fixed],
            "warnings": [item.to_payload(translate) for item in self.warnings],
        }

    def to_import_error_payload(self, translate: Callable[..., str] | None = None) -> dict[str, list[dict[str, Any]]]:
        return {
            "blocking": [item.to_payload(translate) for item in self.blocking],
            "auto_fixable": [item.to_payload(translate) for item in self.auto_fixed],
            "warnings": [item.to_payload(translate) for item in self.warnings],
        }

    def blocking_messages(self) -> list[ValidationMessage]:
        return [item.message for item in self.blocking]

    def warning_messages(self) -> list[ValidationMessage]:
        return [item.message for item in self.warnings]


@dataclass(frozen=True)
class ProjectImportResult:
    project_name: str
    project: dict[str, Any]
    warnings: list[ValidationMessage]
    conflict_resolution: str
    diagnostics: dict[str, list[dict[str, Any]]]


class ProjectArchiveValidationError(ValueError):
    """归档导入/导出的用户可见失败。``detail`` / ``errors`` / ``warnings`` 均为结构化消息，
    由 router 按请求语言渲染。"""

    def __init__(
        self,
        detail: ValidationMessage,
        *,
        status_code: int = 400,
        errors: list[ValidationMessage] | None = None,
        warnings: list[ValidationMessage] | None = None,
        diagnostics: ArchiveDiagnostics | None = None,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(detail.render())
        self.detail = detail
        self.status_code = status_code
        self.errors = errors or []
        self.warnings = warnings or []
        self.diagnostics = diagnostics
        self.extra = dict(extra or {})

    def render_errors(self, translate: Callable[..., str] | None = None) -> list[str]:
        return [error.render(translate) for error in self.errors]

    def render_warnings(self, translate: Callable[..., str] | None = None) -> list[str]:
        return [warning.render(translate) for warning in self.warnings]

    def diagnostics_payload(self, translate: Callable[..., str] | None = None) -> dict[str, list[dict[str, Any]]]:
        """导入失败响应里的诊断三桶；无诊断来源时给空桶，保持响应形状恒定。"""
        if self.diagnostics is None:
            return {"blocking": [], "auto_fixable": [], "warnings": []}
        return self.diagnostics.to_import_error_payload(translate)


class ProjectArchiveService:
    _VERSION_HISTORY_DIRS = frozenset(
        {
            "storyboards",
            "videos",
            "audio",
            "characters",
            "scenes",
            "props",
            "reference_videos",
        }
    )
    _ROOT_VISIBLE_ENTRIES = frozenset(DataValidator.ALLOWED_ROOT_ENTRIES)
    _TYPED_VERSION_HISTORY_DIRS = frozenset({"audio", "videos", "reference_videos"})
    _AGENT_RUNTIME_EXCLUDES = frozenset({".claude", "CLAUDE.md"})
    _PLACEHOLDER_CHARACTER_DESCRIPTION = "Imported placeholder character"

    def __init__(self, project_manager: ProjectManager):
        self.project_manager = project_manager
        self.validator = DataValidator(projects_root=str(project_manager.projects_root))

    def get_export_diagnostics(
        self,
        project_name: str,
        *,
        scope: str = "full",
        translate: Callable[..., str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        self._validate_scope(scope)
        if not self.project_manager.project_exists(project_name):
            raise FileNotFoundError(f"项目 '{project_name}' 不存在或未初始化")

        temp_dir, _, _, diagnostics = self._prepare_export_snapshot(project_name, scope=scope)
        temp_dir.cleanup()
        return diagnostics.to_export_payload(translate)

    def export_project(
        self,
        project_name: str,
        *,
        scope: str = "full",
    ) -> tuple[Path, str]:
        self._validate_scope(scope)
        if not self.project_manager.project_exists(project_name):
            raise FileNotFoundError(f"项目 '{project_name}' 不存在或未初始化")

        fd, archive_path_str = tempfile.mkstemp(
            prefix=f"{project_name}-",
            suffix=".zip",
        )
        os.close(fd)
        archive_path = Path(archive_path_str)

        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            temp_dir, snapshot_dir, manifest, _ = self._prepare_export_snapshot(project_name, scope=scope)
            with zipfile.ZipFile(
                archive_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                self._write_directory_entry(archive, (project_name,))
                archive.writestr(
                    f"{project_name}/{ARCHIVE_MANIFEST_NAME}",
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                self._write_snapshot_members(
                    archive,
                    snapshot_dir,
                    project_name=project_name,
                    scope=scope,
                )
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

        download_name = f"{project_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        return archive_path, download_name

    @staticmethod
    def _raise_artifact_activation_validation_error(
        diagnostics: ArchiveDiagnostics,
        cause: Exception,
    ) -> None:
        diagnostics.add(
            "blocking",
            "artifact_activation_failed",
            ValidationMessage("arch_artifact_activation_failed"),
        )
        raise ProjectArchiveValidationError(
            ValidationMessage("arch_import_validation_failed"),
            errors=diagnostics.blocking_messages(),
            warnings=diagnostics.warning_messages(),
            diagnostics=diagnostics,
        ) from cause

    def import_project_archive(
        self,
        archive_path: Path,
        *,
        uploaded_filename: str | None = None,
        conflict_policy: str = "prompt",
        translate: Callable[..., str] | None = None,
    ) -> ProjectImportResult:
        if conflict_policy not in {"prompt", "rename", "overwrite"}:
            raise ProjectArchiveValidationError(
                ValidationMessage("arch_invalid_conflict_policy"),
                errors=[ValidationMessage("arch_conflict_policy_unsupported", {"value": conflict_policy})],
            )

        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = self._scan_archive_members(archive)
                root_parts, manifest = self._locate_project_root(archive, members)

                with tempfile.TemporaryDirectory(prefix="arcreel-import-") as temp_dir:
                    staging_dir = Path(temp_dir) / "project"
                    staging_dir.mkdir(parents=True, exist_ok=True)

                    self._extract_archive_root(
                        archive,
                        members,
                        root_parts,
                        staging_dir,
                    )

                    diagnostics = self._repair_project_tree(staging_dir)
                    # 在校验前对 staging 副本跑完整迁移链（归一化 legacy provider 名 / 拆分 image_backend /
                    # 生成路线重编码）：启动期 run_project_migrations 只覆盖启动时已存在的项目，启动后导入的
                    # 旧归档需在此补跑，否则解析链不再读 legacy 字段会让该项目静默回退全局默认，且校验器按
                    # 最新 schema 形态断言（如 generation_mode 必填二值），未迁移的旧归档会被误拒。放在安装
                    # **前** → 迁移若抛错，staging 临时目录随 TemporaryDirectory 丢弃、不会留下半迁移的脏项目
                    # 目录，无需回滚已落盘安装。
                    # 编码迁移先于 schema 迁移：源文一律先归到 UTF-8，之后所有按 UTF-8 读源文的
                    # 链路才有统一的输入。转换失败 = 文件本身不可解码（任何路径都读不出），浮成
                    # 导入 warning 而非中止——局部损坏文件不应阻断整个项目导入。
                    encoding_summary = migrate_project_source_encoding(staging_dir)
                    for failed_name in encoding_summary.failed:
                        diagnostics.add(
                            "warnings",
                            "source_encoding_unconverted",
                            ValidationMessage("arch_source_encoding_unconverted", {"name": failed_name}),
                        )
                    try:
                        migrate_project_dir(staging_dir)
                    except _ARTIFACT_ACTIVATION_ERRORS as exc:
                        stalled_project = self._load_json_file(staging_dir / self.project_manager.PROJECT_FILE)
                        if stalled_project is not None and stalled_project.get("schema_version") == 7:
                            self._raise_artifact_activation_validation_error(diagnostics, exc)
                        raise
                    diagnostics.extend_validation(self.validator.validate_project_tree(staging_dir))
                    if diagnostics.blocking:
                        raise ProjectArchiveValidationError(
                            ValidationMessage("arch_import_validation_failed"),
                            errors=diagnostics.blocking_messages(),
                            warnings=diagnostics.warning_messages(),
                            diagnostics=diagnostics,
                        )
                    # Artifact Manifest 是 hidden sidecar，不进入归档成员。官方导出把完整
                    # claim snapshot 放进 visible archive envelope；旧的手工归档没有该字段，
                    # 仍走 self-proving reconstruction。两条路径均在 staging 一次性提交。
                    try:
                        preserved_manifest = (
                            decode_artifact_manifest_payload(manifest["artifact_manifest"])
                            if isinstance(manifest, dict) and "artifact_manifest" in manifest
                            else None
                        )
                        ensure_imported_artifact_target_state(
                            staging_dir,
                            preserved_manifest=preserved_manifest,
                        )
                    except _ARTIFACT_ACTIVATION_ERRORS as exc:
                        self._raise_artifact_activation_validation_error(diagnostics, exc)

                    project = self._load_project_file(staging_dir / self.project_manager.PROJECT_FILE)
                    target_name = self._resolve_target_project_name(
                        project,
                        manifest=manifest,
                        root_parts=root_parts,
                        uploaded_filename=uploaded_filename,
                    )
                    target_name, conflict_resolution = self._resolve_conflict(
                        target_name,
                        project_title=str(project.get("title") or "").strip(),
                        conflict_policy=conflict_policy,
                    )

                    self._ensure_standard_subdirs(staging_dir)

                    self._install_project_dir(
                        staging_dir,
                        target_name,
                        overwrite=(conflict_policy == "overwrite"),
                    )

                    imported_project = self.project_manager.load_project(target_name)
                    emit_project_change_hint(
                        target_name,
                        source="webui",
                        changed_paths=[self.project_manager.PROJECT_FILE],
                    )

                    return ProjectImportResult(
                        project_name=target_name,
                        project=imported_project,
                        warnings=diagnostics.warning_messages(),
                        conflict_resolution=conflict_resolution,
                        diagnostics=diagnostics.to_import_success_payload(translate),
                    )
        except zipfile.BadZipFile as exc:
            raise ProjectArchiveValidationError(
                ValidationMessage("arch_not_a_zip"),
                errors=[ValidationMessage.literal(str(exc))],
            ) from exc

    def _prepare_export_snapshot(
        self,
        project_name: str,
        *,
        scope: str,
    ) -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, Any], ArchiveDiagnostics]:
        source_dir = self.project_manager.get_project_path(project_name)
        temp_dir = tempfile.TemporaryDirectory(prefix="arcreel-export-")
        snapshot_dir = Path(temp_dir.name) / project_name
        source_manifest_entries = self._capture_stable_visible_tree(source_dir, snapshot_dir)

        diagnostics = self._repair_project_tree(snapshot_dir)
        diagnostics.extend_validation(self.validator.validate_project_tree(snapshot_dir))

        # 从源目录收集非标准顶层条目，记录到诊断中（即使已被过滤不导出）
        excluded_entries = self._collect_pass_through_entries(source_dir)
        for entry in excluded_entries:
            diagnostics.add(
                "warnings",
                "non_standard_entry_excluded",
                ValidationMessage("arch_non_standard_entry_excluded", {"entry": entry}),
                location=entry,
            )

        snapshot_project = self._load_json_file(snapshot_dir / self.project_manager.PROJECT_FILE)
        artifact_manifest = None
        if isinstance(snapshot_project, dict) and project_schema_is_current(snapshot_project):
            if source_manifest_entries is None:
                raise ArtifactManifestError("archive snapshot has no matching Artifact Manifest state")
            artifact_manifest = encode_artifact_manifest_payload(
                snapshot_preserved_artifact_manifest(
                    snapshot_dir,
                    source_manifest_entries,
                )
            )
        manifest = self._build_archive_manifest(
            project_name,
            snapshot_project,
            scope=scope,
            # 归档内的诊断快照是随包分发的数据，按默认语言渲染——导入方与导出方的语言未必相同，
            # 面向请求的渲染只发生在 router 边界。
            diagnostics=diagnostics.to_export_payload(),
            pass_through_entries=excluded_entries,
            artifact_manifest=artifact_manifest,
        )
        return temp_dir, snapshot_dir, manifest, diagnostics

    def _build_archive_manifest(
        self,
        project_name: str,
        project: dict[str, Any] | None,
        *,
        scope: str,
        diagnostics: dict[str, Any],
        pass_through_entries: list[str],
        artifact_manifest: dict[str, object] | None,
    ) -> dict[str, Any]:
        project_payload = project or {}
        payload = {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "script_schema_version": ARCHIVE_SCRIPT_SCHEMA_VERSION,
            "project_name": project_name,
            "project_title": project_payload.get("title", project_name),
            "content_mode": project_payload.get("content_mode", ""),
            "scope": scope,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "export_diagnostics": diagnostics,
            "pass_through_entries": pass_through_entries,
        }
        if artifact_manifest is not None:
            payload["artifact_manifest"] = artifact_manifest
        return payload

    @staticmethod
    def _write_directory_entry(
        archive: zipfile.ZipFile,
        parts: tuple[str, ...],
    ) -> None:
        dirname = "/".join(parts).rstrip("/") + "/"
        info = zipfile.ZipInfo(dirname)
        info.external_attr = (0o40755 & 0xFFFF) << 16
        archive.writestr(info, b"")

    def _write_snapshot_members(
        self,
        archive: zipfile.ZipFile,
        snapshot_dir: Path,
        *,
        project_name: str,
        scope: str,
    ) -> None:
        is_current = scope == "current"
        trimmed_versions: dict[str, Any] | None = None
        retained_version_files: frozenset[str] = frozenset()
        if is_current:
            versions_path = snapshot_dir / "versions" / "versions.json"
            payload = self._load_json_file(versions_path) if versions_path.is_file() else None
            trimmed_versions = self._trim_versions_payload(payload or {})
            retained_version_files = self._selected_typed_version_files(trimmed_versions)

        for current_dir, dirnames, filenames in os.walk(snapshot_dir):
            current_path = Path(current_dir)
            is_root = current_path == snapshot_dir
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not name.startswith(".")
                and not (current_path / name).is_symlink()
                and not (is_root and name in self._AGENT_RUNTIME_EXCLUDES)
            ]

            relative_dir = current_path.relative_to(snapshot_dir)
            if is_current and relative_dir.parts == ("versions",):
                retained_dirs = {PurePosixPath(path).parts[1] for path in retained_version_files}
                dirnames[:] = [
                    name for name in dirnames if name not in self._VERSION_HISTORY_DIRS or name in retained_dirs
                ]
            elif is_current and relative_dir.parts[:1] == ("versions",):
                prefix = relative_dir.as_posix().rstrip("/") + "/"
                dirnames[:] = [
                    name
                    for name in dirnames
                    if any(path.startswith(f"{prefix}{name}/") for path in retained_version_files)
                ]

            visible_files = [
                name
                for name in sorted(filenames)
                if not name.startswith(".")
                and not (current_path / name).is_symlink()
                and not (is_root and name in self._AGENT_RUNTIME_EXCLUDES)
            ]
            if is_current and len(relative_dir.parts) >= 2 and relative_dir.parts[0] == "versions":
                visible_files = [
                    name
                    for name in visible_files
                    if relative_dir.parts[1] not in self._VERSION_HISTORY_DIRS
                    or (relative_dir / name).as_posix() in retained_version_files
                ]

            if relative_dir != Path("."):
                self._write_directory_entry(
                    archive,
                    (project_name, *relative_dir.parts),
                )

            for filename in visible_files:
                source_path = current_path / filename
                archive_name = Path(project_name, relative_dir, filename).as_posix()

                if is_current and relative_dir.parts == ("versions",) and filename == "versions.json":
                    assert trimmed_versions is not None
                    archive.writestr(
                        archive_name,
                        json.dumps(
                            trimmed_versions,
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                    continue

                archive.write(source_path, arcname=archive_name)

    @classmethod
    def _trim_versions_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        trimmed = json.loads(json.dumps(payload))
        for resource_type, resource_type_data in tuple(trimmed.items()):
            # Current-only exports retain canonical non-typed media, not their
            # version-history snapshots.  Their metadata must leave with those
            # omitted files; typed selected snapshots remain because artifact
            # activation uses them as independent provenance evidence.
            if resource_type in cls._VERSION_HISTORY_DIRS and resource_type not in cls._TYPED_VERSION_HISTORY_DIRS:
                del trimmed[resource_type]
                continue
            if not isinstance(resource_type_data, dict):
                continue
            for resource_info in resource_type_data.values():
                if not isinstance(resource_info, dict):
                    continue
                current_ver = resource_info.get("current_version")
                versions_list = resource_info.get("versions", [])
                if current_ver is not None and isinstance(versions_list, list):
                    resource_info["versions"] = [
                        version
                        for version in versions_list
                        if isinstance(version, dict) and version.get("version") == current_ver
                    ]
        return trimmed

    @classmethod
    def _selected_typed_version_files(cls, payload: dict[str, Any]) -> frozenset[str]:
        """Return exact selected typed snapshots required to prove current media."""

        selected: set[str] = set()
        for resource_type in cls._TYPED_VERSION_HISTORY_DIRS:
            resources = payload.get(resource_type)
            if not isinstance(resources, dict):
                continue
            for resource in resources.values():
                if not isinstance(resource, dict):
                    continue
                records = resource.get("versions")
                if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
                    continue
                raw_path = records[0].get("file")
                if not isinstance(raw_path, str) or "\\" in raw_path:
                    continue
                path = PurePosixPath(raw_path)
                if path.is_absolute() or path.parts[:2] != ("versions", resource_type) or len(path.parts) != 3:
                    continue
                if any(part in {"", ".", ".."} for part in path.parts):
                    continue
                selected.add(path.as_posix())
        return frozenset(selected)

    def _capture_stable_visible_tree(
        self,
        source_dir: Path,
        target_dir: Path,
    ) -> dict[ArtifactKey, ArtifactManifestEntry] | None:
        """Copy one visible tree whose bytes and Manifest stayed unchanged.

        Export cannot atomically snapshot a directory with ordinary filesystem
        primitives.  Hold the shared formal-write lock around each attempt, then
        compare complete content signatures and the whole Manifest on both sides
        of the copy.  The comparison still rejects unmanaged filesystem changes
        that do not participate in the formal-write lock.
        """

        last_missing: FileNotFoundError | None = None
        for _attempt in range(_EXPORT_SNAPSHOT_ATTEMPTS):
            with project_metadata_lock(source_dir):
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                try:
                    manifest_before = self._source_manifest_entries(source_dir)
                    copied = self._copy_visible_tree(source_dir, target_dir)
                    source_after = self._visible_tree_signature(source_dir)
                    manifest_after = self._source_manifest_entries(source_dir)
                except FileNotFoundError as exc:
                    last_missing = exc
                    continue
            if manifest_before == manifest_after and source_after == copied:
                return manifest_before
        if target_dir.exists():
            shutil.rmtree(target_dir)
        raise ArtifactManifestError("project changed repeatedly while creating an archive snapshot") from last_missing

    def _source_manifest_entries(
        self,
        source_dir: Path,
    ) -> dict[ArtifactKey, ArtifactManifestEntry] | None:
        """源项目的完整 claim 快照；尚未进入清单体系的项目返回 ``None``。

        这道闸判的是「该项目有没有清单可保全」，不是产物读取口径，与写信封那处同判据。
        未进入体系的项目清单必然为空，而空清单与「这个项目一件产物都没有」在信封里长得
        一模一样：导入端见到信封就按保真路径原样落盘，项目里全部已生成产物会一次判
        missing、要用户重新付费生成。这类项目的归档不带信封，导入侧照常迁移并自证补录。
        """

        project = self._load_json_file(source_dir / self.project_manager.PROJECT_FILE)
        if not isinstance(project, dict) or not project_schema_is_current(project):
            return None
        return dict(ProjectArtifactManifestAdapter(source_dir).snapshot_entries())

    def _visible_tree_signature(self, root: Path) -> tuple[tuple[str, str], ...]:
        signature: list[tuple[str, str]] = []
        for current_path, relative_dir, filenames in self._iter_visible_tree(root):
            if relative_dir != Path("."):
                signature.append((f"{relative_dir.as_posix()}/", "directory"))
            for filename in filenames:
                path = current_path / filename
                signature.append(((relative_dir / filename).as_posix(), sha256_file(path)))
        return tuple(signature)

    def _iter_visible_tree(self, root: Path) -> Iterator[tuple[Path, Path, tuple[str, ...]]]:
        for current_dir, dirnames, filenames in os.walk(root):
            current_path = Path(current_dir)
            is_root = current_path == root
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not name.startswith(".")
                and not (current_path / name).is_symlink()
                and not (is_root and name in self._AGENT_RUNTIME_EXCLUDES)
                and not (is_root and name not in self._ROOT_VISIBLE_ENTRIES)
            ]
            visible_files = tuple(
                name
                for name in sorted(filenames)
                if not name.startswith(".")
                and not (current_path / name).is_symlink()
                and not (is_root and name in self._AGENT_RUNTIME_EXCLUDES)
                and not (is_root and name not in self._ROOT_VISIBLE_ENTRIES)
            )
            yield current_path, current_path.relative_to(root), visible_files

    def _copy_visible_tree(self, source_dir: Path, target_dir: Path) -> tuple[tuple[str, str], ...]:
        target_dir.mkdir(parents=True, exist_ok=True)
        copied: list[tuple[str, str]] = []
        for current_path, relative_dir, filenames in self._iter_visible_tree(source_dir):
            destination_dir = target_dir / relative_dir
            destination_dir.mkdir(parents=True, exist_ok=True)
            if relative_dir != Path("."):
                copied.append((f"{relative_dir.as_posix()}/", "directory"))

            for filename in filenames:
                source_path = current_path / filename
                destination_path = destination_dir / filename
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                with source_path.open("rb") as source, destination_path.open("wb") as destination:

                    def _copy_chunk(size: int, source=source, destination=destination) -> bytes:
                        chunk = source.read(size)
                        destination.write(chunk)
                        return chunk

                    hexdigest, _size, _content = digest_stream(_copy_chunk)
                shutil.copystat(source_path, destination_path, follow_symlinks=False)
                copied.append(((relative_dir / filename).as_posix(), hexdigest))
        return tuple(copied)

    def _repair_project_tree(self, project_dir: Path) -> ArchiveDiagnostics:
        diagnostics = ArchiveDiagnostics()
        project_path = project_dir / self.project_manager.PROJECT_FILE
        project = self._load_json_file(project_path)
        if project is None:
            diagnostics.add(
                "blocking",
                "invalid_project_json",
                ValidationMessage(
                    "arch_invalid_project_json",
                    {"file": self.project_manager.PROJECT_FILE, "path": project_path},
                ),
                location=self.project_manager.PROJECT_FILE,
            )
            return diagnostics

        basename_index = self._build_basename_index(project_dir)
        versions_payload = self._load_versions_payload(project_dir)
        project_changed = False

        style_image_rel = project.get("style_image") or "style_reference.png"
        if self._repair_path_to_canonical(
            project_dir,
            project,
            field_name="style_image",
            canonical_rel=style_image_rel,
            location="project.style_image",
            diagnostics=diagnostics,
        ):
            project_changed = True

        characters = project.get("characters")
        if isinstance(characters, dict):
            for char_name, char_data in characters.items():
                if not isinstance(char_data, dict):
                    continue
                if self._repair_path_to_canonical(
                    project_dir,
                    char_data,
                    field_name="character_sheet",
                    canonical_rel=f"characters/{char_name}.png",
                    location=f"characters[{char_name}].character_sheet",
                    diagnostics=diagnostics,
                    resource_type="characters",
                    resource_id=char_name,
                    versions_payload=versions_payload,
                ):
                    project_changed = True
                if self._repair_path_to_canonical(
                    project_dir,
                    char_data,
                    field_name="reference_image",
                    canonical_rel=f"characters/refs/{char_name}.png",
                    location=f"characters[{char_name}].reference_image",
                    diagnostics=diagnostics,
                ):
                    project_changed = True
                # reference_audio 不像 reference_image 强制统一扩展名（不转码），扩展名从
                # 现有字段值推导，与上传落盘约定（characters/refs_audio/{name}<ext>）一致
                audio_raw = char_data.get("reference_audio")
                audio_ext = Path(audio_raw).suffix if isinstance(audio_raw, str) and audio_raw.strip() else ""
                if self._repair_path_to_canonical(
                    project_dir,
                    char_data,
                    field_name="reference_audio",
                    canonical_rel=f"characters/refs_audio/{char_name}{audio_ext}",
                    location=f"characters[{char_name}].reference_audio",
                    diagnostics=diagnostics,
                ):
                    project_changed = True

        scenes = project.get("scenes")
        if isinstance(scenes, dict):
            for scene_name, scene_data in scenes.items():
                if not isinstance(scene_data, dict):
                    continue
                if self._repair_path_to_canonical(
                    project_dir,
                    scene_data,
                    field_name="scene_sheet",
                    canonical_rel=f"scenes/{scene_name}.png",
                    location=f"scenes[{scene_name}].scene_sheet",
                    diagnostics=diagnostics,
                    resource_type="scenes",
                    resource_id=scene_name,
                    versions_payload=versions_payload,
                ):
                    project_changed = True

        props = project.get("props")
        if isinstance(props, dict):
            for prop_name, prop_data in props.items():
                if not isinstance(prop_data, dict):
                    continue
                if self._repair_path_to_canonical(
                    project_dir,
                    prop_data,
                    field_name="prop_sheet",
                    canonical_rel=f"props/{prop_name}.png",
                    location=f"props[{prop_name}].prop_sheet",
                    diagnostics=diagnostics,
                    resource_type="props",
                    resource_id=prop_name,
                    versions_payload=versions_payload,
                ):
                    project_changed = True

        project_characters = {name for name, payload in (characters or {}).items() if isinstance(payload, dict)}
        project_scenes = {name for name, payload in (scenes or {}).items() if isinstance(payload, dict)}
        project_props = {name for name, payload in (props or {}).items() if isinstance(payload, dict)}
        products = project.get("products")
        project_products = {name for name, payload in (products or {}).items() if isinstance(payload, dict)}

        episodes = project.get("episodes")
        if isinstance(episodes, list):
            for index, episode_meta in enumerate(episodes):
                if not isinstance(episode_meta, dict):
                    continue

                script_location = f"episodes[{index}].script_file"
                script_file = episode_meta.get("script_file")
                if isinstance(script_file, str) and script_file.strip():
                    repaired_script = self._repair_relative_reference(
                        project_dir,
                        script_file,
                        default_dir="scripts",
                        basename_index=basename_index,
                        preferred_prefix="scripts/",
                    )
                    if repaired_script and repaired_script != script_file.replace("\\", "/"):
                        episode_meta["script_file"] = repaired_script
                        project_changed = True
                        diagnostics.add(
                            "auto_fixed",
                            "script_file_repaired",
                            ValidationMessage(
                                "arch_script_file_repaired",
                                {"location": script_location, "path": repaired_script},
                            ),
                            location=script_location,
                        )
                    script_path_rel = repaired_script or script_file.replace("\\", "/")
                else:
                    script_path_rel = None

                if not script_path_rel:
                    continue

                script_path = project_dir / script_path_rel
                if not script_path.exists():
                    if parse_positive_episode_num(episode_meta.get("episode")) is not None:
                        # 账本条目的 script_file 是前瞻性契约（剧本生成时回填真实值），
                        # 拆分先于剧本存在是设计内状态，不阻断归档往返；ledger_status 不
                        # 参与判定——v2→v3 迁移不再回填该字段，老项目升级后的条目可能永远
                        # 没有 ledger_status，形状合法即视为正常账本条目
                        diagnostics.add(
                            "warnings",
                            "missing_script_file",
                            ValidationMessage(
                                "arch_missing_script_file_pending",
                                {"location": script_location, "path": script_path_rel},
                            ),
                            location=script_location,
                        )
                    else:
                        diagnostics.add(
                            "blocking",
                            "missing_script_file",
                            ValidationMessage(
                                "arch_missing_script_file",
                                {"location": script_location, "path": script_path_rel},
                            ),
                            location=script_location,
                        )
                    continue

                script_payload = self._load_json_file(script_path)
                if script_payload is None:
                    diagnostics.add(
                        "blocking",
                        "invalid_script_json",
                        ValidationMessage("arch_invalid_script_json", {"path": script_path_rel}),
                        location=script_location,
                    )
                    continue

                script_changed, project_changed_from_script = self._repair_script_payload(
                    project_dir,
                    script_path_rel=script_path_rel,
                    script_payload=script_payload,
                    project_payload=project,
                    project_characters=project_characters,
                    project_scenes=project_scenes,
                    project_props=project_props,
                    project_products=project_products,
                    versions_payload=versions_payload,
                    diagnostics=diagnostics,
                    basename_index=basename_index,
                )
                if script_changed:
                    self._write_json_file(script_path, script_payload)
                if project_changed_from_script:
                    project_changed = True

        if project_changed:
            self._write_json_file(project_path, project)

        return diagnostics

    def _repair_script_payload(
        self,
        project_dir: Path,
        *,
        script_path_rel: str,
        script_payload: dict[str, Any],
        project_payload: dict[str, Any],
        project_characters: set[str],
        project_scenes: set[str],
        project_props: set[str],
        project_products: set[str],
        versions_payload: dict[str, Any],
        diagnostics: ArchiveDiagnostics,
        basename_index: dict[str, list[str]],
    ) -> tuple[bool, bool]:
        script_changed = False
        project_changed = False

        novel = script_payload.get("novel")
        if isinstance(novel, dict) and "source_file" in novel:
            novel.pop("source_file")
            script_changed = True
            diagnostics.add(
                "auto_fixed",
                "deprecated_source_file_removed",
                ValidationMessage("arch_deprecated_source_file_removed"),
                location=f"{script_path_rel}:novel.source_file",
            )

        # 剥离废弃的 episode 级聚合字段
        for deprecated_field in ("characters_in_episode", "clues_in_episode"):
            if deprecated_field in script_payload:
                script_payload.pop(deprecated_field)
                script_changed = True
                diagnostics.add(
                    "auto_fixed",
                    "deprecated_field_removed",
                    ValidationMessage("arch_deprecated_field_removed", {"field": deprecated_field}),
                    location=f"{script_path_rel}:{deprecated_field}",
                )

        # 剧本戳 → 项目声明的回退链保留；链尾 narration 终兜底删除——项目级 content_mode
        # 必填且被校验，两处皆缺（或非字符串脏值）即数据损坏，直接 fail-loud，不静默落 drama。
        # 不用 str(...) 归一：会把缺失的 None 变成字面量 "None" 字符串，既让 reference 分支拿到
        # 假值绕过 fail-loud，又使非 reference 分支的报错语义失真。
        raw_content_mode = script_payload.get("content_mode") or project_payload.get("content_mode")
        if not isinstance(raw_content_mode, str):
            raise ValueError(f"未知或缺失 content_mode: {raw_content_mode!r}")
        content_mode = raw_content_mode
        generation_mode = project_payload.get("generation_mode")

        # 修复分流按规范解析的骨架种类走：所有参考路线都使用 video_units，storyboard
        # 路线按内容模式使用 segments/scenes/shots。
        kind = resolve_declared_kind(content_mode, generation_mode)

        # video_units 骨架用 references 组织资产，结构与
        # storyboard 骨架的 characters/scenes/props 不同，单独走专用修复分支。
        if kind == "video_units":
            units_changed, units_project_changed = self._repair_video_units_payload(
                project_dir,
                script_path_rel=script_path_rel,
                script_payload=script_payload,
                project_payload=project_payload,
                project_characters=project_characters,
                project_scenes=project_scenes,
                project_props=project_props,
                project_products=project_products,
                content_mode=content_mode,
                versions_payload=versions_payload,
                diagnostics=diagnostics,
            )
            return script_changed or units_changed, project_changed or units_project_changed

        # storyboard 骨架（segments/scenes/shots，含 ad 的 shots）逐条补全字段与资产回填。
        items_key = kind
        raw_items, id_field, _kind = resolve_kind_items(script_payload, kind=kind)
        chars_field = SKELETONS[kind].chars_field
        # storyboard 骨架必有 chars_field；video_units 已在上分支返回。
        assert chars_field is not None

        if not isinstance(raw_items, list):
            return script_changed, project_changed

        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue

            location_prefix = f"{script_path_rel}:{items_key}[{index}]"
            resource_id = str(item.get(id_field) or "").strip()

            for legacy_field in ("clues_in_segment", "clues_in_scene", "clues"):
                if legacy_field in item:
                    item.pop(legacy_field)
                    script_changed = True
                    diagnostics.add(
                        "auto_fixed",
                        "deprecated_clue_field_removed",
                        ValidationMessage(
                            "arch_deprecated_clue_field_removed",
                            {"items_key": items_key, "index": index, "field": legacy_field},
                        ),
                        location=f"{location_prefix}.{legacy_field}",
                    )

            for asset_field in ("scenes", "props"):
                if asset_field not in item:
                    item[asset_field] = []
                    script_changed = True
                    diagnostics.add(
                        "auto_fixed",
                        f"missing_{asset_field}_field",
                        ValidationMessage(
                            "arch_missing_field_filled",
                            {"items_key": items_key, "index": index, "field": asset_field},
                        ),
                        location=f"{location_prefix}.{asset_field}",
                    )

            assets, assets_changed = self._backfill_generated_assets(
                item,
                content_mode=content_mode,
                label=items_key,
                index=index,
                location_prefix=location_prefix,
                diagnostics=diagnostics,
            )
            if assets_changed:
                script_changed = True

            characters = item.get(chars_field)
            if isinstance(characters, list):
                for character_name in characters:
                    if not isinstance(character_name, str):
                        continue
                    if self._add_placeholder_character(
                        project_payload,
                        project_characters,
                        character_name,
                        diagnostics,
                    ):
                        project_changed = True

            for asset_field, pool, asset_type in (
                ("scenes", project_scenes, "scene"),
                ("props", project_props, "prop"),
            ):
                refs = item.get(asset_field)
                if not isinstance(refs, list):
                    continue
                missing = sorted(
                    {name for name in refs if isinstance(name, str) and _resolve_existing_asset(name, pool) not in pool}
                )
                if missing:
                    diagnostics.add(
                        "blocking",
                        f"missing_{asset_type}_definition",
                        ValidationMessage(
                            "arch_missing_asset_definition",
                            {
                                "items_key": items_key,
                                "index": index,
                                "field": asset_field,
                                "asset_type": MessageRef(f"asset_type_{asset_type}"),
                                "names": ", ".join(missing),
                            },
                        ),
                        location=f"{location_prefix}.{asset_field}",
                    )

            if isinstance(assets, dict) and resource_id:
                for field_name, resource_type in (
                    ("storyboard_image", "storyboards"),
                    ("video_clip", "videos"),
                    ("narration_audio", "audio"),
                ):
                    if self._repair_path_to_canonical(
                        project_dir,
                        assets,
                        field_name=field_name,
                        canonical_rel=resource_relative_path(resource_type, resource_id),
                        location=f"{location_prefix}.generated_assets.{field_name}",
                        diagnostics=diagnostics,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        versions_payload=versions_payload,
                    ):
                        script_changed = True

        return script_changed, project_changed

    def _backfill_generated_assets(
        self,
        item: dict[str, Any],
        *,
        content_mode: str,
        label: str,
        index: int,
        location_prefix: str,
        diagnostics: ArchiveDiagnostics,
    ) -> tuple[Any, bool]:
        """补全 item.generated_assets 的缺失字段，返回 (assets, changed)。"""
        assets = item.get("generated_assets")
        changed = False
        if isinstance(assets, dict):
            template = self.project_manager.create_generated_assets(content_mode)
            missing_keys = [key for key in template if key not in assets]
            if missing_keys:
                for key in missing_keys:
                    assets[key] = template[key]
                changed = True
                # 补全值非 None 的才报诊断，避免 no-op 补全产生噪音
                non_null_keys = sorted(k for k in missing_keys if template[k] is not None)
                if non_null_keys:
                    diagnostics.add(
                        "auto_fixed",
                        "generated_assets_defaults",
                        ValidationMessage(
                            "arch_generated_assets_defaults",
                            {"label": label, "index": index, "fields": ", ".join(non_null_keys)},
                        ),
                        location=f"{location_prefix}.generated_assets",
                    )
        else:
            # None = 字段缺失，其余非 dict = 外部编辑损坏（list/str 等）；两者同样重置为模板结构，
            # 只在诊断上区分，让导入方知道是补齐还是丢弃了脏数据。
            if assets is None:
                code = "missing_generated_assets"
                message = ValidationMessage("arch_missing_generated_assets", {"label": label, "index": index})
            else:
                code = "invalid_generated_assets"
                message = ValidationMessage(
                    "arch_invalid_generated_assets",
                    {"label": label, "index": index, "actual": type(assets).__name__},
                )
            item["generated_assets"] = self.project_manager.create_generated_assets(content_mode)
            changed = True
            diagnostics.add(
                "auto_fixed",
                code,
                message,
                location=f"{location_prefix}.generated_assets",
            )
            assets = item["generated_assets"]
        return assets, changed

    def _add_placeholder_character(
        self,
        project_payload: dict[str, Any],
        project_characters: set[str],
        character_name: str,
        diagnostics: ArchiveDiagnostics,
    ) -> bool:
        """为缺失的角色引用补占位定义，返回是否改动 project_payload。

        成员判定经 :func:`_resolve_existing_asset`：已登记角色的另一种编码形式不再被
        当作缺失补进第二条占位定义（后写入胜出会盖掉真实的 sheet / 配音元数据）。
        """
        if _resolve_existing_asset(character_name, project_characters) in project_characters:
            return False
        project_payload.setdefault("characters", {})
        if not isinstance(project_payload.get("characters"), dict):
            return False
        project_payload["characters"][character_name] = {
            "description": self._PLACEHOLDER_CHARACTER_DESCRIPTION,
        }
        project_characters.add(character_name)
        diagnostics.add(
            "auto_fixed",
            "placeholder_character_added",
            ValidationMessage("arch_placeholder_character_added", {"name": character_name}),
            location=f"characters[{character_name}]",
        )
        return True

    def _repair_video_units_payload(
        self,
        project_dir: Path,
        *,
        script_path_rel: str,
        script_payload: dict[str, Any],
        project_payload: dict[str, Any],
        project_characters: set[str],
        project_scenes: set[str],
        project_props: set[str],
        project_products: set[str],
        content_mode: str,
        versions_payload: dict[str, Any],
        diagnostics: ArchiveDiagnostics,
    ) -> tuple[bool, bool]:
        """修复 reference_video 模式剧本的 video_units，返回 (script_changed, project_changed)。

        video_units 没有 storyboard 条目的 characters/scenes/props 字段，引用资产改放在
        references（list[{type, name}]）里。本方法做三件事，与 narration/drama 分支对齐：
        generated_assets 补全；references 自愈（缺失角色补占位、缺失场景/道具报阻断）；
        video_clip / video_thumbnail 路径规范化与版本回溯。
        video_uri 是远端 URL，不当作本地路径处理（否则会被同名 canonical 本地文件覆盖）。
        """
        raw_units = script_payload.get("video_units")
        if not isinstance(raw_units, list):
            return False, False

        # 存量归档可能仍是收编前的形状（时长挂在 shots 上、unit 缺 duration_seconds）：
        # 下游的结构校验（DataValidator）要求 unit 级 duration_seconds 落在结构区间内，
        # 修复须先跑这道迁移再校验——本方法在 validate_project_tree 之前执行、写回结果
        # 由调用方按 script_changed 落盘，与其它字段修复共用同一次写盘。
        # 档位表按归档自带 project.json 的自报身份查 registry（无 DB 访问——导入跑在 to_thread
        # 里，且此刻自定义供应商的凭证/能力可能尚未导入本机）：迁移一次落盘，与生成侧、审阅门
        # 口径不一致会让先跑的把非档位秒数固化。查不到（未声明型号、或自定义供应商不在 registry）
        # 时为 None，退回结构区间 clamp。
        # provider 先在副本上归一化：本方法跑在 migrate_project_dir 之前，存量归档里可能还是
        # legacy 别名（如 gemini/…），registry 查不到会让档位解析落空，而迁移幂等、归一化之后
        # 再无机会取档。归一化是纯函数且幂等，不影响随后的正式迁移。
        normalized_project = normalize_legacy_providers(project_payload)
        migrated, migration_warnings = migrate_unit_durations(
            raw_units, supported_durations=resolve_raw_supported_durations(normalized_project)
        )
        changed = migrated
        for message in migration_warnings:
            # 迁移消息本身不含剧本路径，出处经 location 携带（消息模板因此对各调用链复用）。
            diagnostics.add(
                "warnings",
                "reference_video_duration_migrated",
                message,
                location=script_path_rel,
            )

        project_changed = False
        for index, unit in enumerate(raw_units):
            if not isinstance(unit, dict):
                continue

            location_prefix = f"{script_path_rel}:video_units[{index}]"
            resource_id = str(unit.get("unit_id") or "").strip()

            assets, assets_changed = self._backfill_generated_assets(
                unit,
                content_mode=content_mode,
                label="video_units",
                index=index,
                location_prefix=location_prefix,
                diagnostics=diagnostics,
            )
            if assets_changed:
                changed = True

            if self._repair_unit_mentions(
                unit,
                project_payload=project_payload,
                project_characters=project_characters,
                project_scenes=project_scenes,
                project_props=project_props,
                project_products=project_products,
                index=index,
                location_prefix=location_prefix,
                diagnostics=diagnostics,
            ):
                project_changed = True

            if not (isinstance(assets, dict) and resource_id):
                continue

            # video_clip 有版本历史，可从 versions/ 回溯物化当前文件
            if self._repair_path_to_canonical(
                project_dir,
                assets,
                field_name="video_clip",
                canonical_rel=resource_relative_path("reference_videos", resource_id),
                location=f"{location_prefix}.generated_assets.video_clip",
                diagnostics=diagnostics,
                resource_type="reference_videos",
                resource_id=resource_id,
                versions_payload=versions_payload,
            ):
                changed = True

            # 缩略图无版本历史，仅在 canonical 文件存在时规范化路径
            if self._repair_path_to_canonical(
                project_dir,
                assets,
                field_name="video_thumbnail",
                canonical_rel=f"reference_videos/thumbnails/{resource_id}.jpg",
                location=f"{location_prefix}.generated_assets.video_thumbnail",
                diagnostics=diagnostics,
            ):
                changed = True

        return changed, project_changed

    def _repair_unit_mentions(
        self,
        unit: dict[str, Any],
        *,
        project_payload: dict[str, Any],
        project_characters: set[str],
        project_scenes: set[str],
        project_props: set[str],
        project_products: set[str],
        index: int,
        location_prefix: str,
        diagnostics: ArchiveDiagnostics,
    ) -> bool:
        """自愈 video_unit 正文里的 ``@[名称]``：说话人缺定义补占位，其余未解析的只警告。

        正文是单元的唯一真相，参考图执行期才解析，未解析的提及只是「这一处不出参考图」，
        不阻断导入。说话人是例外：``@[角色]{台词}`` 的位置在语法上就断定它是角色，缺定义会
        让这句台词丢掉声音绑定，故与 narration/drama 同口径补占位角色。

        名字与 registered 集合的 key 可以是 NFC/NFD 中的任一形态，成员判定一律经
        :func:`_resolve_existing_asset`，与 narration/drama 分支同口径。

        返回是否补过占位角色（即 ``project_payload`` 是否改动）。
        """
        text = unit.get("text")
        if not isinstance(text, str) or not text.strip():
            return False

        project_changed = False
        # 说话人先补：``extract_mentions`` 按设计剔除了发声记号内的 speaker 位（说话人不进参考图），
        # 只出现在 ``{}`` 前的角色因此不在提及列表里，补占位必须另取一遍说话人。
        for speaker in dialogue_speakers(text):
            name = asset_name_comparison_key(speaker)
            if _resolve_existing_asset(name, project_characters) in project_characters:
                continue
            if self._add_placeholder_character(project_payload, project_characters, name, diagnostics):
                project_changed = True

        unresolved: list[str] = []
        for name in extract_mentions(text):
            if any(
                _resolve_existing_asset(name, pool) in pool
                for pool in (project_characters, project_scenes, project_props, project_products)
            ):
                continue
            unresolved.append(name)

        if unresolved:
            diagnostics.add(
                "warnings",
                "unresolved_mention",
                ValidationMessage(
                    "arch_unit_unresolved_mentions",
                    {"index": index, "names": ", ".join(sorted(unresolved))},
                ),
                location=f"{location_prefix}.text",
            )
        return project_changed

    def _repair_path_to_canonical(
        self,
        project_dir: Path,
        payload: dict[str, Any],
        *,
        field_name: str,
        canonical_rel: str,
        location: str,
        diagnostics: ArchiveDiagnostics,
        resource_type: str | None = None,
        resource_id: str | None = None,
        versions_payload: dict[str, Any] | None = None,
    ) -> bool:
        raw_value = payload.get(field_name)
        if not isinstance(raw_value, str) or not raw_value.strip():
            return False

        normalized_value = raw_value.strip().replace("\\", "/")
        canonical_path = project_dir / canonical_rel
        resolved_raw = self._resolve_existing_relative(project_dir, normalized_value)

        if canonical_path.exists():
            if normalized_value != canonical_rel:
                payload[field_name] = canonical_rel
                diagnostics.add(
                    "auto_fixed",
                    "canonical_path_normalized",
                    ValidationMessage("arch_canonical_path_normalized", {"location": location, "path": canonical_rel}),
                    location=location,
                )
                return True
            return False

        if resolved_raw:
            if (
                resource_type
                and resource_id
                and resolved_raw.startswith(f"versions/{resource_type}/")
                and Path(resolved_raw).name.startswith(f"{resource_id}_v")
            ):
                if self._materialize_current_file(
                    project_dir / resolved_raw,
                    canonical_path,
                ):
                    payload[field_name] = canonical_rel
                    diagnostics.add(
                        "auto_fixed",
                        "current_asset_materialized",
                        ValidationMessage(
                            "arch_current_asset_materialized",
                            {"location": location, "source": resolved_raw, "target": canonical_rel},
                        ),
                        location=location,
                    )
                    return True
            return False

        if resource_type and resource_id and versions_payload is not None:
            version_rel = self._resolve_version_file(
                project_dir,
                versions_payload,
                resource_type=resource_type,
                resource_id=resource_id,
            )
            if version_rel:
                if self._materialize_current_file(
                    project_dir / version_rel,
                    canonical_path,
                ):
                    payload[field_name] = canonical_rel
                    diagnostics.add(
                        "auto_fixed",
                        "current_asset_restored_from_version",
                        ValidationMessage(
                            "arch_current_asset_restored_from_version",
                            {"location": location, "source": version_rel, "target": canonical_rel},
                        ),
                        location=location,
                    )
                    return True

        return False

    def _resolve_version_file(
        self,
        project_dir: Path,
        versions_payload: dict[str, Any],
        *,
        resource_type: str,
        resource_id: str,
    ) -> str | None:
        type_payload = versions_payload.get(resource_type, {})
        resource_info = type_payload.get(resource_id) if isinstance(type_payload, dict) else None
        if isinstance(resource_info, dict):
            current_version = resource_info.get("current_version")
            versions = resource_info.get("versions", [])
            if current_version is not None and isinstance(versions, list):
                for version in versions:
                    if (
                        isinstance(version, dict)
                        and version.get("version") == current_version
                        and isinstance(version.get("file"), str)
                    ):
                        rel_path = version["file"].replace("\\", "/")
                        if self._resolve_existing_relative(project_dir, rel_path):
                            return rel_path

        version_dir = project_dir / "versions" / resource_type
        if not version_dir.exists():
            return None

        prefix = f"{resource_id}_v"
        extension = resource_extension(resource_type)
        candidates: list[str] = []
        for candidate in sorted(version_dir.iterdir(), key=lambda path: path.name):
            if candidate.is_file() and candidate.name.startswith(prefix) and candidate.suffix == extension:
                candidates.append(candidate.relative_to(project_dir).as_posix())

        if len(candidates) == 1:
            return candidates[0]
        return None

    def _repair_relative_reference(
        self,
        project_dir: Path,
        raw_value: str,
        *,
        default_dir: str,
        basename_index: dict[str, list[str]],
        preferred_prefix: str | None = None,
        allow_single_preferred_candidate: bool = False,
    ) -> str | None:
        normalized = raw_value.strip().replace("\\", "/")
        if not normalized:
            return None

        resolved = self._resolve_existing_relative(
            project_dir,
            normalized,
            default_dir=default_dir,
        )
        if resolved:
            return resolved

        if "/" not in normalized:
            basename = Path(normalized).name
            preferred_matches = [
                candidate
                for candidate in basename_index.get(basename, [])
                if candidate.startswith(preferred_prefix or "")
            ]
            if len(preferred_matches) == 1:
                return preferred_matches[0]

            all_matches = basename_index.get(basename, [])
            if len(all_matches) == 1:
                return all_matches[0]

        if allow_single_preferred_candidate and preferred_prefix:
            preferred_candidates = sorted(
                {
                    candidate
                    for candidates in basename_index.values()
                    for candidate in candidates
                    if candidate.startswith(preferred_prefix)
                }
            )
            if len(preferred_candidates) == 1:
                return preferred_candidates[0]

        return None

    def _build_basename_index(self, project_dir: Path) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for item in sorted(project_dir.rglob("*")):
            if not item.is_file() or item.is_symlink():
                continue
            relative = item.relative_to(project_dir)
            if self._is_hidden_path(relative):
                continue
            index.setdefault(item.name, []).append(relative.as_posix())
        return index

    def _load_versions_payload(self, project_dir: Path) -> dict[str, Any]:
        versions_path = project_dir / "versions" / "versions.json"
        payload = self._load_json_file(versions_path)
        if payload is None:
            return {
                "storyboards": {},
                "videos": {},
                "characters": {},
                "scenes": {},
                "props": {},
            }
        return payload

    def _collect_pass_through_entries(self, project_dir: Path) -> list[str]:
        entries: list[str] = []
        if not project_dir.exists():
            return entries

        for child in sorted(project_dir.iterdir(), key=lambda item: item.name):
            if self._is_hidden_path(Path(child.name)):
                continue
            if child.name in self._AGENT_RUNTIME_EXCLUDES:
                continue
            if child.name not in self._ROOT_VISIBLE_ENTRIES:
                entries.append(child.name)
        return entries

    @staticmethod
    def _is_hidden_path(path: Path) -> bool:
        return any(part.startswith(".") or part == "__MACOSX" for part in path.parts)

    def _materialize_current_file(self, source_path: Path, target_path: Path) -> bool:
        if not source_path.exists() or source_path.resolve() == target_path.resolve():
            return False
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        return True

    def _resolve_existing_relative(
        self,
        project_dir: Path,
        raw_path: str,
        *,
        default_dir: str | None = None,
    ) -> str | None:
        normalized = raw_path.strip().replace("\\", "/")
        if not normalized:
            return None

        candidates = [Path(normalized)]
        if default_dir and len(candidates[0].parts) == 1:
            candidates.append(Path(default_dir) / candidates[0])

        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.as_posix()
            if key in seen:
                continue
            seen.add(key)

            resolved = try_safe_join(project_dir, candidate)
            if resolved is None:
                continue

            if resolved.exists():
                return candidate.as_posix()

        return None

    def _resolve_json_path(self, path: Path) -> Path | None:
        """归档读写只允许落在 projects_root 或系统临时目录内；越界返回 None。"""
        for base in (self.project_manager.projects_root, tempfile.gettempdir()):
            resolved = try_safe_join(base, path)
            if resolved is not None:
                return resolved
        return None

    def _load_json_file(self, path: Path) -> dict[str, Any] | None:
        real = self._resolve_json_path(path)
        if real is None:
            logger.warning("路径越界，拒绝读取: %s", path)
            return None
        try:
            return load_json(real)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _write_json_file(self, path: Path, payload: dict[str, Any]) -> None:
        real = self._resolve_json_path(path)
        if real is None:
            raise ValueError(f"路径越界，拒绝写入: {path}")
        real.parent.mkdir(parents=True, exist_ok=True)
        with open(real, "w", encoding="utf-8") as handle:  # noqa: PTH123
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def _validate_scope(scope: str) -> None:
        if scope not in {"full", "current"}:
            raise ValueError(f"scope 仅支持 full 或 current，收到: {scope}")

    def _scan_archive_members(self, archive: zipfile.ZipFile) -> list[ArchiveMember]:
        members: list[ArchiveMember] = []
        for info in archive.infolist():
            if info.flag_bits & 0x1:
                raise ProjectArchiveValidationError(
                    ValidationMessage("arch_import_validation_failed"),
                    errors=[ValidationMessage("arch_zip_encrypted_entry", {"name": info.filename})],
                )

            normalized_name = info.filename.replace("\\", "/")
            if normalized_name.startswith("/"):
                raise ProjectArchiveValidationError(
                    ValidationMessage("arch_import_validation_failed"),
                    errors=[ValidationMessage("arch_zip_absolute_path_entry", {"name": info.filename})],
                )

            stripped_name = normalized_name.strip("/")
            if not stripped_name:
                continue

            parts = tuple(part for part in stripped_name.split("/") if part)
            if parts and len(parts[0]) == 2 and parts[0][1] == ":":
                raise ProjectArchiveValidationError(
                    ValidationMessage("arch_import_validation_failed"),
                    errors=[ValidationMessage("arch_zip_absolute_path_entry", {"name": info.filename})],
                )
            if any(part == ".." for part in parts):
                raise ProjectArchiveValidationError(
                    ValidationMessage("arch_import_validation_failed"),
                    errors=[ValidationMessage("arch_zip_traversal_entry", {"name": info.filename})],
                )

            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ProjectArchiveValidationError(
                    ValidationMessage("arch_import_validation_failed"),
                    errors=[ValidationMessage("arch_zip_symlink_entry", {"name": info.filename})],
                )

            members.append(
                ArchiveMember(
                    info=info,
                    parts=parts,
                    is_dir=info.is_dir() or normalized_name.endswith("/"),
                )
            )

        return members

    @staticmethod
    def _is_hidden_member(parts: tuple[str, ...]) -> bool:
        return any(part.startswith(".") or part == "__MACOSX" for part in parts)

    def _load_member_json(
        self,
        archive: zipfile.ZipFile,
        member: ArchiveMember,
        label: str,
    ) -> dict[str, Any]:
        try:
            with archive.open(member.info) as handle:
                return json.loads(handle.read().decode("utf-8"))
        except Exception as exc:
            raise ProjectArchiveValidationError(
                ValidationMessage("arch_import_validation_failed"),
                errors=[
                    ValidationMessage("arch_zip_unparsable_member", {"label": label, "path": "/".join(member.parts)})
                ],
            ) from exc

    def _locate_project_root(
        self,
        archive: zipfile.ZipFile,
        members: list[ArchiveMember],
    ) -> tuple[tuple[str, ...], dict[str, Any] | None]:
        visible_members = [member for member in members if not self._is_hidden_member(member.parts)]

        manifest_members = [member for member in visible_members if member.parts[-1] == ARCHIVE_MANIFEST_NAME]
        if manifest_members:
            root_candidates = {member.parts[:-1] for member in manifest_members}
            if len(root_candidates) != 1:
                raise ProjectArchiveValidationError(
                    ValidationMessage("arch_import_validation_failed"),
                    errors=[ValidationMessage("arch_multiple_manifests")],
                )

            root_parts = next(iter(root_candidates))
            if not any(member.parts == (*root_parts, self.project_manager.PROJECT_FILE) for member in visible_members):
                raise ProjectArchiveValidationError(
                    ValidationMessage("arch_import_validation_failed"),
                    errors=[ValidationMessage("arch_manifest_missing_project_json")],
                )

            manifest = self._load_member_json(
                archive,
                manifest_members[0],
                ARCHIVE_MANIFEST_NAME,
            )
            return root_parts, manifest

        project_members = [
            member for member in visible_members if member.parts[-1] == self.project_manager.PROJECT_FILE
        ]
        root_candidates = {member.parts[:-1] for member in project_members}
        if not root_candidates:
            raise ProjectArchiveValidationError(
                ValidationMessage("arch_import_validation_failed"),
                errors=[ValidationMessage("arch_no_project_json")],
            )
        if len(root_candidates) != 1:
            raise ProjectArchiveValidationError(
                ValidationMessage("arch_import_validation_failed"),
                errors=[ValidationMessage("arch_multiple_project_json")],
            )

        return next(iter(root_candidates)), None

    def _extract_archive_root(
        self,
        archive: zipfile.ZipFile,
        members: list[ArchiveMember],
        root_parts: tuple[str, ...],
        staging_dir: Path,
    ) -> None:
        root_length = len(root_parts)

        for member in members:
            if member.parts[:root_length] != root_parts:
                continue

            relative_parts = member.parts[root_length:]
            if not relative_parts:
                continue
            if relative_parts == (ARCHIVE_MANIFEST_NAME,):
                continue
            if self._is_hidden_member(relative_parts):
                continue

            try:
                target_path = safe_join(staging_dir, *relative_parts)
            except PathTraversalError as exc:
                raise ProjectArchiveValidationError(
                    ValidationMessage("arch_import_validation_failed"),
                    errors=[ValidationMessage("arch_extract_path_traversal", {"path": "/".join(member.parts)})],
                ) from exc

            if member.is_dir:
                target_path.mkdir(parents=True, exist_ok=True)
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member.info) as source, open(target_path, "wb") as target:
                shutil.copyfileobj(source, target)

    def _normalize_project_name(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            return self.project_manager.normalize_project_name(value)
        except ValueError:
            return None

    def _resolve_target_project_name(
        self,
        project: dict[str, Any],
        *,
        manifest: dict[str, Any] | None,
        root_parts: tuple[str, ...],
        uploaded_filename: str | None,
    ) -> str:
        manifest_name = self._normalize_project_name((manifest or {}).get("project_name"))
        if manifest_name:
            return manifest_name

        root_name = self._normalize_project_name(root_parts[-1] if root_parts else None)
        if root_name:
            return root_name

        project_title = str(project.get("title") or "").strip()
        if project_title:
            return self.project_manager.generate_project_name(project_title)

        filename_stem = Path(uploaded_filename or DEFAULT_IMPORT_FILENAME).stem
        return self.project_manager.generate_project_name(filename_stem)

    @staticmethod
    def _load_project_file(project_path: Path) -> dict[str, Any]:
        with open(project_path, encoding="utf-8") as handle:
            return json.load(handle)

    def _resolve_conflict(
        self,
        preferred_name: str,
        *,
        project_title: str,
        conflict_policy: str,
    ) -> tuple[str, str]:
        target_dir = self.project_manager.projects_root / preferred_name
        if conflict_policy == "prompt":
            if target_dir.exists():
                raise ProjectArchiveValidationError(
                    ValidationMessage("arch_conflict_detected"),
                    status_code=409,
                    errors=[ValidationMessage("arch_project_name_conflict", {"name": preferred_name})],
                    extra={"conflict_project_name": preferred_name},
                )
            return preferred_name, "none"

        if conflict_policy == "rename":
            if target_dir.exists():
                generated_name = self.project_manager.generate_project_name(project_title or preferred_name)
                return generated_name, "renamed"
            return preferred_name, "none"

        if target_dir.exists():
            return preferred_name, "overwritten"
        return preferred_name, "none"

    def _ensure_standard_subdirs(self, project_dir: Path) -> None:
        for subdir in self.project_manager.SUBDIRS:
            (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    def _install_project_dir(
        self,
        staging_dir: Path,
        project_name: str,
        *,
        overwrite: bool,
    ) -> None:
        target_dir = self.project_manager.projects_root / project_name
        backup_dir: Path | None = None

        try:
            if overwrite and target_dir.exists():
                backup_dir = target_dir.with_name(f".import-backup-{target_dir.name}-{secrets.token_hex(4)}")
                target_dir.rename(backup_dir)

            shutil.move(str(staging_dir), str(target_dir))
            # profile sync 是安装的一部分；纳入同一个事务里，sync 失败也走下面的
            # rollback：删 target_dir + 恢复 backup_dir。否则失败时旧项目已经被删，
            # 用户会丢数据（overwrite 分支）或留半安装状态（new 分支）
            self.project_manager.sync_agent_profile(target_dir)
        except Exception:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            if backup_dir and backup_dir.exists():
                backup_dir.rename(target_dir)
            raise

        if backup_dir and backup_dir.exists():
            shutil.rmtree(backup_dir)
