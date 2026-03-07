from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from lib.data_validator import DataValidator
from lib.project_change_hints import emit_project_change_hint
from lib.project_manager import ProjectManager

logger = logging.getLogger(__name__)

ARCHIVE_MANIFEST_NAME = "arcreel-export.json"
ARCHIVE_FORMAT_VERSION = 1
DEFAULT_IMPORT_FILENAME = "imported-project.zip"


@dataclass(frozen=True)
class ArchiveMember:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]
    is_dir: bool


@dataclass(frozen=True)
class ProjectImportResult:
    project_name: str
    project: dict[str, Any]
    warnings: list[str]
    conflict_resolution: str


class ProjectArchiveValidationError(ValueError):
    def __init__(
        self,
        detail: str,
        *,
        status_code: int = 400,
        errors: Optional[list[str]] = None,
        warnings: Optional[list[str]] = None,
        extra: Optional[dict[str, Any]] = None,
    ):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.errors = errors or []
        self.warnings = warnings or []
        self.extra = extra or {}


class ProjectArchiveService:
    def __init__(self, project_manager: ProjectManager):
        self.project_manager = project_manager
        self.validator = DataValidator(projects_root=str(project_manager.projects_root))

    def export_project(self, project_name: str) -> tuple[Path, str]:
        if not self.project_manager.project_exists(project_name):
            raise FileNotFoundError(f"项目 '{project_name}' 不存在或未初始化")

        project_dir = self.project_manager.get_project_path(project_name)
        project = self.project_manager.load_project(project_name)

        fd, archive_path_str = tempfile.mkstemp(
            prefix=f"{project_name}-",
            suffix=".zip",
        )
        os.close(fd)
        archive_path = Path(archive_path_str)

        try:
            with zipfile.ZipFile(
                archive_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                self._write_directory_entry(archive, (project_name,))
                archive.writestr(
                    f"{project_name}/{ARCHIVE_MANIFEST_NAME}",
                    json.dumps(
                        self._build_archive_manifest(project_name, project),
                        ensure_ascii=False,
                        indent=2,
                    ),
                )

                for current_dir, dirnames, filenames in os.walk(project_dir):
                    current_path = Path(current_dir)

                    dirnames[:] = [
                        name
                        for name in sorted(dirnames)
                        if not name.startswith(".")
                        and not (current_path / name).is_symlink()
                    ]
                    visible_files = [
                        name
                        for name in sorted(filenames)
                        if not name.startswith(".")
                        and not (current_path / name).is_symlink()
                    ]

                    relative_dir = current_path.relative_to(project_dir)
                    if relative_dir != Path("."):
                        self._write_directory_entry(
                            archive,
                            (project_name, *relative_dir.parts),
                        )

                    for filename in visible_files:
                        source_path = current_path / filename
                        archive_name = Path(project_name, relative_dir, filename).as_posix()
                        archive.write(source_path, arcname=archive_name)
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise

        download_name = (
            f"{project_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        )
        return archive_path, download_name

    def import_project_archive(
        self,
        archive_path: Path,
        *,
        uploaded_filename: Optional[str] = None,
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

                    validation = self.validator.validate_project_tree(staging_dir)
                    if not validation.valid:
                        raise ProjectArchiveValidationError(
                            "导入包校验失败",
                            errors=validation.errors,
                            warnings=validation.warnings,
                        )

                    project = self._load_project_file(
                        staging_dir / self.project_manager.PROJECT_FILE
                    )
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

                    # Create .claude symlink for agent runtime isolation
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
                        warnings=validation.warnings,
                        conflict_resolution=conflict_resolution,
                    )
        except zipfile.BadZipFile as exc:
            raise ProjectArchiveValidationError(
                "上传文件不是有效的 ZIP 归档",
                errors=[str(exc)],
            ) from exc

    def _build_archive_manifest(
        self,
        project_name: str,
        project: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "format_version": ARCHIVE_FORMAT_VERSION,
            "project_name": project_name,
            "project_title": project.get("title", project_name),
            "content_mode": project.get("content_mode", ""),
            "scope": "full",
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
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
    ) -> tuple[tuple[str, ...], Optional[dict[str, Any]]]:
        visible_members = [
            member for member in members if not self._is_hidden_member(member.parts)
        ]

        manifest_members = [
            member
            for member in visible_members
            if member.parts[-1] == ARCHIVE_MANIFEST_NAME
        ]
        if manifest_members:
            root_candidates = {member.parts[:-1] for member in manifest_members}
            if len(root_candidates) != 1:
                raise ProjectArchiveValidationError(
                    "导入包校验失败",
                    errors=["ZIP 中包含多个 arcreel-export.json，无法确定项目根目录"],
                )

            root_parts = next(iter(root_candidates))
            if not any(
                member.parts == (*root_parts, self.project_manager.PROJECT_FILE)
                for member in visible_members
            ):
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
            member
            for member in visible_members
            if member.parts[-1] == self.project_manager.PROJECT_FILE
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

    def _normalize_project_name(self, value: Any) -> Optional[str]:
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
        manifest: Optional[dict[str, Any]],
        root_parts: tuple[str, ...],
        uploaded_filename: Optional[str],
    ) -> str:
        manifest_name = self._normalize_project_name(
            (manifest or {}).get("project_name")
        )
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
        with open(project_path, "r", encoding="utf-8") as handle:
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
                generated_name = self.project_manager.generate_project_name(
                    project_title or preferred_name
                )
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
        backup_dir: Optional[Path] = None

        try:
            if overwrite and target_dir.exists():
                backup_dir = target_dir.with_name(
                    f".import-backup-{target_dir.name}-{secrets.token_hex(4)}"
                )
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
