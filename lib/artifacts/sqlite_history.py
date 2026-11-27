"""项目内的记录级版本历史。数据库的存在表示项目已显式选择该后端。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from lib.infra.project_write_journal import HISTORY_DB_RELATIVE_PATH
from lib.project.asset_types import normalize_asset_name
from lib.script.item_ids import ITEM_ID_LETTERS, item_id_number

type Identity = tuple[str, str]
type History = dict[str, dict[str, Any]]

_rollback_local = threading.local()

_SCHEMA = """
CREATE TABLE history_bucket(kind TEXT PRIMARY KEY);
CREATE TABLE history_state(revision BLOB NOT NULL);
INSERT INTO history_state VALUES (randomblob(16));
CREATE TABLE staged_file(path TEXT PRIMARY KEY);
CREATE TABLE undo_file(path TEXT PRIMARY KEY, backup TEXT);
CREATE TABLE undo_resource(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, normalized TEXT, attributes TEXT);
CREATE TABLE undo_version(resource INTEGER,number INTEGER,position INTEGER,payload TEXT,PRIMARY KEY(resource,number));
CREATE TABLE resource (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL, normalized TEXT NOT NULL,
    attributes TEXT NOT NULL, current_version GENERATED ALWAYS AS (json_extract(attributes,'$.current_version')), UNIQUE(kind, name)
);
CREATE INDEX equivalent_name ON resource(kind,normalized);
CREATE TABLE version_record (
    resource INTEGER NOT NULL REFERENCES resource ON DELETE CASCADE,
    number INTEGER NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
    file TEXT GENERATED ALWAYS AS (json_extract(payload, '$.file')) VIRTUAL,
    content_id TEXT GENERATED ALWAYS AS (json_extract(payload, '$.content_version_id')) VIRTUAL,
    PRIMARY KEY(resource, number)
);
CREATE INDEX by_file ON version_record(file);
CREATE INDEX by_content ON version_record(content_id);
CREATE INDEX remote_records ON version_record(resource,position) WHERE json_type(payload,'$.storage')='object';
CREATE INDEX by_position ON version_record(resource, position);
"""


class ResourceBatch(dict):
    """Only identities participating in this mutation, with their original values."""

    def __init__(self, data: History):
        super().__init__(data)
        self.original = deepcopy(data)


def _rollback_stacks() -> dict[Path, list[dict[Identity, dict[str, Any] | None]]]:
    stacks = getattr(_rollback_local, "stacks", None)
    if stacks is None:
        stacks = {}
        _rollback_local.stacks = stacks
    return stacks


class SQLiteHistory:
    def __init__(self, project: Path):
        self.project = Path(project)
        self.path = self.project / HISTORY_DB_RELATIVE_PATH

    def is_enabled(self) -> bool:
        return self.path.is_file()

    def _root_key(self) -> Path:
        return self.project.resolve(strict=False)

    def _check_symlinks(self) -> None:
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise ValueError("unexpected history database symlink")

    @contextmanager
    def _read_connection(self) -> Generator[sqlite3.Connection]:
        self._check_symlinks()
        with closing(sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True, timeout=30.0)) as db:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            try:
                yield db
            finally:
                if db.in_transaction:
                    db.rollback()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        self._check_symlinks()
        from lib.artifacts.formal_write import project_metadata_lock

        with (
            project_metadata_lock(self.project),
            closing(sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True, timeout=30.0)) as db,
            db,
        ):
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db

    @contextmanager
    def rollback_scope(self) -> Generator[None]:
        """Record touched identities' pre-scope states and restore them on in-process failure."""

        if not self.is_enabled():
            yield
            return
        stacks = _rollback_stacks()
        root_key = self._root_key()
        stack = stacks.setdefault(root_key, [])
        frame: dict[Identity, dict[str, Any] | None] = {}
        stack.append(frame)
        try:
            yield
        except BaseException:
            stack.pop()
            if not stack:
                stacks.pop(root_key, None)
            if frame:
                self._restore_scope(frame)
            raise
        else:
            stack.pop()
            if not stack:
                stacks.pop(root_key, None)

    def _capture_for_scopes(self, db: sqlite3.Connection, keys: Iterable[Identity]) -> None:
        stack = _rollback_stacks().get(self._root_key())
        if not stack:
            return
        for key in keys:
            missing = [frame for frame in stack if key not in frame]
            if not missing:
                continue
            snapshot = self._fetch_resource(db, key)
            for frame in missing:
                frame[key] = deepcopy(snapshot)

    def _restore_scope(self, frame: Mapping[Identity, dict[str, Any] | None]) -> None:
        with self.connection() as db:
            self._journal_changes(db)
            db.execute("UPDATE history_state SET revision=randomblob(16)")
            for key, before in frame.items():
                current = self._fetch_resource(db, key)
                if current != before:
                    self._update(db, key, current, before)

    @staticmethod
    def read_exchange(path: Path) -> History:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing version history: {path.name}")
        records = json.loads(path.read_bytes())
        if not isinstance(records, dict):
            raise ValueError(f"invalid version history: {path.name}")
        for kind, bucket in records.items():
            if not isinstance(bucket, dict):
                raise ValueError(f"invalid version history bucket: {kind}")
            for name, resource in bucket.items():
                versions = resource.get("versions") if isinstance(resource, dict) else None
                if not isinstance(versions, list):
                    raise ValueError(f"invalid version history: {kind}/{name}")
                current = resource.get("current_version")
                if current is not None and (type(current) is not int or current < 0):
                    raise ValueError(f"invalid current version: {kind}/{name}")
                numbers = set()
                for record in versions:
                    number = record.get("version") if isinstance(record, dict) else None
                    if type(number) is not int or number < 1 or number in numbers:
                        raise ValueError(f"invalid version number: {kind}/{name}")
                    numbers.add(number)
        return records

    def create(self, records: History) -> None:
        if self.path.parent.is_symlink():
            raise ValueError("unexpected history directory symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".history-build-")
        os.close(fd)
        try:
            with closing(sqlite3.connect(temp)) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("PRAGMA foreign_keys=ON")
                with db:
                    db.executescript(_SCHEMA)
                    for kind, bucket in records.items():
                        db.execute("INSERT INTO history_bucket VALUES (?)", (kind,))
                        for name, history in bucket.items():
                            self._update(db, (kind, name), None, history)
                    if list(self._kinds(db)) != list(records):
                        raise ValueError("SQLite version history bucket order did not round-trip")
                    for kind, expected in records.items():
                        names = [
                            name
                            for (name,) in db.execute("SELECT name FROM resource WHERE kind=? ORDER BY id", (kind,))
                        ]
                        if names != list(expected) or any(
                            self._fetch_resource(db, (kind, name)) != expected[name] for name in names
                        ):
                            raise ValueError(f"SQLite version history did not round-trip: {kind}")
            os.link(temp, self.path)
        finally:
            Path(temp).unlink(missing_ok=True)  # hard-delete: temp -- unpublished database candidate

    @staticmethod
    def _kinds(db: sqlite3.Connection) -> Iterator[str]:
        known = set()
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_bucket'").fetchone():
            for (kind,) in db.execute("SELECT kind FROM history_bucket ORDER BY rowid"):
                known.add(kind)
                yield kind
        for (kind,) in db.execute("SELECT kind FROM resource GROUP BY kind ORDER BY min(id)"):
            if kind not in known:
                yield kind

    @classmethod
    def _logical_buckets(cls, db: sqlite3.Connection):
        for kind in cls._kinds(db):
            yield (
                kind,
                {
                    name: cls._fetch_resource(db, (kind, name))
                    for (name,) in db.execute("SELECT name FROM resource WHERE kind=? ORDER BY id", (kind,))
                },
            )

    def iter_resources(self) -> Iterator[History]:
        with self._read_connection() as db:
            for kind, name in db.execute("SELECT kind,name FROM resource ORDER BY id"):
                yield {kind: {name: self._fetch_resource(db, (kind, name))}}

    def snapshot(self) -> History:
        with self._read_connection() as db:
            return dict(self._logical_buckets(db))

    def backup_to(self, target: SQLiteHistory) -> None:
        with self._read_connection() as source, closing(sqlite3.connect(target.path)) as destination:
            source.backup(destination)

    def export_json(self, path: Path, transform: Callable[[History], History] | None = None) -> None:
        with self._read_connection() as db, path.open("w", encoding="utf-8") as output:
            output.write("{")
            separator = ""
            for kind in self._kinds(db):
                header = separator + json.dumps(kind) + ":{"
                opened = False
                for (name,) in db.execute("SELECT name FROM resource WHERE kind=? ORDER BY id", (kind,)):
                    info = self._fetch_resource(db, (kind, name))
                    if transform is not None:
                        info = transform({kind: {name: info}}).get(kind, {}).get(name)
                    if info is None:
                        continue
                    output.write("," if opened else header)
                    opened = True
                    output.write(json.dumps(name) + ":")
                    json.dump(info, output, ensure_ascii=False)
                if not opened:
                    if transform is not None and kind not in transform({kind: {}}):
                        continue
                    output.write(header)
                output.write("}")
                separator = ","
            output.write("}")

    def revision(self) -> bytes:
        with self._read_connection() as db:
            return db.execute("SELECT revision FROM history_state").fetchone()[0]

    def resource_keys(self, kind: str | None = None) -> Iterator[Identity]:
        with self._read_connection() as db:
            if kind is None:
                yield from db.execute("SELECT kind,name FROM resource ORDER BY id")
            else:
                yield from db.execute("SELECT kind,name FROM resource WHERE kind=? ORDER BY id", (kind,))

    def episode_resource_names(self, episode: int, kinds: Iterable[str]) -> Iterator[str]:
        letters = "".join(sorted(set(ITEM_ID_LETTERS.values())))
        direct = f"E{episode}[{letters}]*"
        padded = f"E0*{episode}[{letters}]*"
        with self._read_connection() as db:
            for kind in kinds:
                for (name,) in db.execute(
                    "SELECT name FROM resource WHERE kind=? AND (name GLOB ? OR name GLOB ?)",
                    (kind, direct, padded),
                ):
                    if item_id_number(name, episode) is not None:
                        yield name

    def equivalent_keys(self, kind: str, *names: str) -> Iterator[Identity]:
        with self._read_connection() as db:
            for name in dict.fromkeys(map(normalize_asset_name, names)):
                yield from db.execute(
                    "SELECT kind,name FROM resource WHERE kind=? AND normalized=? ORDER BY id", (kind, name)
                )

    def rows(self, *, remote_only: bool = False):
        condition = "WHERE json_type(payload,'$.storage')='object'" if remote_only else ""
        with self._read_connection() as db:
            for kind, name, current, payload in db.execute(
                "SELECT kind,name,json_extract(attributes,'$.current_version'),payload FROM resource "
                f"JOIN version_record ON resource.id=version_record.resource {condition} ORDER BY resource.id,position"
            ):
                yield kind, name, current, json.loads(payload)

    def lookup(self, *, file: str | None = None, content_id: str | None = None) -> list[Identity]:
        field, value = ("file", file) if file is not None else ("content_id", content_id)
        with self._read_connection() as db:
            return list(
                db.execute(
                    f"SELECT DISTINCT kind,name FROM version_record JOIN resource "
                    f"ON resource.id=version_record.resource WHERE {field}=?",
                    (value,),
                )
            )

    @staticmethod
    def _fetch_resource(db: sqlite3.Connection, key: Identity) -> dict[str, Any] | None:
        row = db.execute("SELECT id,attributes FROM resource WHERE kind=? AND name=?", key).fetchone()
        if row is None:
            return None
        attrs = json.loads(row[1])
        attrs["versions"] = [
            json.loads(payload)
            for (payload,) in db.execute(
                "SELECT payload FROM version_record WHERE resource=? ORDER BY position", (row[0],)
            )
        ]
        return attrs

    def resources(self, keys: Iterable[Identity]) -> ResourceBatch:
        data: History = {}
        with self._read_connection() as db:
            for kind, name in keys:
                attrs = self._fetch_resource(db, (kind, name))
                if attrs is None:
                    continue
                data.setdefault(kind, {})[name] = attrs
        return ResourceBatch(data)

    def selected_equivalents(self, keys: Iterable[Identity]) -> dict[Identity, History]:
        wanted = {(kind, normalize_asset_name(name)) for kind, name in keys}
        result: dict[Identity, History] = {key: {} for key in wanted}
        with self._read_connection() as db:
            rows = db.execute(
                "WITH wanted(kind,name) AS (SELECT value->>0,value->>1 FROM json_each(?)) "
                "SELECT r.kind,r.normalized,r.name,r.current_version,v.payload FROM wanted w "
                "JOIN resource r ON r.kind=w.kind AND r.normalized=w.name "
                "LEFT JOIN version_record v ON v.resource=r.id AND v.number=r.current_version ORDER BY r.id",
                (json.dumps(list(wanted)),),
            )
            for kind, normalized, name, current, payload in rows:
                result[kind, normalized].setdefault(kind, {})[name] = {
                    "current_version": current,
                    "versions": [json.loads(payload)] if payload is not None else [],
                }
        return result

    def selected_resources(self, keys: Iterable[Identity], *, proof_version: int | None = None) -> History:
        result: History = {}
        with self._read_connection() as db:
            for kind, name in dict.fromkeys(keys):
                rows = db.execute(
                    "SELECT r.current_version,v.payload FROM resource r LEFT JOIN version_record v "
                    "ON v.resource=r.id AND (v.number=r.current_version OR v.number=?) "
                    "WHERE r.kind=? AND r.name=? ORDER BY v.position",
                    (proof_version, kind, name),
                )
                for current, payload in rows:
                    history = result.setdefault(kind, {}).setdefault(name, {"current_version": current, "versions": []})
                    if payload is not None:
                        history["versions"].append(json.loads(payload))
        return result

    def allocation(self, key: Identity) -> tuple[int, int]:
        with self._read_connection() as db:
            row = db.execute(
                "SELECT current_version,(SELECT max(number) FROM version_record WHERE resource=r.id) "
                "FROM resource r WHERE kind=? AND name=?",
                key,
            ).fetchone()
            if row is None:
                return 0, 1
            return row[0] if type(row[0]) is int else 0, (row[1] or 0) + 1

    def current_record(self, key: Identity) -> dict | None:
        with self._read_connection() as db:
            row = db.execute(
                "SELECT payload FROM resource r JOIN version_record v ON v.resource=r.id "
                "AND v.number=r.current_version WHERE r.kind=? AND r.name=?",
                key,
            ).fetchone()
            return json.loads(row[0]) if row else None

    def append_selected(self, key: Identity, record: dict) -> None:
        with self.connection() as db:
            self._capture_for_scopes(db, (key,))
            self._journal_changes(db)
            db.execute("UPDATE history_state SET revision=randomblob(16)")
            number = record["version"]
            (rid,) = db.execute(
                "INSERT INTO resource(kind,name,normalized,attributes) VALUES (?,?,?,json_object('current_version',?)) "
                "ON CONFLICT(kind,name) DO UPDATE SET attributes=json_set(attributes,'$.current_version',?) "
                "RETURNING id",
                (*key, normalize_asset_name(key[1]), number, number),
            ).fetchone()
            db.execute(
                "INSERT INTO version_record(resource,number,position,payload) "
                "SELECT ?,?,coalesce(max(position),-1)+1,? FROM version_record WHERE resource=?",
                (rid, number, json.dumps(record), rid),
            )

    def _journal_changes(self, db: sqlite3.Connection) -> None:
        from lib.infra.project_write_journal import active_journal

        journal = active_journal(self.project)
        if journal is None:
            return
        journal.begin()
        for table, undo, keys, values in (
            ("resource", "undo_resource", ("id",), ("kind", "name", "normalized", "attributes")),
            ("version_record", "undo_version", ("resource", "number"), ("position", "payload")),
        ):
            for event in ("INSERT", "UPDATE", "DELETE"):
                prefix = "NEW" if event == "INSERT" else "OLD"
                fields = [f"{prefix}.{k}" for k in keys]
                fields += ["NULL" if event == "INSERT" else f"OLD.{v}" for v in values]
                timing = "AFTER" if event == "INSERT" else "BEFORE"
                db.execute(
                    f"CREATE TEMP TRIGGER IF NOT EXISTS log_{table}_{event} {timing} {event} ON {table} BEGIN "
                    f"INSERT INTO {undo} VALUES ({','.join(fields)}) ON CONFLICT DO NOTHING; END"
                )

    def save(self, batch: ResourceBatch, *, durable_snapshots: list[Path] | None = None) -> None:
        changed = [
            ((kind, name), old.get(name), new.get(name))
            for kind in dict.fromkeys((*batch.original, *batch))
            for old, new in ((batch.original.get(kind, {}), batch.get(kind, {})),)
            for name in dict.fromkeys((*old, *new))
            if old.get(name) != new.get(name)
        ]
        with self.connection() as db:
            if durable_snapshots is None:
                self._capture_for_scopes(db, (key for key, _old, _new in changed))
                self._journal_changes(db)
            else:
                root_key = self._root_key()
                db.executemany(
                    "DELETE FROM undo_file WHERE path=?",
                    (
                        ((p.parent.resolve(strict=False) / p.name).relative_to(root_key).as_posix(),)
                        for p in durable_snapshots
                    ),
                )
            db.execute("UPDATE history_state SET revision=randomblob(16)")
            for key, old_val, new_val in changed:
                self._update(db, key, old_val, new_val)
        batch.original = deepcopy(dict(batch))

    @staticmethod
    def _update(db: sqlite3.Connection, key: Identity, old: Mapping | None, new: Mapping | None) -> None:
        if new is None:
            db.execute("DELETE FROM resource WHERE kind=? AND name=?", key)
            return
        attrs = json.dumps({k: v for k, v in new.items() if k != "versions"})
        (rid,) = db.execute(
            "INSERT INTO resource(kind,name,normalized,attributes) VALUES (?,?,?,?) "
            "ON CONFLICT(kind,name) DO UPDATE SET attributes=excluded.attributes RETURNING id",
            (*key, normalize_asset_name(key[1]), attrs),
        ).fetchone()
        previous = {r["version"]: (i, r) for i, r in enumerate((old or {}).get("versions", []))}
        for i, record in enumerate(new.get("versions", [])):
            num = record["version"]
            if previous.pop(num, None) != (i, record):
                db.execute(
                    "INSERT INTO version_record(resource,number,position,payload) VALUES (?,?,?,?) ON CONFLICT(resource,number) DO UPDATE SET position=excluded.position,payload=excluded.payload",
                    (rid, num, i, json.dumps(record)),
                )
        db.executemany("DELETE FROM version_record WHERE resource=? AND number=?", ((rid, n) for n in previous))


def restore_history(db: sqlite3.Connection) -> None:
    db.execute("UPDATE history_state SET revision=randomblob(16)")
    db.execute("DELETE FROM resource WHERE id IN (SELECT id FROM undo_resource WHERE attributes IS NULL)")
    db.execute(
        "INSERT INTO resource(id,kind,name,normalized,attributes) SELECT id,kind,name,normalized,attributes FROM undo_resource "
        "WHERE attributes IS NOT NULL ON CONFLICT(id) DO UPDATE SET attributes=excluded.attributes"
    )
    db.execute(
        "DELETE FROM version_record WHERE (resource,number) IN "
        "(SELECT resource,number FROM undo_version WHERE payload IS NULL)"
    )
    db.execute(
        "INSERT INTO version_record(resource,number,position,payload) "
        "SELECT resource,number,position,payload FROM undo_version WHERE payload IS NOT NULL "
        "ON CONFLICT(resource,number) DO UPDATE SET position=excluded.position,payload=excluded.payload"
    )
