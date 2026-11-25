"""项目内的记录级版本历史。数据库的存在表示项目已显式选择该后端。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Generator, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from lib.project.asset_types import normalize_asset_name

type Identity = tuple[str, str]
type History = dict[str, dict[str, Any]]

_SCHEMA = """
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


class SQLiteHistory:
    def __init__(self, project: Path):
        self.path = project / "versions/history.sqlite3"

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise ValueError("unexpected history database symlink")
        from lib.artifacts.formal_write import project_metadata_lock

        with (
            project_metadata_lock(self.path.parent.parent),
            closing(sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True)) as db,
            db,
        ):
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN")
            yield db

    def create(self, records: History) -> None:
        if self.path.parent.is_symlink():
            raise ValueError("unexpected history directory symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent)
        os.close(fd)
        try:
            with closing(sqlite3.connect(temp)) as db, db:
                db.executescript(_SCHEMA)
                for kind, bucket in records.items():
                    for name, history in bucket.items():
                        self._update(db, (kind, name), None, history)
            os.link(temp, self.path)
        finally:
            Path(temp).unlink(missing_ok=True)  # hard-delete: temp -- unpublished database candidate

    def revision(self) -> bytes:
        with self.connection() as db:
            return db.execute("SELECT revision FROM history_state").fetchone()[0]

    def resource_keys(self, kind: str | None = None) -> Iterator[Identity]:
        with self.connection() as db:
            if kind is None:
                yield from db.execute("SELECT kind,name FROM resource ORDER BY id")
            else:
                yield from db.execute("SELECT kind,name FROM resource WHERE kind=? ORDER BY id", (kind,))

    def equivalent_keys(self, kind: str, *names: str) -> Iterator[Identity]:
        with self.connection() as db:
            for name in dict.fromkeys(map(normalize_asset_name, names)):
                yield from db.execute(
                    "SELECT kind,name FROM resource WHERE kind=? AND normalized=? ORDER BY id", (kind, name)
                )

    def rows(self, *, remote_only: bool = False):
        condition = "WHERE json_type(payload,'$.storage')='object'" if remote_only else ""
        with self.connection() as db:
            for kind, name, current, payload in db.execute(
                "SELECT kind,name,json_extract(attributes,'$.current_version'),payload FROM resource "
                f"JOIN version_record ON resource.id=version_record.resource {condition} ORDER BY resource.id,position"
            ):
                yield kind, name, current, json.loads(payload)

    def lookup(self, *, file: str | None = None, content_id: str | None = None) -> list[Identity]:
        field, value = ("file", file) if file is not None else ("content_id", content_id)
        with self.connection() as db:
            return list(
                db.execute(
                    f"SELECT DISTINCT kind,name FROM version_record JOIN resource "
                    f"ON resource.id=version_record.resource WHERE {field}=?",
                    (value,),
                )
            )

    def resources(self, keys: Iterable[Identity]) -> ResourceBatch:
        data: History = {}
        with self.connection() as db:
            for kind, name in keys:
                row = db.execute("SELECT id,attributes FROM resource WHERE kind=? AND name=?", (kind, name)).fetchone()
                if row is None:
                    continue
                attrs = json.loads(row[1])
                attrs["versions"] = [
                    json.loads(payload)
                    for (payload,) in db.execute(
                        "SELECT payload FROM version_record WHERE resource=? ORDER BY position", (row[0],)
                    )
                ]
                data.setdefault(kind, {})[name] = attrs
        return ResourceBatch(data)

    def allocation(self, key: Identity) -> tuple[int, int]:
        with self.connection() as db:
            row = db.execute(
                "SELECT current_version,(SELECT max(number) FROM version_record WHERE resource=r.id) "
                "FROM resource r WHERE kind=? AND name=?",
                key,
            ).fetchone()
            if row is None:
                return 0, 1
            return row[0] if type(row[0]) is int else 0, (row[1] or 0) + 1

    def current_record(self, key: Identity) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM resource r JOIN version_record v ON v.resource=r.id "
                "AND v.number=r.current_version WHERE r.kind=? AND r.name=?",
                key,
            ).fetchone()
            return json.loads(row[0]) if row else None

    def append_selected(self, key: Identity, record: dict) -> None:
        with self.connection() as db:
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

        journal = active_journal(self.path.parent.parent)
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
                    f"CREATE TEMP TRIGGER log_{table}_{event} {timing} {event} ON {table} BEGIN "
                    f"INSERT INTO {undo} VALUES ({','.join(fields)}) ON CONFLICT DO NOTHING; END"
                )

    def save(self, batch: ResourceBatch, *, durable_snapshots: list[Path] | None = None) -> None:
        with self.connection() as db:
            if durable_snapshots is None:
                self._journal_changes(db)
            else:
                db.executemany(
                    "DELETE FROM undo_file WHERE path=?",
                    ((p.relative_to(self.path.parent.parent).as_posix(),) for p in durable_snapshots),
                )
            db.execute("UPDATE history_state SET revision=randomblob(16)")
            for kind in dict.fromkeys((*batch.original, *batch)):
                old, new = batch.original.get(kind, {}), batch.get(kind, {})
                for name in dict.fromkeys((*old, *new)):
                    if old.get(name) != new.get(name):
                        self._update(db, (kind, name), old.get(name), new.get(name))
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
