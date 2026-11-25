"""Before-images for a project operation, published before each participating write."""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from collections.abc import Generator
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from lib.infra.path_safety import safe_join

_local = threading.local()


class ProjectJournal:
    def __init__(self, root: Path):
        self.root = root.absolute()
        self.db = self.root / "versions/history.sqlite3"
        self.stage = self.root / "versions/.history-undo"
        self.started = False

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        if self.db.is_symlink() or self.db.parent.is_symlink():
            raise ValueError("unexpected history database symlink")
        with closing(sqlite3.connect(self.db.as_uri() + "?mode=rw", uri=True)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            yield db

    def begin(self) -> None:
        if self.stage.is_symlink():
            raise ValueError("unexpected history journal symlink")
        self.stage.mkdir(exist_ok=True)
        self.started = True

    def _target(self, rel: str) -> Path:
        target = self.root / rel
        if safe_join(self.root, rel) != target or target.is_relative_to(self.stage):
            raise ValueError(f"invalid history journal path: {rel}")
        if target == self.db or (target.parent == self.db.parent and target.name.startswith("history.sqlite3-")):
            raise ValueError("cannot journal a live SQLite database")
        return target

    def track(self, path: Path) -> None:
        path = path.absolute()
        if not path.is_relative_to(self.root):
            return
        rel = path.relative_to(self.root).as_posix()
        self._target(rel)
        with self.connection() as db:
            if db.execute("SELECT 1 FROM undo_file WHERE path=?", (rel,)).fetchone():
                return
        self.begin()
        backup = None
        if path.exists():
            backup = uuid4().hex
            shutil.copy2(path, self.stage / backup)
        with self.connection() as db:
            db.execute("INSERT INTO undo_file VALUES (?,?)", (rel, backup))

    def discard_staged(self, path: Path) -> None:
        if path.absolute().is_relative_to(self.root):
            rel = path.absolute().relative_to(self.root).as_posix()
            self._target(rel)
            self.begin()
            with self.connection() as db:
                db.execute("INSERT OR IGNORE INTO staged_file VALUES (?)", (rel,))

    def settle(self, rollback: bool) -> None:
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
                        shutil.copy2(source, replacement)
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
            self.stage.rmdir()


def active_journal(root: Path) -> ProjectJournal | None:
    return getattr(_local, "projects", {}).get(root.absolute())


def scratch_directory(path: Path) -> Path:
    for journal in getattr(_local, "projects", {}).values():
        if path.absolute().is_relative_to(journal.root):
            journal.begin()
            return journal.stage
    return path.parent


def track_write(*paths: Path) -> None:
    for journal in getattr(_local, "projects", {}).values():
        for path in paths:
            journal.track(path)


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
