"""Data-root layout: the single source of locations for each kind of runtime data.

The data root (``app_data_dir()``) stores a deployment's runtime data; projects are
only one kind. Other code must use ``DataRootLayout`` rather than independently
constructing entries or inferring the root from project directories (ADR 0088).
Projects live under ``projects/``; other runtime data uses unprefixed sibling names.

Only ``is_project_dir`` defines a project: a directory matching project-name rules
and containing ``project.json``. No other data-root entries are projects.

Except ``is_project_dir`` and ``list_project_dirs``, these functions do no I/O: derive
paths without checking existence or creating directories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lib.agent.agent_memory_paths import MEMORY_DIRNAME, is_valid_memory_user_id
from lib.infra.app_data_dir import app_data_dir

PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
PROJECT_FILENAME = "project.json"
#: Global asset-library directory under the data root; data-root-relative asset paths use this prefix.
GLOBAL_ASSETS_DIRNAME = "global_assets"


def is_project_dir(path: Path) -> bool:
    """Whether ``path`` is a directory matching ``PROJECT_NAME_PATTERN`` and containing ``project.json``."""
    return bool(PROJECT_NAME_PATTERN.fullmatch(path.name)) and path.is_dir() and (path / PROJECT_FILENAME).is_file()


def list_project_dirs(projects_dir: Path) -> list[Path]:
    """List all projects in the projects directory, sorted by name; return empty if the directory is absent."""
    try:
        children = sorted(projects_dir.iterdir())
    except FileNotFoundError:
        return []
    return [child for child in children if is_project_dir(child)]


@dataclass(frozen=True)
class DataRootLayout:
    """Locations of each kind of entry within one data root."""

    root: Path

    @classmethod
    def current(cls) -> DataRootLayout:
        """Resolve the layout from the current data-root configuration."""
        return cls(app_data_dir())

    @classmethod
    def for_project_dir(cls, project_dir: Path) -> DataRootLayout:
        """Derive the layout from a project directory; Agent skill scripts run with that directory as cwd."""
        return cls(Path(project_dir).parent.parent)

    @property
    def projects_dir(self) -> Path:
        """Project container: each project lives at ``<项目目录>/<项目名>/``."""
        return self.root / "projects"

    @property
    def deleted_projects_dir(self) -> Path:
        """Deleted projects live at ``<本目录>/<墓碑名>/`` and can be restored whole during retention."""
        return self.root / "deleted_projects"

    @property
    def global_assets_dir(self) -> Path:
        """Global asset library; asset-record paths are data-root-relative and start with this directory name."""
        return self.root / GLOBAL_ASSETS_DIRNAME

    @property
    def users_dir(self) -> Path:
        """Per-user data root: memory and other data live under ``<users_dir>/<user_id>/``."""
        return self.root / "users"

    def user_memory_dir(self, user_id: str) -> Path:
        """User-memory directory.

        ``user_id`` directly becomes a directory name and must be a single path segment
        (see ``is_valid_memory_user_id``). Otherwise the derived path escapes the root;
        raise ``ValueError``.
        """
        if not is_valid_memory_user_id(user_id):
            raise ValueError(f"user_id 必须是单个路径段，不能为空或含路径分隔符 / 驱动器冒号 / NUL：{user_id!r}")
        return self.users_dir / user_id / MEMORY_DIRNAME

    @property
    def sqlite_db_path(self) -> Path:
        """Default SQLite main file without ``DATABASE_URL``; ``-wal`` / ``-shm`` share its directory and prefix."""
        return self.root / "arcreel.db"

    @property
    def system_config_json_path(self) -> Path:
        """Legacy system configuration file, used only as a one-time import source."""
        return self.root / ".system_config.json"

    @property
    def log_dir(self) -> Path:
        """File-log directory."""
        return self.root / "logs"

    @property
    def vertex_keys_dir(self) -> Path:
        """Vertex credential directory."""
        return self.root / "vertex_keys"

    def vertex_credential_path(self, credential_id: int) -> Path:
        """Vertex credential file uploaded under its credential ID."""
        return self.vertex_keys_dir / f"vertex_cred_{credential_id}.json"

    @property
    def trial_runs_dir(self) -> Path:
        """Artifacts from endpoint Test connection runs."""
        return self.root / "trial_runs"

    @property
    def runtime_dir(self) -> Path:
        """Process-internal state: generation-admission locks, migration markers, migration error logs, etc."""
        return self.root / "runtime"

    @property
    def system_dirs(self) -> tuple[Path, ...]:
        """Registered top-level data-root directories other than projects.

        Register new system directories here. Session startup precreates them and denies
        Bash reads. Unregistered directories created after startup are outside ``denyRead``;
        built-in read tools still deny data-root access by default.
        """
        return (
            self.deleted_projects_dir,
            self.global_assets_dir,
            self.users_dir,
            self.log_dir,
            self.vertex_keys_dir,
            self.trial_runs_dir,
            self.runtime_dir,
        )

    @property
    def top_level_entries(self) -> tuple[Path, ...]:
        """All registered top-level data-root entries: projects and other runtime data."""
        return (self.projects_dir, *self.system_dirs, self.sqlite_db_path)

    @property
    def generation_admission_locks_dir(self) -> Path:
        """Generation-admission lock directory."""
        return self.runtime_dir / "generation-admission-locks"

    @property
    def session_import_marker_path(self) -> Path:
        """Marker that local SDK session import has completed."""
        return self.runtime_dir / "session-store-import.done"

    @property
    def layout_migration_marker_path(self) -> Path:
        """Data-root layout migration marker; startup skips migration steps when present."""
        return self.runtime_dir / "data-root-layout-migrated.done"

    @property
    def project_migration_error_log_path(self) -> Path:
        """Project-schema migration error log."""
        return self.runtime_dir / "project-migration-errors.log"

    @property
    def auth_token_secret_path(self) -> Path:
        """JWT signing key generated when ``AUTH_TOKEN_SECRET`` is absent; readable/writable only by its owner."""
        return self.runtime_dir / "auth-token-secret"

    @property
    def image_thumbnail_cache_dir(self) -> Path:
        """Image thumbnail cache produced by public media endpoints with ``?w=``; the whole directory is disposable.

        Keep outside project directories: excluded from asset fingerprints, version
        snapshots and project exports, and not on the public-media allowlist.
        """
        return self.runtime_dir / "image-thumbnails"
