"""Byte ranges for on-demand resource reads from the legacy JSON history."""

from __future__ import annotations

import codecs
import json
import os
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from lib.material.storage import STORAGE
from lib.project.asset_types import normalize_asset_name

Identity = tuple[str, str]


class _Reader:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.decoder = json.JSONDecoder()
        self.utf8 = codecs.getincrementaldecoder("utf-8")()
        self.buffer = ""
        self.position = 0
        self.offset = 0
        self.eof = False

    def refill(self) -> None:
        chunk = self.stream.read(65536)
        self.eof = not chunk
        self.buffer = self.buffer[self.position :] + self.utf8.decode(chunk, final=self.eof)
        self.position = 0

    def peek(self) -> str:
        while True:
            while self.position < len(self.buffer):
                char = self.buffer[self.position]
                if char not in " \r\n\t":
                    return char
                self.position += 1
                self.offset += 1
            if self.eof:
                return ""
            self.refill()

    def take(self, token: str) -> None:
        if self.peek() != token:
            raise ValueError("invalid version metadata")
        self.position += 1
        self.offset += 1

    def value(self) -> tuple[Any, int, int]:
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.position)
                if end < len(self.buffer) or self.eof:
                    start = self.offset
                    size = len(self.buffer[self.position : end].encode())
                    self.offset += size
                    self.position = end
                    return value, start, size
            except ValueError:
                if self.eof:
                    raise ValueError("invalid version metadata") from None
            self.refill()

    def members(self, visit: Callable[[str], None]) -> None:
        self.take("{")
        if self.peek() != "}":
            while True:
                name, _, _ = self.value()
                if not isinstance(name, str):
                    raise ValueError("invalid version metadata key")
                self.take(":")
                visit(name)
                if self.peek() != ",":
                    break
                self.take(",")
        self.take("}")


@dataclass(slots=True)
class _Resource:
    current: int | None = None
    remote: list[tuple[int, int]] = field(default_factory=list)
    records: dict[int, list[tuple[int, int]]] = field(default_factory=dict)

    @classmethod
    def read(cls, reader: _Reader) -> _Resource:
        resource = cls()

        def attribute(name: str) -> None:
            if name == "versions" and reader.peek() == "[":
                reader.take("[")
                if reader.peek() != "]":
                    while True:
                        record, offset, size = reader.value()
                        if isinstance(record, dict) and isinstance(record.get(STORAGE), dict):
                            resource.remote.append((offset, size))
                        if isinstance(record, dict) and type(number := record.get("version")) is int:
                            resource.records.setdefault(number, []).append((offset, size))
                        if reader.peek() != ",":
                            break
                        reader.take(",")
                reader.take("]")
            else:
                value, _, _ = reader.value()
                if name == "current_version" and type(value) is int:
                    resource.current = value

        if reader.peek() == "{":
            reader.members(attribute)
        else:
            reader.value()
        return resource


class LegacyHistoryIndex:
    def __init__(self) -> None:
        self._stamp: tuple[int, ...] | None = None
        self._resources: dict[Identity, _Resource] = {}
        self._normalized: dict[Identity, list[str]] = {}

    def _index(self, stream: BinaryIO) -> None:
        reader = _Reader(stream)
        resources: dict[Identity, _Resource] = {}
        normalized: dict[Identity, list[str]] = {}

        def bucket(kind: str) -> None:
            def resource(name: str) -> None:
                resources[kind, name] = _Resource.read(reader)
                names = normalized.setdefault((kind, normalize_asset_name(name)), [])
                if name not in names:
                    names.append(name)

            if reader.peek() == "{":
                reader.members(resource)
            else:
                reader.value()

        reader.members(bucket)
        if reader.peek():
            raise ValueError("invalid version metadata")
        self._resources, self._normalized = resources, normalized

    def _refresh(self, stream: BinaryIO) -> None:
        stat = os.fstat(stream.fileno())
        stamp = stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        if stamp != self._stamp:
            self._index(stream)
            self._stamp = stamp

    def remote_rows(self, path: Path) -> Generator[tuple[str, str, int | None, dict]]:
        with path.open("rb") as stream:
            self._refresh(stream)
            for (kind, name), resource in self._resources.items():
                for offset, size in resource.remote:
                    stream.seek(offset)
                    yield kind, name, resource.current, json.loads(stream.read(size))

    def resources(
        self, path: Path, keys: Iterable[Identity], *, equivalent: bool, proof_version: int | None = None
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        with path.open("rb") as stream:
            self._refresh(stream)
            targets = (
                dict.fromkeys(
                    (kind, candidate)
                    for kind, name in keys
                    for candidate in self._normalized.get((kind, normalize_asset_name(name)), (name,))
                )
                if equivalent
                else dict.fromkeys(keys)
            )
            for kind, name in targets:
                resource = self._resources.get((kind, name))
                if resource is None:
                    continue
                records = []
                for version in dict.fromkeys((resource.current, proof_version)):
                    if version is None:
                        continue
                    for offset, size in resource.records.get(version, ()):
                        stream.seek(offset)
                        records.append(json.loads(stream.read(size)))
                result.setdefault(kind, {})[name] = {"current_version": resource.current, "versions": records}
        return result
