"""Before-images for a project operation, published before each participating write."""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from collections.abc import Collection, Generator
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

import portalocker

from lib.infra.path_safety import safe_join

HISTORY_DB_FILENAME = "history.sqlite3"
HISTORY_DB_RELATIVE_PATH = f"versions/{HISTORY_DB_FILENAME}"
HISTORY_UNDO_RELATIVE_PATH = "versions/.history-undo"

_OWNER_FILE = "writer.lock"
_local = threading.local()


def is_history_database_name(name: str) -> bool:
    """Return whether ``name`` is the live SQLite history database or one of its sidecar files."""

    return name == HISTORY_DB_FILENAME or name.startswith(f"{HISTORY_DB_FILENAME}-")


def is_history_database_path(path: Path) -> bool:
    """Return whether ``path`` points at the project's live SQLite history database or a sidecar."""

    return path.parent.name == "versions" and is_history_database_name(path.name)


def has_sqlite_history(project_dir: Path) -> bool:
    """Return whether ``project_dir`` has opted into the project-local SQLite version history."""

    return (Path(project_dir) / HISTORY_DB_RELATIVE_PATH).is_file()


def history_recovery_required(project_dir: Path) -> bool:
    stage = project_dir / HISTORY_UNDO_RELATIVE_PATH
    if not stage.is_dir():
        return False
    marker = stage / _OWNER_FILE
    if stage.is_symlink() or marker.is_symlink() or not marker.is_file():
        return True
    try:
        with marker.open("rb") as owner:
            try:
                portalocker.lock(owner, portalocker.LOCK_SH | portalocker.LOCK_NB)
            except portalocker.AlreadyLocked:
                return False
    except FileNotFoundError:
        pass
    return True


def _project_root_key(root: Path) -> Path:
    return Path(root).resolve(strict=False)


def _copy_durable(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    with destination.open("r+b") as handle:
        os.fsync(handle.fileno())


class ProjectJournal:
    def __init__(self, root: Path):
        self.root = _project_root_key(root)
        self.db = self.root / HISTORY_DB_RELATIVE_PATH
        self.stage = self.root / HISTORY_UNDO_RELATIVE_PATH
        self.started = False
        self._owner = portalocker.Lock(self.stage / _OWNER_FILE, mode="xb", flags=portalocker.LOCK_EX)

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        if self.db.is_symlink() or self.db.parent.is_symlink():
            raise ValueError("unexpected history database symlink")
        with closing(sqlite3.connect(self.db.as_uri() + "?mode=rw", uri=True, timeout=30.0)) as db, db:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            yield db

    def begin(self) -> None:
        if self.started:
            return
        if self.stage.is_symlink():
            raise ValueError("unexpected history journal symlink")
        self.stage.mkdir(exist_ok=True)
        self.started = True
        self._owner.acquire()

    def _target(self, rel: str) -> Path:
        target = self.root / rel
        if safe_join(self.root, rel) != target or target.is_relative_to(self.stage):
            raise ValueError(f"invalid history journal path: {rel}")
        if is_history_database_path(target) and target.parent == self.db.parent:
            raise ValueError("cannot journal a live SQLite database")
        return target

    def track(self, *paths: Path) -> None:
        candidates = {}
        for path in paths:
            resolved = path.parent.resolve(strict=False) / path.name
            if resolved.is_relative_to(self.root):
                rel = resolved.relative_to(self.root).as_posix()
                candidates[rel] = self._target(rel)
        with self.connection() as db:
            missing = [
                (rel, path)
                for rel, path in candidates.items()
                if not db.execute("SELECT 1 FROM undo_file WHERE path=?", (rel,)).fetchone()
            ]
        if not missing:
            return
        self.begin()
        backups = []
        for rel, resolved in missing:
            backup = None
            if resolved.exists():
                backup = uuid4().hex
                _copy_durable(resolved, self.stage / backup)
            backups.append((rel, backup))
        with self.connection() as db:
            db.executemany("INSERT INTO undo_file VALUES (?,?)", backups)

    def track_new_files(self, directory: Path, names: Collection[str]) -> None:
        """Record absent before-images together, before a new snapshot directory is populated."""
        resolved = directory.parent.resolve() / directory.name
        prefix = resolved.relative_to(self.root).as_posix()
        target = self._target(prefix)
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
        if any(not name or Path(name).anchor or name == "." or ".." in Path(name).parts for name in names):
            raise ValueError("invalid new snapshot path")
        self.begin()
        with self.connection() as db:
            db.executemany(
                "INSERT OR IGNORE INTO undo_file VALUES (?, NULL)", ((f"{prefix}/{name}",) for name in names)
            )

    def discard_staged(self, path: Path) -> None:
        resolved = path.parent.resolve(strict=False) / path.name
        if resolved.is_relative_to(self.root):
            rel = resolved.relative_to(self.root).as_posix()
            self._target(rel)
            self.begin()
            with self.connection() as db:
                db.execute("INSERT OR IGNORE INTO staged_file VALUES (?)", (rel,))

    def settle(self, rollback: bool) -> None:
        self._owner.release()
        with self.connection() as db:
            if rollback:
                for rel, backup in db.execute("SELECT path,backup FROM undo_file ORDER BY rowid DESC"):
                    target = self._target(rel)
                    if backup is None:
                        target.unlink(missing_ok=True)  # hard-delete: rollback -- file created by interrupted operation
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source = safe_join(self.stage, backup)
                        replacement = self.stage / uuid4().hex
                        _copy_durable(source, replacement)
                        os.replace(replacement, target)
                from lib.artifacts.sqlite_history import restore_history

                restore_history(db)
            for (rel,) in db.execute("SELECT path FROM staged_file"):
                target = self._target(rel)
                target.unlink(missing_ok=True)  # hard-delete: temp -- consumed input to a paid selection
            db.execute("DELETE FROM staged_file")
            db.execute("DELETE FROM undo_file")
            db.execute("DELETE FROM undo_resource")
            db.execute("DELETE FROM undo_version")
        if self.stage.is_dir():
            for candidate in self.stage.iterdir():
                candidate.unlink()  # hard-delete: temp -- retired before-image
            self.stage.rmdir()  # hard-delete: temp -- retired history undo directory


def active_journal(root: Path) -> ProjectJournal | None:
    return getattr(_local, "projects", {}).get(_project_root_key(root))


def scratch_directory(path: Path) -> Path:
    resolved = path.parent.resolve(strict=False) / path.name
    for journal in getattr(_local, "projects", {}).values():
        if resolved.is_relative_to(journal.root):
            journal.begin()
            return journal.stage
    return path.parent


def track_write(*paths: Path) -> None:
    for journal in getattr(_local, "projects", {}).values():
        journal.track(*paths)


@contextmanager
def project_operation(root: Path) -> Generator[None]:
    journal = ProjectJournal(root)
    if not journal.db.is_file() or active_journal(root) is not None:
        yield
        return
    projects = getattr(_local, "projects", {})
    _local.projects = projects
    if journal.stage.is_symlink():
        raise ValueError("unexpected history journal symlink")
    if journal.stage.is_dir():
        with journal.connection() as db:
            pending = db.execute(
                "SELECT EXISTS(SELECT 1 FROM undo_file) OR EXISTS(SELECT 1 FROM undo_resource) "
                "OR EXISTS(SELECT 1 FROM undo_version)"
            ).fetchone()[0]
        journal.settle(bool(pending))
    projects[journal.root] = journal
    try:
        yield
    except BaseException:
        if journal.started:
            journal.settle(True)
        raise
    else:
        if journal.started:
            journal.settle(False)
    finally:
        del projects[journal.root]
