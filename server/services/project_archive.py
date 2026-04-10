from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from lib.data_validator import DataValidator, ValidationResult
from lib.project_change_hints import emit_project_change_hint
from lib.project_manager import ProjectManager

logger = logging.getLogger(__name__)

ARCHIVE_MANIFEST_NAME = "arcreel-export.json"
ARCHIVE_FORMAT_VERSION = 2
ARCHIVE_SCRIPT_SCHEMA_VERSION = 2
DEFAULT_IMPORT_FILENAME = "imported-project.zip"


@dataclass(frozen=True)
class ArchiveMember:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]
    is_dir: bool


@dataclass(frozen=True)
class ArchiveDiagnostic:
    code: str
    message: str
    location: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "message": self.message,
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
        message: str,
        *,
        location: str | None = None,
    ) -> None:
        key = (bucket, code, message, location)
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
        for error in validation.errors:
            self.add("blocking", "validation_error", error)
        for warning in validation.warnings:
            self.add("warnings", "validation_warning", warning)

    def to_export_payload(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "blocking": [item.to_payload() for item in self.blocking],
            "auto_fixed": [item.to_payload() for item in self.auto_fixed],
            "warnings": [item.to_payload() for item in self.warnings],
        }

    def to_import_success_payload(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "auto_fixed": [item.to_payload() for item in self.auto_fixed],
            "warnings": [item.to_payload() for item in self.warnings],
        }

    def to_import_error_payload(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "blocking": [item.to_payload() for item in self.blocking],
            "auto_fixable": [item.to_payload() for item in self.auto_fixed],
            "warnings": [item.to_payload() for item in self.warnings],
        }

    def blocking_messages(self) -> list[str]:
        return [item.message for item in self.blocking]

    def warning_messages(self) -> list[str]:
        return [item.message for item in self.warnings]


@dataclass(frozen=True)
class ProjectImportResult:
    project_name: str
    project: dict[str, Any]
    warnings: list[str]
    conflict_resolution: str
    diagnostics: dict[str, list[dict[str, Any]]]


class ProjectArchiveValidationError(ValueError):
    def __init__(
        self,
        detail: str,
        *,
        status_code: int = 400,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        diagnostics: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.errors = errors or []
        self.warnings = warnings or []
        merged_extra = dict(extra or {})
        if diagnostics is not None:
            merged_extra["diagnostics"] = diagnostics
        self.extra = merged_extra


class ProjectArchiveService:
    _VERSION_HISTORY_DIRS = frozenset(
        {
            "storyboards",
            "videos",
            "characters",
            "clues",
        }
    )
    _RESOURCE_EXTENSIONS = {
        "storyboards": ".png",
        "videos": ".mp4",
        "characters": ".png",
        "clues": ".png",
    }
    _ROOT_VISIBLE_ENTRIES = frozenset(DataValidator.ALLOWED_ROOT_ENTRIES)
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
    ) -> dict[str, list[dict[str, Any]]]:
        self._validate_scope(scope)
        if not self.project_manager.project_exists(project_name):
            raise FileNotFoundError(f"项目 '{project_name}' 不存在或未初始化")

        temp_dir, _, _, diagnostics = self._prepare_export_snapshot(project_name, scope=scope)
        temp_dir.cleanup()
        return diagnostics.to_export_payload()

    def export_project(self, project_name: str, *, scope: str = "full") -> tuple[Path, str]:
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
            temp_dir, snapshot_dir, manifest, _ = self._prepare_export_snapshot(
                project_name,
                scope=scope,
            )
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

    def import_project_archive(
        self,
        archive_path: Path,
        *,
        uploaded_filename: str | None = None,
        conflict_policy: str = "prompt",
    ) -> ProjectImportResult:
        if conflict_policy not in {"prompt", "rename", "overwrite"}:
            raise ProjectArchiveValidationError(
                "无效的冲突策略",
                errors=[f"conflict_policy 仅支持 prompt、rename 或 overwrite，收到: {conflict_policy}"],
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
                    diagnostics.extend_validation(self.validator.validate_project_tree(staging_dir))
                    if diagnostics.blocking:
                        raise ProjectArchiveValidationError(
                            "导入包校验失败",
                            errors=diagnostics.blocking_messages(),
                            warnings=diagnostics.warning_messages(),
                            diagnostics=diagnostics.to_import_error_payload(),
                        )

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

                    target_dir = self.project_manager.projects_root / target_name
                    self.project_manager.repair_claude_symlink(target_dir)

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
                        diagnostics=diagnostics.to_import_success_payload(),
                    )
        except zipfile.BadZipFile as exc:
            raise ProjectArchiveValidationError(
                "上传文件不是有效的 ZIP 归档",
                errors=[str(exc)],
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
        self._copy_visible_tree(source_dir, snapshot_dir)

        diagnostics = self._repair_project_tree(snapshot_dir)
        diagnostics.extend_validation(self.validator.validate_project_tree(snapshot_dir))

        # 从源目录收集非标准顶层条目，记录到诊断中（即使已被过滤不导出）
        excluded_entries = self._collect_pass_through_entries(source_dir)
        for entry in excluded_entries:
            diagnostics.add(
                "warnings",
                "non_standard_entry_excluded",
                f"非标准顶层目录/文件 '{entry}' 未包含在导出中",
                location=entry,
            )

        snapshot_project = self._load_json_file(snapshot_dir / self.project_manager.PROJECT_FILE)
        manifest = self._build_archive_manifest(
            project_name,
            snapshot_project,
            scope=scope,
            diagnostics=diagnostics.to_export_payload(),
            pass_through_entries=excluded_entries,
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
    ) -> dict[str, Any]:
        project_payload = project or {}
        return {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "script_schema_version": ARCHIVE_SCRIPT_SCHEMA_VERSION,
            "project_name": project_name,
            "project_title": project_payload.get("title", project_name),
            "content_mode": project_payload.get("content_mode", ""),
            "scope": scope,
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "export_diagnostics": diagnostics,
            "pass_through_entries": pass_through_entries,
        }

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
                dirnames[:] = [name for name in dirnames if name not in self._VERSION_HISTORY_DIRS]

            visible_files = [
                name
                for name in sorted(filenames)
                if not name.startswith(".")
                and not (current_path / name).is_symlink()
                and not (is_root and name in self._AGENT_RUNTIME_EXCLUDES)
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
                    payload = self._load_json_file(source_path) or {}
                    archive.writestr(
                        archive_name,
                        json.dumps(
                            self._trim_versions_payload(payload),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                    continue

                archive.write(source_path, arcname=archive_name)

    @staticmethod
    def _trim_versions_payload(payload: dict[str, Any]) -> dict[str, Any]:
        trimmed = json.loads(json.dumps(payload))
        for resource_type_data in trimmed.values():
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

    def _copy_visible_tree(self, source_dir: Path, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        for current_dir, dirnames, filenames in os.walk(source_dir):
            current_path = Path(current_dir)
            is_root = current_path == source_dir
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not name.startswith(".")
                and not (current_path / name).is_symlink()
                and not (is_root and name in self._AGENT_RUNTIME_EXCLUDES)
                and not (is_root and name not in self._ROOT_VISIBLE_ENTRIES)
            ]
            relative_dir = current_path.relative_to(source_dir)
            destination_dir = target_dir / relative_dir
            destination_dir.mkdir(parents=True, exist_ok=True)

            for filename in sorted(filenames):
                source_path = current_path / filename
                if filename.startswith(".") or source_path.is_symlink():
                    continue
                if is_root and filename in self._AGENT_RUNTIME_EXCLUDES:
                    continue
                if is_root and filename not in self._ROOT_VISIBLE_ENTRIES:
                    continue
                destination_path = destination_dir / filename
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination_path)

    def _repair_project_tree(self, project_dir: Path) -> ArchiveDiagnostics:
        diagnostics = ArchiveDiagnostics()
        project_path = project_dir / self.project_manager.PROJECT_FILE
        project = self._load_json_file(project_path)
        if project is None:
            diagnostics.add(
                "blocking",
                "invalid_project_json",
                f"无法解析 {self.project_manager.PROJECT_FILE}: {project_path}",
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

        clues = project.get("clues")
        if isinstance(clues, dict):
            for clue_name, clue_data in clues.items():
                if not isinstance(clue_data, dict):
                    continue
                if self._repair_path_to_canonical(
                    project_dir,
                    clue_data,
                    field_name="clue_sheet",
                    canonical_rel=f"clues/{clue_name}.png",
                    location=f"clues[{clue_name}].clue_sheet",
                    diagnostics=diagnostics,
                    resource_type="clues",
                    resource_id=clue_name,
                    versions_payload=versions_payload,
                ):
                    project_changed = True

        project_characters = {name for name, payload in (characters or {}).items() if isinstance(payload, dict)}
        project_clues = {name for name, payload in (clues or {}).items() if isinstance(payload, dict)}

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
                            f"{script_location}: 自动修复为 {repaired_script}",
                            location=script_location,
                        )
                    script_path_rel = repaired_script or script_file.replace("\\", "/")
                else:
                    script_path_rel = None

                if not script_path_rel:
                    continue

                script_path = project_dir / script_path_rel
                if not script_path.exists():
                    diagnostics.add(
                        "blocking",
                        "missing_script_file",
                        f"{script_location}: 引用的文件不存在: {script_path_rel}",
                        location=script_location,
                    )
                    continue

                script_payload = self._load_json_file(script_path)
                if script_payload is None:
                    diagnostics.add(
                        "blocking",
                        "invalid_script_json",
                        f"无法解析剧本文件: {script_path_rel}",
                        location=script_location,
                    )
                    continue

                script_changed, project_changed_from_script = self._repair_script_payload(
                    project_dir,
                    script_path_rel=script_path_rel,
                    script_payload=script_payload,
                    project_payload=project,
                    project_characters=project_characters,
                    project_clues=project_clues,
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
        project_clues: set[str],
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
                "novel.source_file 字段已废弃，已移除",
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
                    f"{deprecated_field} 字段已废弃（改为读时计算），已移除",
                    location=f"{script_path_rel}:{deprecated_field}",
                )

        content_mode = str(script_payload.get("content_mode") or project_payload.get("content_mode") or "narration")
        items_key = "segments" if content_mode == "narration" else "scenes"
        id_field = "segment_id" if content_mode == "narration" else "scene_id"
        chars_field = "characters_in_segment" if content_mode == "narration" else "characters_in_scene"
        clues_field = "clues_in_segment" if content_mode == "narration" else "clues_in_scene"

        raw_items = script_payload.get(items_key)
        if not isinstance(raw_items, list):
            return script_changed, project_changed

        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue

            location_prefix = f"{script_path_rel}:{items_key}[{index}]"
            resource_id = str(item.get(id_field) or "").strip()

            if clues_field not in item:
                item[clues_field] = []
                script_changed = True
                diagnostics.add(
                    "auto_fixed",
                    "missing_clues_field",
                    f"{items_key}[{index}]: 补全缺失字段 {clues_field}",
                    location=f"{location_prefix}.{clues_field}",
                )

            assets = item.get("generated_assets")
            if assets is None:
                item["generated_assets"] = self.project_manager.create_generated_assets(content_mode)
                script_changed = True
                diagnostics.add(
                    "auto_fixed",
                    "missing_generated_assets",
                    f"{items_key}[{index}]: 补全缺失字段 generated_assets",
                    location=f"{location_prefix}.generated_assets",
                )
                assets = item["generated_assets"]
            elif isinstance(assets, dict):
                template = self.project_manager.create_generated_assets(content_mode)
                missing_keys = [key for key in template if key not in assets]
                if missing_keys:
                    for key in missing_keys:
                        assets[key] = template[key]
                    script_changed = True
                    # 补全值非 None 的才报诊断，避免 no-op 补全产生噪音
                    non_null_keys = sorted(k for k in missing_keys if template[k] is not None)
                    if non_null_keys:
                        diagnostics.add(
                            "auto_fixed",
                            "generated_assets_defaults",
                            (f"{items_key}[{index}].generated_assets: 补全默认字段 {', '.join(non_null_keys)}"),
                            location=f"{location_prefix}.generated_assets",
                        )

            characters = item.get(chars_field)
            if isinstance(characters, list):
                for character_name in characters:
                    if not isinstance(character_name, str):
                        continue
                    if character_name in project_characters:
                        continue
                    project_payload.setdefault("characters", {})
                    if not isinstance(project_payload.get("characters"), dict):
                        continue
                    project_payload["characters"][character_name] = {
                        "description": self._PLACEHOLDER_CHARACTER_DESCRIPTION,
                    }
                    project_characters.add(character_name)
                    project_changed = True
                    diagnostics.add(
                        "auto_fixed",
                        "placeholder_character_added",
                        f"自动补充缺失角色定义: {character_name}",
                        location=f"characters[{character_name}]",
                    )

            clues = item.get(clues_field)
            if isinstance(clues, list):
                missing_clues = sorted(
                    {clue_name for clue_name in clues if isinstance(clue_name, str) and clue_name not in project_clues}
                )
                if missing_clues:
                    diagnostics.add(
                        "blocking",
                        "missing_clue_definition",
                        (
                            f"{items_key}[{index}]: {clues_field} 引用了不存在于 "
                            f"project.json 的线索: {', '.join(missing_clues)}"
                        ),
                        location=f"{location_prefix}.{clues_field}",
                    )

            if isinstance(assets, dict) and resource_id:
                for field_name, resource_type in (
                    ("storyboard_image", "storyboards"),
                    ("video_clip", "videos"),
                ):
                    if self._repair_path_to_canonical(
                        project_dir,
                        assets,
                        field_name=field_name,
                        canonical_rel=self._canonical_resource_path(
                            resource_type,
                            resource_id,
                        ),
                        location=f"{location_prefix}.generated_assets.{field_name}",
                        diagnostics=diagnostics,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        versions_payload=versions_payload,
                    ):
                        script_changed = True

        return script_changed, project_changed

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
                    f"{location}: 规范化为 {canonical_rel}",
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
                        f"{location}: 从 {resolved_raw} 恢复当前文件 {canonical_rel}",
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
                        f"{location}: 从 {version_rel} 恢复当前文件 {canonical_rel}",
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
        extension = self._RESOURCE_EXTENSIONS[resource_type]
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
                "clues": {},
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

        project_root = project_dir.resolve()
        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.as_posix()
            if key in seen:
                continue
            seen.add(key)

            try:
                resolved = (project_dir / candidate).resolve(strict=False)
                resolved.relative_to(project_root)
            except ValueError:
                continue

            if resolved.exists():
                return candidate.as_posix()

        return None

    @classmethod
    def _canonical_resource_path(cls, resource_type: str, resource_id: str) -> str:
        extension = cls._RESOURCE_EXTENSIONS[resource_type]
        if resource_type in {"storyboards", "videos"}:
            return f"{resource_type}/scene_{resource_id}{extension}"
        return f"{resource_type}/{resource_id}{extension}"

    def _load_json_file(self, path: Path) -> dict[str, Any] | None:
        real = os.path.realpath(path)
        base = os.path.realpath(self.project_manager.projects_root) + os.sep
        tmp = os.path.realpath(tempfile.gettempdir()) + os.sep
        try:
            if real.startswith(base):
                with open(real, encoding="utf-8") as handle:  # noqa: PTH123
                    return json.load(handle)
            if real.startswith(tmp):
                with open(real, encoding="utf-8") as handle:  # noqa: PTH123
                    return json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        logger.warning("路径越界，拒绝读取: %s", real)
        return None

    def _write_json_file(self, path: Path, payload: dict[str, Any]) -> None:
        real = os.path.realpath(path)
        base = os.path.realpath(self.project_manager.projects_root) + os.sep
        tmp = os.path.realpath(tempfile.gettempdir()) + os.sep
        if real.startswith(base):
            os.makedirs(os.path.dirname(real), exist_ok=True)
            with open(real, "w", encoding="utf-8") as handle:  # noqa: PTH123
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            return
        if real.startswith(tmp):
            os.makedirs(os.path.dirname(real), exist_ok=True)
            with open(real, "w", encoding="utf-8") as handle:  # noqa: PTH123
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            return
        raise ValueError(f"路径越界，拒绝写入: {real}")

    @staticmethod
    def _validate_scope(scope: str) -> None:
        if scope not in {"full", "current"}:
            raise ValueError(f"scope 仅支持 full 或 current，收到: {scope}")

    def _scan_archive_members(self, archive: zipfile.ZipFile) -> list[ArchiveMember]:
        members: list[ArchiveMember] = []
        for info in archive.infolist():
            if info.flag_bits & 0x1:
                raise ProjectArchiveValidationError(
                    "导入包校验失败",
                    errors=[f"ZIP 包含加密条目，无法导入: {info.filename}"],
                )

            normalized_name = info.filename.replace("\\", "/")
            if normalized_name.startswith("/"):
                raise ProjectArchiveValidationError(
                    "导入包校验失败",
                    errors=[f"ZIP 包含绝对路径条目: {info.filename}"],
                )

            stripped_name = normalized_name.strip("/")
            if not stripped_name:
                continue

            parts = tuple(part for part in stripped_name.split("/") if part)
            if parts and len(parts[0]) == 2 and parts[0][1] == ":":
                raise ProjectArchiveValidationError(
                    "导入包校验失败",
                    errors=[f"ZIP 包含绝对路径条目: {info.filename}"],
                )
            if any(part == ".." for part in parts):
                raise ProjectArchiveValidationError(
                    "导入包校验失败",
                    errors=[f"ZIP 包含路径穿越条目: {info.filename}"],
                )

            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ProjectArchiveValidationError(
                    "导入包校验失败",
                    errors=[f"ZIP 包含符号链接条目: {info.filename}"],
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
                "导入包校验失败",
                errors=[f"无法解析 {label}: {'/'.join(member.parts)}"],
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
                    "导入包校验失败",
                    errors=["ZIP 中包含多个 arcreel-export.json，无法确定项目根目录"],
                )

            root_parts = next(iter(root_candidates))
            if not any(member.parts == (*root_parts, self.project_manager.PROJECT_FILE) for member in visible_members):
                raise ProjectArchiveValidationError(
                    "导入包校验失败",
                    errors=["官方导出包缺少 project.json"],
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
                "导入包校验失败",
                errors=["ZIP 中未找到 project.json"],
            )
        if len(root_candidates) != 1:
            raise ProjectArchiveValidationError(
                "导入包校验失败",
                errors=["ZIP 中包含多个 project.json，无法确定项目根目录"],
            )

        return next(iter(root_candidates)), None

    def _extract_archive_root(
        self,
        archive: zipfile.ZipFile,
        members: list[ArchiveMember],
        root_parts: tuple[str, ...],
        staging_dir: Path,
    ) -> None:
        staging_root = staging_dir.resolve()
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

            target_path = staging_dir.joinpath(*relative_parts)
            try:
                target_path.resolve(strict=False).relative_to(staging_root)
            except ValueError as exc:
                raise ProjectArchiveValidationError(
                    "导入包校验失败",
                    errors=[f"解压路径越界: {'/'.join(member.parts)}"],
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
                    "检测到项目编号冲突",
                    status_code=409,
                    errors=[f"项目编号 '{preferred_name}' 已存在，请选择覆盖现有项目或自动重命名导入。"],
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
        except Exception:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            if backup_dir and backup_dir.exists():
                backup_dir.rename(target_dir)
            raise

        if backup_dir and backup_dir.exists():
            shutil.rmtree(backup_dir)
