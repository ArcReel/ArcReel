from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import stat
import tempfile
import zipfile
from collections.abc import AsyncGenerator, Callable, Iterator, Mapping
from contextlib import AbstractContextManager, asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcreel_market_core.validation_messages import MessageRef, ValidationMessage
from lib.artifacts.artifact_activation import (
    ensure_imported_artifact_target_state,
    snapshot_preserved_artifact_manifest,
    stage_preserved_artifact_claims,
    take_migrated_artifact_claims,
)
from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ProjectArtifactManifestAdapter,
    decode_artifact_manifest_payload,
    encode_artifact_manifest_payload,
)
from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.rendered_artifact import RENDERS_DIRNAME
from lib.artifacts.version_manager import VersionManager, selected_manual_upload_snapshot
from lib.asset_library.links import (
    UPLOADABLE_RESOURCE_TYPES,
    ArchivedLink,
    LinkHolder,
    adopt_archived_links,
    archivable_links,
)
from lib.catalog import query as catalog
from lib.config.resolver import VideoGenerationType, project_video_backend_ids
from lib.episode.episode_ledger import parse_positive_episode_num
from lib.infra.api_errors import BadGatewayError
from lib.infra.async_thread import EventLoopBridge, run_noninterruptible_async
from lib.infra.content_digest import digest_stream, sha256_file
from lib.infra.json_io import load_json
from lib.infra.path_safety import PathTraversalError, safe_join, try_safe_join
from lib.infra.validation_messages import ValidationResult, default_translate
from lib.material.formal_objects import (
    ObjectContentStorage,
    RemoteSaveFailedError,
    iter_version_records,
    stores_remotely,
)
from lib.material.storage import CONTENT_DIGEST, CONTENT_VERSION_ID, STORAGE, ObjectLocator, new_content_version_id
from lib.material.work_files import (
    MaterialFetchFailedError,
    RemoteWorkFile,
    held_work_files,
    locked_remote_work_files,
    remote_work_files,
)
from lib.project import deleted_projects
from lib.project.asset_types import ASSET_SPECS, asset_name_comparison_key, build_asset_entry, normalize_asset_name
from lib.project.data_validator import DataValidator
from lib.project.project_change_hints import emit_project_change_hint
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_failure import ProjectMigrationError, migration_failure_details
from lib.project.project_migration_report import (
    MigrationAdjustment,
    acknowledge_migration_report,
    extend_migration_report,
    visible_migration_report,
)
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_migrations.v1_to_v2_normalize_providers import (
    migrate_project_dict as normalize_legacy_providers,
)
from lib.project.project_schema import project_schema_is_current
from lib.project.resource_paths import (
    CHARACTER_DERIVATIVE_RESOURCE_TYPE,
    END_FRAME_RESOURCE_TYPE,
    resource_extension,
    resource_id_segments,
    resource_of_path,
    resource_relative_path,
)
from lib.script.reference_video.draft_validation import dialogue_speakers
from lib.script.reference_video.duration_migration import migrate_unit_durations
from lib.script.reference_video.text_parser import extract_mentions
from lib.script.script_skeleton import SKELETONS, resolve_declared_kind, resolve_kind_items
from lib.script.source_loader.migration import migrate_project_source_encoding
from server.services.project.project_retirement import RetireProject

#: Formal archive-import commit: takes destination project name and supplies image object storage for this import (``None`` for local destinations).
FormalCommit = Callable[[str], AbstractContextManager[ObjectContentStorage | None]]


class ProviderAssetLinkArchive(Protocol):
    """DB reads/writes for archived provider-asset associations, used by synchronous export/import in worker threads."""

    def export(self, project_name: str, content_digests: dict[str, str]) -> list[ArchivedLink]:
        """Currently usable associations for these project content versions (identity → digest)."""
        ...

    def restore(self, links: list[tuple[ArchivedLink, LinkHolder]]) -> int:
        """Persist verified associations for the imported project, commit, and return their count."""
        ...


@dataclass(frozen=True)
class _LinkArchiveOnLoop:
    bridge: EventLoopBridge
    session_factory: async_sessionmaker[AsyncSession]

    def export(self, project_name: str, content_digests: dict[str, str]) -> list[ArchivedLink]:
        return self.bridge.run(self._export(project_name, content_digests))

    def restore(self, links: list[tuple[ArchivedLink, LinkHolder]]) -> int:
        return self.bridge.run(self._restore(links))

    async def _export(self, project_name: str, content_digests: dict[str, str]) -> list[ArchivedLink]:
        async with self.session_factory() as session:
            return await archivable_links(session, project_name=project_name, content_digests=content_digests)

    async def _restore(self, links: list[tuple[ArchivedLink, LinkHolder]]) -> int:
        async with self.session_factory() as session:
            adopted = await adopt_archived_links(session, links)
            await run_noninterruptible_async(session.commit())
            return len(adopted)


def provider_asset_link_archive_on(
    bridge: EventLoopBridge, session_factory: async_sessionmaker[AsyncSession]
) -> ProviderAssetLinkArchive:
    """Association reads/writes for worker-thread export/import: dispatch back to ``bridge``'s event loop; usable only in worker threads."""
    return _LinkArchiveOnLoop(bridge, session_factory)


logger = logging.getLogger(__name__)

ARCHIVE_MANIFEST_NAME = "arcreel-export.json"
#: Provider associations in archive envelopes (``ArchivedLink.to_payload`` list); omit the field when none are available.
ARCHIVED_LINKS_KEY = "provider_asset_links"
ARCHIVE_FORMAT_VERSION = 2
ARCHIVE_SCRIPT_SCHEMA_VERSION = 2
DEFAULT_IMPORT_FILENAME = "imported-project.zip"
_ARTIFACT_ACTIVATION_ERRORS = (ArtifactManifestError, OSError, UnicodeError, ValueError)
_EXPORT_SNAPSHOT_ATTEMPTS = 3


class CurrentExportVersionRetention(Enum):
    """Version history retained in a current-version-only archive for one resource type.

    Always export current content from formal paths. Include retained version records and their snapshots
    together, excluding all others, so packaged versions.json records correspond exactly to snapshots."""

    NONE = "none"
    """Do not retain version history."""

    SELECTED = "selected"
    """Retain the selected record and snapshot for each resource: import activation independently verifies typed-media bases with them, including claims for manually uploaded videos."""

    MANUAL_UPLOAD = "manual_upload"
    """Retain only resources whose selected record is a manual upload: the planner uses that record and snapshot to assess currency from the upload itself."""


#: Current-only export retention by resource type, covering every ``RESOURCE_TYPES`` value.
#: Planners judge asset sheets (all ``ASSET_SPECS`` buckets) and character derivatives by selected manual-upload records,
#: so preserve upload evidence. Any type whose currency reads selected manual-upload records must use
#: ``MANUAL_UPLOAD`` or ``SELECTED``.
CURRENT_EXPORT_VERSION_RETENTION: Mapping[str, CurrentExportVersionRetention] = MappingProxyType(
    {
        "storyboards": CurrentExportVersionRetention.NONE,
        END_FRAME_RESOURCE_TYPE: CurrentExportVersionRetention.NONE,
        "grids": CurrentExportVersionRetention.NONE,
        CHARACTER_DERIVATIVE_RESOURCE_TYPE: CurrentExportVersionRetention.MANUAL_UPLOAD,
        "videos": CurrentExportVersionRetention.SELECTED,
        "reference_videos": CurrentExportVersionRetention.SELECTED,
        "audio": CurrentExportVersionRetention.SELECTED,
        **{spec.bucket_key: CurrentExportVersionRetention.MANUAL_UPLOAD for spec in ASSET_SPECS.values()},
    }
)


def _resolve_existing_asset(name: str, candidates: set[str]) -> str:
    """Resolve script asset names to equivalent actual names in *candidates*; return unchanged on a miss.

    Imported scripts/project.json may independently use NFC/NFD (registration normalizes to NFC, existing
    archives do not migrate). Resolve with ``lib.project.asset_types`` comparison coordinates before
    membership checks; otherwise repair duplicates registered assets with visually identical placeholders
    or reports blocking missing-asset errors."""
    if name in candidates:
        return name
    canonical = normalize_asset_name(name)
    for candidate in candidates:
        if normalize_asset_name(candidate) == canonical:
            return candidate
    return name


def _failure_location(exc: BaseException) -> str | None:
    """Migration-rejected file/episode names as diagnostic locations."""
    if not isinstance(exc, ProjectMigrationError) or exc.file is None:
        return None
    return exc.file if exc.episode is None else f"{exc.file} (episode {exc.episode})"


def _take_placeholder_adjustments(diagnostics: ArchiveDiagnostics) -> list[MigrationAdjustment]:
    """Extract repair-added asset placeholders from diagnostics as migration-report adjustments."""
    adjustments: list[MigrationAdjustment] = []
    kept: list[ArchiveDiagnostic] = []
    for item in diagnostics.auto_fixed:
        asset_type = item.code.removeprefix("placeholder_").removesuffix("_added")
        if asset_type not in ASSET_SPECS or item.code != f"placeholder_{asset_type}_added":
            kept.append(item)
            continue
        adjustments.append(
            MigrationAdjustment(
                code="placeholder_asset_added",
                params={"asset_type": asset_type, "name": str(item.message.params.get("name", ""))},
            )
        )
    diagnostics.auto_fixed = kept
    return adjustments


@dataclass(frozen=True)
class ArchiveMember:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]
    is_dir: bool


@dataclass(frozen=True)
class ArchiveDiagnostic:
    """One archive diagnostic. ``message`` is locale-neutral structured data, rendered at consumer boundaries."""

    code: str
    message: ValidationMessage
    location: str | None = None

    def to_payload(self, translate: Callable[..., str] | None = None) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "message": self.message.render(translate or default_translate),
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
        # Deduplicate by default-language rendered text: identical keys with different params are distinct diagnostics;
        # params may contain unhashable lists/sets, but rendered results are stable, hashable equivalent fingerprints.
        key = (bucket, code, message.render(default_translate), location)
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
    """User-visible archive import/export failure. ``detail`` / ``errors`` / ``warnings`` are structured
    messages rendered by routers in the request language."""

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
        super().__init__(detail.render(default_translate))
        self.detail = detail
        self.status_code = status_code
        self.errors = errors or []
        self.warnings = warnings or []
        self.diagnostics = diagnostics
        self.extra = dict(extra or {})

    def render_errors(self, translate: Callable[..., str] | None = None) -> list[str]:
        return [error.render(translate or default_translate) for error in self.errors]

    def render_warnings(self, translate: Callable[..., str] | None = None) -> list[str]:
        return [warning.render(translate or default_translate) for warning in self.warnings]

    def diagnostics_payload(self, translate: Callable[..., str] | None = None) -> dict[str, list[dict[str, Any]]]:
        """Three diagnostic buckets for import-failure responses; empty buckets without diagnostic sources preserve response shape."""
        if self.diagnostics is None:
            return {"blocking": [], "auto_fixable": [], "warnings": []}
        return self.diagnostics.to_import_error_payload(translate)


def _registry_bucket_durations(
    project: dict[str, Any], generation_type: VideoGenerationType | None = None
) -> list[int] | None:
    ids = project_video_backend_ids(project, generation_type=generation_type)
    model_info = catalog.get_model(*ids) if ids is not None else None
    if model_info is None or not model_info.supported_durations:
        return None
    return list(model_info.supported_durations)


def _registry_supported_durations(project: dict[str, Any]) -> list[int] | None:
    """Full registry-declared durations for archive-reported video models; None without a declared/registered model.

    Read only project.json/registry, without capability composition or narrowing: imports run in threads
    without configuration DB sessions and cannot obtain video request facts. Use this full set only for
    legacy per-shot duration adoption. Reference units route by available images to r2v/i2v; union both
    buckets' declared sets, or None if either is unavailable, matching online content confirmation."""
    if project.get("generation_mode") != "reference_video":
        return _registry_bucket_durations(project)
    with_references = _registry_bucket_durations(project, "r2v")
    without_references = _registry_bucket_durations(project, "i2v")
    if with_references is None or without_references is None:
        return None
    return sorted(set(with_references) | set(without_references))


class ProjectArchiveService:
    # Rendered outputs (final cuts, etc.) can be rebuilt from project content; exclude them and their manifest entries so imports read missing.
    _ARCHIVE_EXCLUDED_ROOTS = frozenset({RENDERS_DIRNAME})
    _ROOT_VISIBLE_ENTRIES = frozenset(DataValidator.ALLOWED_ROOT_ENTRIES - _ARCHIVE_EXCLUDED_ROOTS)
    _AGENT_RUNTIME_EXCLUDES = frozenset({".claude", "CLAUDE.md"})

    def __init__(
        self,
        project_manager: ProjectManager,
        *,
        retire_project: RetireProject | None = None,
        formal_commit: FormalCommit | None = None,
        provider_asset_links: ProviderAssetLinkArchive | None = None,
    ):
        """``retire_project`` finalizes existing projects replaced by overwrite imports; required when overwriting.

        ``formal_commit`` is the formal imported-image entry point
        (``lib.material.formal_objects.formal_commit_on``), storing image content versions locally or remotely
        according to destination global settings; default is always local.

        ``provider_asset_links`` reads/writes archived associations (:func:`provider_asset_link_archive_on`);
        without it, export includes none and import writes none."""
        self.project_manager = project_manager
        self._retire_project = retire_project
        self._formal_commit = formal_commit
        self._provider_asset_links = provider_asset_links
        self.validator = DataValidator(projects_dir=str(project_manager.projects_dir))

    @asynccontextmanager
    async def export_materials_held(self, project_name: str, *, scope: str = "full") -> AsyncGenerator[None]:
        """Retrieve remote content-version working files needed by this archive before export, registering use within the context to prevent cleanup deletion.

        Run both export and export diagnostics inside this context: archives contain actual image content,
        retrieved and verified from object storage when working files were cleaned. Do nothing for absent
        projects, leaving export itself to report them.

        Raises:
            MaterialConnectionUnavailableError, MaterialFetchFailedError: remote content cannot be retrieved or its digest mismatches."""
        self._validate_scope(scope)
        project_dir, paths = await asyncio.to_thread(self._export_work_file_paths, project_name, scope)
        if not paths:
            yield
            return
        async with held_work_files(project_dir, paths):
            yield

    def _export_work_file_paths(self, project_name: str, scope: str) -> tuple[Path, list[str]]:
        if not self.project_manager.project_exists(project_name):
            return Path(), []
        project_dir = self.project_manager.get_project_path(project_name)
        versions = self._load_json_file(project_dir / "versions" / "versions.json") or {}
        exported = self._exported_work_files(locked_remote_work_files(project_dir), versions, scope)
        return project_dir, [work_file.path for work_file in exported]

    def _exported_work_files(
        self, work_files: list[RemoteWorkFile], versions: dict[str, Any], scope: str
    ) -> list[RemoteWorkFile]:
        """Remote-version working files for archiving: all for full archives; formal files plus retained snapshots for current-only exports."""
        if scope == "full":
            return work_files
        _, retained = self._current_export_versions(versions)
        return [work_file for work_file in work_files if work_file.formal or work_file.path in retained]

    def _verify_remote_content(self, snapshot_dir: Path, scope: str) -> None:
        """Snapshots must contain actual content for every archived remote version; absent/mismatched content prevents archive creation.

        Validate version snapshots by recorded digest. Formal files need only exist, taking current disk content
        as authoritative, matching local-version export."""
        versions = self._load_json_file(snapshot_dir / "versions" / "versions.json") or {}
        project = self._load_json_file(snapshot_dir / self.project_manager.PROJECT_FILE) or {}
        for work_file in self._exported_work_files(remote_work_files(versions, project), versions, scope):
            path = snapshot_dir / work_file.path
            if not path.is_file():
                raise MaterialFetchFailedError("work_file_missing")
            if not work_file.formal and sha256_file(path) != work_file.digest:
                raise MaterialFetchFailedError("content_mismatch")

    @staticmethod
    def _drop_remote_locators(payload: dict[str, Any]) -> bool:
        """Remove version-record remote locators; return whether changed.

        Locations matter only within their originating instance: exports exclude source connections/buckets,
        and imports never use packaged locators to read destination buckets."""
        changed = False
        for _resource_type, _resource_id, record in iter_version_records(payload):
            if STORAGE in record:
                del record[STORAGE]
                changed = True
        return changed

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
            archived_links = self._archived_links(project_name, snapshot_dir, scope)
            if archived_links:
                manifest[ARCHIVED_LINKS_KEY] = [link.to_payload() for link in archived_links]
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
            archive_path.unlink(
                missing_ok=True
            )  # hard-delete: temp -- Partially produced archive after export failure.
            raise
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

        download_name = f"{project_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        return archive_path, download_name

    def _archived_links(self, project_name: str, snapshot_dir: Path, scope: str) -> list[ArchivedLink]:
        """Provider associations to archive: usable associations for each packaged record's content version, matching its digest.

        Current-only archives trim history by ``CURRENT_EXPORT_VERSION_RETENTION``; excluded records carry no associations."""
        if self._provider_asset_links is None:
            return []
        versions = self._load_json_file(snapshot_dir / "versions" / "versions.json") or {}
        if scope == "current":
            versions, _ = self._current_export_versions(versions)
        content_digests = {
            record[CONTENT_VERSION_ID]: record[CONTENT_DIGEST]
            for resource_type, _resource_id, record in iter_version_records(versions)
            if resource_type in UPLOADABLE_RESOURCE_TYPES
            and isinstance(record.get(CONTENT_VERSION_ID), str)
            and isinstance(record.get(CONTENT_DIGEST), str)
        }
        return self._provider_asset_links.export(project_name, content_digests) if content_digests else []

    def _verified_archived_links(
        self, project_dir: Path, manifest: dict[str, Any] | None, project_name: str
    ) -> list[tuple[ArchivedLink, LinkHolder]]:
        """Packaged associations whose content identity/digest match staging version records and actual snapshot content.

        Discard mismatches without blocking image import: associations prove content corresponds to provider
        assets, which is no longer true after content changes."""
        raw = (manifest or {}).get(ARCHIVED_LINKS_KEY)
        if self._provider_asset_links is None or not isinstance(raw, list) or not raw:
            return []
        versions = self._load_json_file(project_dir / "versions" / "versions.json") or {}
        records: dict[str, tuple[str, str, dict[str, Any]]] = {}
        for resource_type, resource_id, record in iter_version_records(versions):
            content_version_id = record.get(CONTENT_VERSION_ID)
            if resource_type in UPLOADABLE_RESOURCE_TYPES and isinstance(content_version_id, str):
                records.setdefault(content_version_id, (resource_type, resource_id, record))
        snapshot_digests: dict[Path, str] = {}
        verified: list[tuple[ArchivedLink, LinkHolder]] = []
        for item in raw:
            link = ArchivedLink.from_payload(item)
            found = records.get(link.content_version_id) if link is not None else None
            if link is None or found is None:
                continue
            resource_type, resource_id, record = found
            snapshot = try_safe_join(project_dir, record["file"]) if isinstance(record.get("file"), str) else None
            if (
                record.get(CONTENT_DIGEST) != link.content_digest
                or type(record.get("version")) is not int
                or snapshot is None
                or not snapshot.is_file()
            ):
                continue
            if snapshot not in snapshot_digests:
                snapshot_digests[snapshot] = sha256_file(snapshot)
            if snapshot_digests[snapshot] != link.content_digest:
                continue
            holder = LinkHolder(
                project_name=project_name,
                content_version_id=link.content_version_id,
                content_digest=link.content_digest,
                resource_type=resource_type,
                resource_id=resource_id,
                version=record["version"],
                locator_path=record["file"],
            )
            verified.append((link, holder))
        if len(verified) < len(raw):
            logger.info("导入包随带的供应商素材关联有 %d 条与包内图片对不上，已丢弃", len(raw) - len(verified))
        return verified

    def _restore_archived_links(
        self, links: list[tuple[ArchivedLink, LinkHolder]], diagnostics: ArchiveDiagnostics
    ) -> None:
        """Persist verified associations after installation. Failure does not undo import: images remain usable, and generation sends image content without associations."""
        if not links or self._provider_asset_links is None:
            return
        try:
            self._provider_asset_links.restore(links)
        except Exception:
            logger.exception("导入项目的供应商素材关联写入失败")
            diagnostics.add(
                "warnings",
                "provider_asset_links_not_restored",
                ValidationMessage("arch_provider_asset_links_not_restored", {"count": len(links)}),
            )

    @staticmethod
    def _raise_artifact_activation_validation_error(
        diagnostics: ArchiveDiagnostics,
        cause: Exception,
    ) -> NoReturn:
        diagnostics.add(
            "blocking",
            "artifact_activation_failed",
            ValidationMessage("arch_artifact_activation_failed", {"reason": str(cause)}),
            location=_failure_location(cause),
        )
        raise ProjectArchiveValidationError(
            ValidationMessage("arch_import_validation_failed"),
            errors=diagnostics.blocking_messages(),
            warnings=diagnostics.warning_messages(),
            diagnostics=diagnostics,
        ) from cause

    def _ledger_titles(self, project_dir: Path) -> list[dict[str, Any]]:
        project = self._load_json_file(project_dir / self.project_manager.PROJECT_FILE) or {}
        episodes = project.get("episodes")
        return [
            {"episode": entry["episode"], "title": entry.get("title") if isinstance(entry.get("title"), str) else ""}
            for entry in (episodes if isinstance(episodes, list) else [])
            if isinstance(entry, dict) and parse_positive_episode_num(entry.get("episode")) is not None
        ]

    @staticmethod
    def _raise_migration_validation_error(diagnostics: ArchiveDiagnostics, cause: Exception) -> NoReturn:
        """Archive data upgrade could not finish: reject import with reasons; discard staging with the temporary directory.

        Rejected data (unreadable/structurally invalid) returns validation failure. Incomplete upgrades
        (disk/write/program defects) also include reasons, but return server errors."""
        detail = migration_failure_details(cause)[0]
        diagnostics.add(
            "blocking",
            "project_migration_failed",
            ValidationMessage("arch_project_migration_failed", {"reason": detail.violation}),
            location=_failure_location(cause),
        )
        data_rejected = isinstance(cause, _ARTIFACT_ACTIVATION_ERRORS) and not isinstance(cause, OSError)
        raise ProjectArchiveValidationError(
            ValidationMessage("arch_import_validation_failed"),
            status_code=400 if data_rejected else 500,
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
        confirm_report: bool = False,
        translate: Callable[..., str] | None = None,
    ) -> ProjectImportResult:
        """Import an archive. Nonempty migration reports with false ``confirm_report`` return 409 plus report, leaving no project data."""
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

                    # Validate version history before repair/migration, so later versions.json readers see only validated snapshot paths.
                    self._confine_version_history(staging_dir)
                    diagnostics = self._repair_project_tree(staging_dir)
                    # Run the full migration chain on staging before validation (normalize legacy providers / split image_backend /
                    # reencode generation mode). Startup run_project_migrations covers only then-existing projects; poststartup imports
                    # need migration here or resolution ignores legacy fields and silently falls back to global defaults. Validators assume
                    # latest schemas (e.g. required binary generation_mode), so unmigrated archives would be falsely refused. Before installation
                    # means migration errors discard TemporaryDirectory staging without half-migrated dirty project directories,
                    # avoiding rollback of persisted installation.
                    # Encoding migration precedes schema migration: normalize sources to UTF-8 so every subsequent UTF-8 source reader
                    # receives consistent input. Conversion failure means the file itself cannot decode (no path can read it);
                    # report import warnings rather than abort, because local corruption must not block the entire project.
                    encoding_summary = migrate_project_source_encoding(staging_dir)
                    for failed_name in encoding_summary.failed:
                        diagnostics.add(
                            "warnings",
                            "source_encoding_unconverted",
                            ValidationMessage("arch_source_encoding_unconverted", {"name": failed_name}),
                        )
                    # Artifact Manifest is a hidden sidecar, excluded from archive members. Official exports put complete
                    # claim snapshots into visible envelopes; old manual archives lacking them
                    # still use self-proving reconstruction. Before migration, materialize envelope claims into staging manifests,
                    # allowing migrations to transform them as for in-place upgrades (e.g. v18→v19 storyboard/grid basis changes),
                    # then restore at activation according to content evidence.
                    try:
                        preserved_manifest = (
                            decode_artifact_manifest_payload(manifest["artifact_manifest"])
                            if isinstance(manifest, dict) and "artifact_manifest" in manifest
                            else None
                        )
                        staged = preserved_manifest is not None and stage_preserved_artifact_claims(
                            staging_dir, preserved_manifest
                        )
                    except _ARTIFACT_ACTIVATION_ERRORS as exc:
                        self._raise_artifact_activation_validation_error(diagnostics, exc)
                    try:
                        migrate_project_dir(staging_dir)
                    except Exception as exc:
                        self._raise_migration_validation_error(diagnostics, exc)
                    if preserved_manifest is not None and staged:
                        try:
                            preserved_manifest = take_migrated_artifact_claims(staging_dir, preserved_manifest)
                        except _ARTIFACT_ACTIVATION_ERRORS as exc:
                            self._raise_artifact_activation_validation_error(diagnostics, exc)
                    # Heal mentions after migration: legacy body text is formed by migration and is unreadable earlier.
                    self._repair_unit_mentions_tree(staging_dir, diagnostics)
                    diagnostics.extend_validation(self.validator.validate_project_tree(staging_dir))
                    if diagnostics.blocking:
                        raise ProjectArchiveValidationError(
                            ValidationMessage("arch_import_validation_failed"),
                            errors=diagnostics.blocking_messages(),
                            warnings=diagnostics.warning_messages(),
                            diagnostics=diagnostics,
                        )
                    # Paths with/without envelopes both commit once in staging.
                    try:
                        import_skipped = ensure_imported_artifact_target_state(
                            staging_dir,
                            preserved_manifest=preserved_manifest,
                        )
                    except _ARTIFACT_ACTIVATION_ERRORS as exc:
                        self._raise_artifact_activation_validation_error(diagnostics, exc)

                    # Include repair placeholders and activation-unregistered artifacts with migration results in reports, independent of archive
                    # schema versions. Ask users for one preimport confirmation (ADR 0098). Move placeholders from diagnostics into reports
                    # so successful-import diagnostics do not repeat them.
                    extend_migration_report(
                        staging_dir,
                        adjusted=_take_placeholder_adjustments(diagnostics),
                        skipped=import_skipped,
                    )
                    report = visible_migration_report(staging_dir)
                    if report is not None:
                        if not confirm_report:
                            raise ProjectArchiveValidationError(
                                ValidationMessage("arch_import_confirmation_required"),
                                status_code=409,
                                warnings=diagnostics.warning_messages(),
                                diagnostics=diagnostics,
                                extra={
                                    "confirmation_required": True,
                                    "migration_report": report.model_dump(mode="json"),
                                    # Reports identify episodes by ID; confirmation dialogs display names from those IDs.
                                    "episodes": self._ledger_titles(staging_dir),
                                },
                            )
                        # Users already confirmed reports before importing; do not repeat them on project pages.
                        acknowledge_migration_report(staging_dir)

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
                    # Installation moves staging: verify associations against actual packaged content first, then persist after installation.
                    archived_links = self._verified_archived_links(staging_dir, manifest, target_name)

                    commit = (
                        self._formal_commit(target_name)
                        if self._formal_commit is not None and self._has_importable_images(staging_dir)
                        else nullcontext(None)
                    )
                    with commit as storage:
                        if storage is not None:
                            # Current-only archives omit generated-image records; register content versions before saving by destination storage.
                            self._track_current_images(staging_dir)
                            # Save before installation: failure leaves nothing installed, with settlement compensation deleting newly created objects.
                            try:
                                self._store_imported_images(staging_dir, storage)
                            except RemoteSaveFailedError as exc:
                                # New imports have no previous version; describe imported results with no project writes.
                                raise BadGatewayError("archive_import_remote_save_failed", **exc.params) from exc
                            storage.track(self.project_manager.projects_dir / target_name)
                        self._install_project_dir(
                            staging_dir,
                            target_name,
                            overwrite=(conflict_policy == "overwrite"),
                        )
                    # Overwrite retirement moved old-project associations to tombstone names; new associations belong only to imported projects.
                    self._restore_archived_links(archived_links, diagnostics)

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
        try:
            self._verify_remote_content(snapshot_dir, scope)
        except BaseException:
            temp_dir.cleanup()
            raise
        snapshot_versions_path = snapshot_dir / "versions" / "versions.json"
        snapshot_versions = self._load_json_file(snapshot_versions_path)
        if isinstance(snapshot_versions, dict) and self._drop_remote_locators(snapshot_versions):
            self._write_json_file(snapshot_versions_path, snapshot_versions)

        diagnostics = self._repair_project_tree(snapshot_dir)
        # Source projects already use current schema/body text; share import's mention-healing pass.
        self._repair_unit_mentions_tree(snapshot_dir, diagnostics)
        diagnostics.extend_validation(self.validator.validate_project_tree(snapshot_dir))

        # Collect nonstandard source top-level entries in diagnostics, even when filtered from export.
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
                    # Current-only archives carry only selected-version snapshots (CURRENT_EXPORT_VERSION_RETENTION).
                    ships_unselected_versions=scope == "full",
                )
            )
        manifest = self._build_archive_manifest(
            project_name,
            snapshot_project,
            scope=scope,
            # Packaged diagnostic snapshots are distributed data, rendered in the default language since importer/exporter languages may differ;
            # request-specific rendering occurs only at router boundaries.
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
            trimmed_versions, retained_version_files = self._current_export_versions(
                payload if isinstance(payload, dict) else {}
            )

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
            in_version_history = is_current and relative_dir.parts[:1] == ("versions",)
            if in_version_history:
                prefix = relative_dir.as_posix() + "/"
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
            if in_version_history:
                visible_files = [
                    name
                    for name in visible_files
                    if (relative_dir / name).as_posix() in retained_version_files
                    or (relative_dir.parts == ("versions",) and name == "versions.json")
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

    @staticmethod
    def _current_export_versions(payload: dict[str, Any]) -> tuple[dict[str, Any], frozenset[str]]:
        """Trim ``versions.json`` for a current-only export and name the snapshots that ship with it.

        Each bucket follows ``CURRENT_EXPORT_VERSION_RETENTION``; buckets of other
        resource types, and buckets that are not objects, are dropped.  Every retained record is the selected one and
        its managed snapshot is the only history file packed for that resource.
        """

        trimmed: dict[str, Any] = {}
        snapshots: set[str] = set()
        for resource_type, bucket in payload.items():
            retention = CURRENT_EXPORT_VERSION_RETENTION.get(resource_type, CurrentExportVersionRetention.NONE)
            if retention is CurrentExportVersionRetention.MANUAL_UPLOAD:
                if not isinstance(bucket, dict):
                    continue
                uploads: dict[str, Any] = {}
                for resource_id, resource_info in bucket.items():
                    snapshot = selected_manual_upload_snapshot(resource_info, resource_type)
                    if snapshot is None:
                        continue
                    upload = json.loads(json.dumps(resource_info))
                    upload["versions"] = [
                        record
                        for record in upload["versions"]
                        if isinstance(record, dict)
                        and record.get("version") == upload["current_version"]
                        and record.get("file") == snapshot
                    ]
                    uploads[resource_id] = upload
                    snapshots.add(snapshot)
                if uploads:
                    trimmed[resource_type] = uploads
            elif retention is CurrentExportVersionRetention.SELECTED:
                if not isinstance(bucket, dict):
                    continue
                selected_bucket = json.loads(json.dumps(bucket))
                trimmed[resource_type] = selected_bucket
                for resource_info in selected_bucket.values():
                    if not isinstance(resource_info, dict):
                        continue
                    current_ver = resource_info.get("current_version")
                    records = resource_info.get("versions", [])
                    if not isinstance(records, list):
                        continue
                    if current_ver is not None:
                        records = [
                            record
                            for record in records
                            if isinstance(record, dict) and record.get("version") == current_ver
                        ]
                        resource_info["versions"] = records
                    if (
                        len(records) == 1
                        and isinstance(records[0], dict)
                        and VersionManager.is_managed_snapshot_path(resource_type, records[0].get("file"))
                    ):
                        snapshots.add(records[0]["file"])
        return trimmed, frozenset(snapshots)

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
                    shutil.rmtree(target_dir)  # hard-delete: temp -- Directory snapshot for export.
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
            shutil.rmtree(target_dir)  # hard-delete: temp -- Directory snapshot for export.
        raise ArtifactManifestError("project changed repeatedly while creating an archive snapshot") from last_missing

    def _source_manifest_entries(
        self,
        source_dir: Path,
    ) -> dict[ArtifactKey, ArtifactManifestEntry] | None:
        """Complete source-project claim snapshot; ``None`` for projects not yet in the manifest system.

        This gate asks whether a project has a manifest to preserve, not how artifacts are read, sharing the
        envelope-writing criterion. Unadopted projects necessarily have empty manifests, indistinguishable
        from projects with no artifacts in envelopes. Seeing an envelope, import preserves it verbatim and
        would mark all generated artifacts missing, forcing paid regeneration. Omit envelopes for these
        projects; import still migrates and reconstructs self-proving claims."""

        project = self._load_json_file(source_dir / self.project_manager.PROJECT_FILE)
        if not isinstance(project, dict) or not project_schema_is_current(project):
            return None
        return {
            key: entry
            for key, entry in ProjectArtifactManifestAdapter(source_dir).snapshot_entries().items()
            if entry.artifact_path.split("/", 1)[0] not in self._ARCHIVE_EXCLUDED_ROOTS
        }

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
                # Unlike reference_image, reference_audio retains extensions without transcoding; derive them from
                # current field values, matching uploads (characters/refs_audio/{name}<ext>).
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
                        # Ledger script_file is a forward-looking contract (actual paths filled on script generation);
                        # splitting before script existence is expected and must not block archive roundtrips. Ignore ledger_status:
                        # v2→v3 no longer backfills it, so upgraded legacy entries may never
                        # have ledger_status; valid shape is sufficient.
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

        # Strip obsolete episode-level aggregates.
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

        # Preserve script-stamp → project-declaration fallback, removing terminal narration defaults. Project content_mode
        # is required/validated; absent or nonstring values in both places mean corruption. Fail loudly without silently choosing drama.
        # Do not normalize via str(...), turning missing None into literal "None": this gives reference branches
        # false values bypassing fail-loud checks and distorts nonreference error semantics.
        raw_content_mode = script_payload.get("content_mode") or project_payload.get("content_mode")
        if not isinstance(raw_content_mode, str):
            raise ValueError(f"未知或缺失 content_mode: {raw_content_mode!r}")
        content_mode = raw_content_mode
        generation_mode = project_payload.get("generation_mode")

        # Dispatch repair by canonically resolved skeleton: all reference-to-video uses video_units, while storyboard
        # image-to-video uses segments/scenes/shots by content mode.
        kind = resolve_declared_kind(content_mode, generation_mode)

        # video_units organizes assets through references, structurally distinct from
        # storyboard characters/scenes/props; use its dedicated repair branch.
        if kind == "video_units":
            units_changed = self._repair_video_units_payload(
                project_dir,
                script_path_rel=script_path_rel,
                script_payload=script_payload,
                project_payload=project_payload,
                content_mode=content_mode,
                versions_payload=versions_payload,
                diagnostics=diagnostics,
            )
            return script_changed or units_changed, project_changed

        # Complete fields/backfill assets per storyboard item (segments/scenes/shots, including ad shots).
        items_key = kind
        raw_items, id_field, _kind = resolve_kind_items(script_payload, kind=kind)
        chars_field = SKELETONS[kind].chars_field
        # Storyboard skeletons always have chars_field; video_units already returned above.
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
                for name in refs:
                    if isinstance(name, str) and self._add_placeholder_asset(
                        project_payload, pool, asset_type, name, diagnostics
                    ):
                        project_changed = True

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
        """Fill missing item.generated_assets fields, returning (assets, changed)."""
        assets = item.get("generated_assets")
        changed = False
        if isinstance(assets, dict):
            template = self.project_manager.create_generated_assets(content_mode)
            missing_keys = [key for key in template if key not in assets]
            if missing_keys:
                for key in missing_keys:
                    assets[key] = template[key]
                changed = True
                # Diagnose only non-None filled values, avoiding no-op completion noise.
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
            # None means absent; other nondicts (list/str, etc.) mean external-edit corruption. Reset both to template structure,
            # distinguishing only in diagnostics so importers know completion versus discarded dirt.
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
        """Add placeholder definitions for missing character references; return whether project_payload changed.

        Membership uses :func:`_resolve_existing_asset`, preventing equivalent encodings of registered names
        from creating second placeholders whose last-write-wins behavior would overwrite real sheet/voice metadata."""
        if _resolve_existing_asset(character_name, project_characters) in project_characters:
            return False
        project_payload.setdefault("characters", {})
        if not isinstance(project_payload.get("characters"), dict):
            return False
        # Placeholder characters have no descriptions: users supply generation-input descriptions or upload sheets directly.
        project_payload["characters"][character_name] = {"description": ""}
        project_characters.add(character_name)
        diagnostics.add(
            "auto_fixed",
            "placeholder_character_added",
            ValidationMessage("arch_placeholder_character_added", {"name": character_name}),
            location=f"characters[{character_name}]",
        )
        return True

    @staticmethod
    def _add_placeholder_asset(
        project_payload: dict[str, Any],
        pool: set[str],
        asset_type: str,
        name: str,
        diagnostics: ArchiveDiagnostics,
    ) -> bool:
        """Add description-free placeholders for undefined scene/prop references; return whether project_payload changed."""
        if _resolve_existing_asset(name, pool) in pool:
            return False
        bucket_key = ASSET_SPECS[asset_type].bucket_key
        project_payload.setdefault(bucket_key, {})
        if not isinstance(project_payload.get(bucket_key), dict):
            return False
        project_payload[bucket_key][name] = build_asset_entry(asset_type, "")
        pool.add(name)
        diagnostics.add(
            "auto_fixed",
            f"placeholder_{asset_type}_added",
            ValidationMessage(
                "arch_placeholder_asset_added",
                {"asset_type": MessageRef(f"asset_type_{asset_type}"), "name": name},
            ),
            location=f"{bucket_key}[{name}]",
        )
        return True

    def _repair_video_units_payload(
        self,
        project_dir: Path,
        *,
        script_path_rel: str,
        script_payload: dict[str, Any],
        project_payload: dict[str, Any],
        content_mode: str,
        versions_payload: dict[str, Any],
        diagnostics: ArchiveDiagnostics,
    ) -> bool:
        """Repair reference-to-video video_units, returning script_changed.

        Unit references are not persisted; body text is authoritative. Touch only structure/artifact fields:
        per-unit duration adoption, generated_assets completion, video_clip/video_thumbnail path normalization,
        and history reconstruction. Body ``@[名称]`` self-healing uses :meth:`_repair_unit_mentions_tree`
        after schema migration converts legacy ``shots`` into readable body text. video_uri is a remote URL,
        not a local path (otherwise canonical local files with matching names would overwrite it)."""
        raw_units = script_payload.get("video_units")
        if not isinstance(raw_units, list):
            return False

        # Legacy archives may retain preadoption shapes (durations on shots, missing unit duration_seconds);
        # downstream DataValidator requires unit-level duration_seconds within structural ranges.
        # Repair runs migration before validation, here before validate_project_tree; callers persist script_changed results
        # together with other repairs in one write.
        # Resolve full registry-declared tiers by project.json self-reported identity (no DB access: imports run in
        # to_thread, and custom-provider credentials/capabilities may not yet exist locally, preventing video-request-fact evaluation).
        # Missing tiers (undeclared model/unregistered custom provider) yield None and structural-range clamps;
        # subsequent preflight/execution tier selection handles drift.
        # Normalize providers on a copy first: this precedes migrate_project_dir, and archives may retain
        # legacy aliases (e.g. gemini/…). Missed registry lookups would lose tier selection, and idempotent migration
        # leaves no later chance after normalization. Pure/idempotent normalization does not affect later formal migration.
        normalized_project = normalize_legacy_providers(project_payload)
        migrated, migration_warnings = migrate_unit_durations(
            raw_units, supported_durations=_registry_supported_durations(normalized_project)
        )
        changed = migrated
        for message in migration_warnings:
            # Migration messages omit script paths; location carries provenance so templates work across call chains.
            diagnostics.add(
                "warnings",
                "reference_video_duration_migrated",
                message,
                location=script_path_rel,
            )

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

            if not (isinstance(assets, dict) and resource_id):
                continue

            # video_clip has version history, allowing materialization of current files from versions/.
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

            # Thumbnails lack history; normalize paths only when canonical files exist.
            if self._repair_path_to_canonical(
                project_dir,
                assets,
                field_name="video_thumbnail",
                canonical_rel=f"reference_videos/thumbnails/{resource_id}.jpg",
                location=f"{location_prefix}.generated_assets.video_thumbnail",
                diagnostics=diagnostics,
            ):
                changed = True

        return changed

    def _repair_unit_mentions_tree(self, project_dir: Path, diagnostics: ArchiveDiagnostics) -> None:
        """Scan all video_units bodies after migration: add missing-speaker placeholders, warning only for other unresolved mentions.

        Run **after** :func:`migrate_project_dir`: legacy units keep content in ``shots``, and migration forms
        the body. Running earlier heals empty text, producing neither placeholders nor diagnostics.
        This pass changes only ``project.json`` (character placeholders), never scripts."""
        project_path = project_dir / self.project_manager.PROJECT_FILE
        project = self._load_json_file(project_path)
        if project is None:
            return
        pools = {
            key: {name for name, payload in (project.get(key) or {}).items() if isinstance(payload, dict)}
            if isinstance(project.get(key), dict)
            else set[str]()
            for key in ("characters", "scenes", "props", "products")
        }
        episodes = project.get("episodes")
        if not isinstance(episodes, list):
            return

        project_changed = False
        for episode_meta in episodes:
            if not isinstance(episode_meta, dict):
                continue
            script_file = episode_meta.get("script_file")
            if not isinstance(script_file, str) or not script_file.strip():
                continue
            script_path = try_safe_join(project_dir, script_file)
            if script_path is None or not script_path.is_file():
                continue
            script_payload = self._load_json_file(script_path)
            if script_payload is None:
                continue
            raw_units = script_payload.get("video_units")
            if not isinstance(raw_units, list):
                continue
            script_path_rel = script_file.replace("\\", "/")
            for index, unit in enumerate(raw_units):
                if not isinstance(unit, dict):
                    continue
                if self._repair_unit_mentions(
                    unit,
                    project_payload=project,
                    project_characters=pools["characters"],
                    project_scenes=pools["scenes"],
                    project_props=pools["props"],
                    project_products=pools["products"],
                    index=index,
                    location_prefix=f"{script_path_rel}:video_units[{index}]",
                    diagnostics=diagnostics,
                ):
                    project_changed = True

        if project_changed:
            self._write_json_file(project_path, project)

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
        """Heal ``@[名称]`` in video_unit bodies: add missing-speaker placeholders; warn only for other unresolved mentions.

        Body text alone is authoritative; references resolve at execution. Unresolved mentions mean only no
        reference image here and do not block import. Speakers are the exception: ``@[角色]{台词}`` syntax
        establishes a character; missing definitions lose dialogue voice bindings, so add placeholders like narration/drama.

        Names and registered keys may use NFC/NFD; always test membership through
        :func:`_resolve_existing_asset`, as narration/drama do.

        Return whether character placeholders were added (whether ``project_payload`` changed)."""
        text = unit.get("text")
        if not isinstance(text, str) or not text.strip():
            return False

        project_changed = False
        # Add speakers first: ``extract_mentions`` intentionally excludes speaker positions inside speech markers (speakers are not image references),
        # so characters appearing only before ``{}`` are absent from mention lists; extract speakers separately for placeholders.
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
            ) and self._materialize_current_file(
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
            if version_rel and self._materialize_current_file(
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
        candidates: list[str] = [
            candidate.relative_to(project_dir).as_posix()
            for candidate in sorted(version_dir.iterdir(), key=lambda path: path.name)
            if candidate.is_file() and candidate.name.startswith(prefix) and candidate.suffix == extension
        ]

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

    def _confine_version_history(self, project_dir: Path) -> None:
        """Keep only typed history buckets and require every record to name a managed snapshot.

        Buckets of resource types this version does not know (such as ``clues``
        left behind by the v0→v1 migration) are dropped before installation.  In a
        typed bucket, a malformed resource entry or history, or a single record
        that is not an object naming a managed snapshot path, rejects the whole
        package.
        """

        versions_path = project_dir / "versions" / "versions.json"
        if not versions_path.is_file():
            return
        payload = self._load_json_file(versions_path)
        if not isinstance(payload, dict):
            return

        unknown = [key for key in payload if key not in VersionManager.RESOURCE_TYPES]
        for key in unknown:
            records = sum(len(history) for _, history in self._iter_version_histories(payload.pop(key)))
            logger.info("导入包的版本历史含未知资源类型桶 %r（%d 条版本记录），已剔除", key, records)

        errors: list[ValidationMessage] = []
        for resource_type, bucket in payload.items():
            if not isinstance(bucket, dict):
                errors.append(ValidationMessage("arch_version_history_malformed", {"location": resource_type}))
                continue
            for resource_id, info in bucket.items():
                location = f"{resource_type}/{resource_id}"
                history = info.get("versions", []) if isinstance(info, dict) else None
                if not isinstance(history, list) or not all(isinstance(record, dict) for record in history):
                    errors.append(ValidationMessage("arch_version_history_malformed", {"location": location}))
                elif not all(
                    VersionManager.is_managed_snapshot_path(resource_type, record.get("file")) for record in history
                ):
                    errors.append(ValidationMessage("arch_version_snapshot_path_unmanaged", {"location": location}))
        if errors:
            raise ProjectArchiveValidationError(ValidationMessage("arch_import_validation_failed"), errors=errors)
        # Strip every packaged remote locator: imported images rely only on actual packaged content, resaved under destination settings.
        dropped_locators = self._drop_remote_locators(payload)
        if unknown or dropped_locators:
            self._write_json_file(versions_path, payload)

    def _importable_image_records(self, project_dir: Path) -> tuple[dict[str, Any], list[tuple[dict[str, Any], Path]]]:
        """Staging version history plus image-version records/snapshot files eligible for object storage."""
        payload = self._load_json_file(project_dir / "versions" / "versions.json")
        if not isinstance(payload, dict):
            return {}, []
        records = []
        for resource_type, _resource_id, record in iter_version_records(payload):
            if not stores_remotely(resource_type) or not isinstance(record.get("file"), str):
                continue
            snapshot = try_safe_join(project_dir, record["file"])
            if snapshot is not None and snapshot.is_file():
                records.append((record, snapshot))
        return payload, records

    def _has_importable_images(self, project_dir: Path) -> bool:
        return bool(self._importable_image_records(project_dir)[1] or self._untracked_current_images(project_dir))

    def _untracked_current_images(self, project_dir: Path) -> list[tuple[str, str, Path]]:
        """Current formal staging images lacking version records: ``(资源类型, 资源 ID, 文件)``.

        Current-only archives trim generated-image records using ``CURRENT_EXPORT_VERSION_RETENTION``
        and carry only formal files. Asset sheets follow project.json image fields; other images use canonical
        paths. End frames bypass storage selection and are excluded."""
        payload = self._load_json_file(project_dir / "versions" / "versions.json")
        payload = payload if isinstance(payload, dict) else {}
        project = self._load_json_file(project_dir / self.project_manager.PROJECT_FILE)
        project = project if isinstance(project, dict) else {}

        candidates: dict[tuple[str, str], str] = {}
        for spec in ASSET_SPECS.values():
            bucket = project.get(spec.bucket_key)
            for name, entry in bucket.items() if isinstance(bucket, dict) else ():
                sheet = entry.get(spec.sheet_field) if isinstance(entry, dict) else None
                if isinstance(sheet, str) and sheet:
                    candidates[(spec.bucket_key, str(name))] = sheet
        for resource_type in ("storyboards", "grids", CHARACTER_DERIVATIVE_RESOURCE_TYPE):
            segments = resource_id_segments(resource_type)
            sample = Path(resource_relative_path(resource_type, "/".join(["x"] * segments)))
            subdir = project_dir / sample.parents[segments - 1]
            for path in sorted(subdir.rglob(f"*{resource_extension(resource_type)}")) if subdir.is_dir() else ():
                rel = path.relative_to(project_dir).as_posix()
                resource = resource_of_path(rel)
                if resource is not None and resource[0] == resource_type:
                    candidates.setdefault(resource, rel)

        result: list[tuple[str, str, Path]] = []
        for (resource_type, resource_id), rel in candidates.items():
            if not stores_remotely(resource_type) or resource_type == END_FRAME_RESOURCE_TYPE:
                continue
            bucket = payload.get(resource_type)
            history = bucket.get(resource_id) if isinstance(bucket, dict) else None
            if isinstance(history, dict) and history.get("versions"):
                continue
            path = try_safe_join(project_dir, rel)
            if path is not None and path.is_file() and not path.is_symlink():
                result.append((resource_type, resource_id, path))
        return result

    def _track_current_images(self, project_dir: Path) -> None:
        """Register a content version for current formal images lacking records (as ``VersionManager.ensure_current_tracked`` does).

        Only when destination settings store new images remotely; local imports still skip registration,
        preserving current-only scope semantics."""
        versions = VersionManager(project_dir)
        for resource_type, resource_id, path in self._untracked_current_images(project_dir):
            versions.ensure_current_tracked(resource_type, resource_id, path, prompt="")

    def _store_imported_images(self, project_dir: Path, storage: ObjectContentStorage) -> None:
        """Save staging image versions as destination formal objects and record locators on their version records.

        Backfill identity/digest on records predating content-version identities (as
        ``VersionManager.ensure_content_version`` does). If snapshot content mismatches recorded digest,
        keep that version local, matching local-import results. Identical-byte versions share one object saved once."""
        payload, records = self._importable_image_records(project_dir)
        stored: dict[tuple[str, str], ObjectLocator] = {}
        for record, snapshot in records:
            digest = sha256_file(snapshot)
            if not isinstance(record.get(CONTENT_VERSION_ID), str):
                record[CONTENT_VERSION_ID] = new_content_version_id()
            if not isinstance(record.get(CONTENT_DIGEST), str):
                record[CONTENT_DIGEST] = digest
            if record[CONTENT_DIGEST] != digest:
                continue
            key = (digest, snapshot.suffix.lower())
            if key not in stored:
                stored[key] = storage.store(snapshot, digest)
            record[STORAGE] = stored[key].to_record()
        if records:
            self._write_json_file(project_dir / "versions" / "versions.json", payload)

    @staticmethod
    def _iter_version_histories(bucket: object) -> Iterator[tuple[str, list[Any]]]:
        """Yield ``(resource_id, versions)`` for well-formed entries of one versions.json bucket.

        Only used to count records of dropped buckets; typed buckets are validated strictly.
        """

        if not isinstance(bucket, dict):
            return
        for resource_id, info in bucket.items():
            history = info.get("versions") if isinstance(info, dict) else None
            if isinstance(history, list):
                yield str(resource_id), history

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
            if child.name in self._AGENT_RUNTIME_EXCLUDES or child.name in self._ARCHIVE_EXCLUDED_ROOTS:
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
        """Archive reads/writes permit only project or system-temporary directories; return None outside boundaries."""
        for base in (self.project_manager.projects_dir, tempfile.gettempdir()):
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
        with open(real, "w", encoding="utf-8") as handle:
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
        target_dir = self.project_manager.projects_dir / preferred_name
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
        target_dir = self.project_manager.projects_dir / project_name
        prepared_dir = target_dir.with_name(f".import-staging-{target_dir.name}-{secrets.token_hex(4)}")

        try:
            shutil.move(str(staging_dir), str(prepared_dir))
            # Profile sync is part of installation, performed beside the project directory; failure leaves existing projects/records untouched.
            self.project_manager.sync_agent_profile(prepared_dir)
            if overwrite and target_dir.exists():
                self._replace_project_dir(prepared_dir, target_dir)
            else:
                prepared_dir.rename(target_dir)
        finally:
            if prepared_dir.exists():
                shutil.rmtree(prepared_dir, ignore_errors=True)  # hard-delete: temp -- Import staging directory.

    def _replace_project_dir(self, prepared_dir: Path, target_dir: Path) -> None:
        """Overwrite import equals deleting the existing project then importing: finalize old records as deletion,
        with the old directory and project memory becoming a deleted project.

        Finalization uses only two renames, keeping DB transaction duration independent of project size.
        Directory-replacement or record-commit failures roll back records and restore the old directory
        unchanged, also by rename, avoiding incomplete new-directory deletion stranding it among deleted projects."""
        if self._retire_project is None:
            raise RuntimeError("覆盖导入需要收尾现有项目的记录")
        deleted_dir: Path | None = None

        try:
            with self._retire_project(target_dir) as tombstone:
                deleted_dir = deleted_projects.move_in(self.project_manager.layout, target_dir, tombstone)
                prepared_dir.rename(target_dir)
        except BaseException:
            if deleted_dir is not None and deleted_dir.exists():
                if target_dir.exists():
                    discarded_dir = target_dir.with_name(f".import-discard-{target_dir.name}-{secrets.token_hex(4)}")
                    target_dir.rename(discarded_dir)
                    # hard-delete: rollback -- Remove newly installed imports if swapping fails.
                    shutil.rmtree(discarded_dir, ignore_errors=True)
                deleted_dir.rename(target_dir)
            raise
