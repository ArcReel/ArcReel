"""Content-digest primitives: one sha256 implementation for file bytes and canonical JSON.

Artifact manifests, provenance, checkpoints and profile materialization must give
the same content the same digest. Separate implementations can drift in chunk
size, prefixes or included lengths. This module supplies minimal primitives;
thin wrappers own semantic policy, such as whether to include ``sha256-v1:`` or
file size. The byte-reading and hashing loop is defined only here.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

# Algorithm identifier: prefixed digests persist across processes in manifests, provenance and project revisions.
HASH_ALGORITHM = "sha256-v1"
# Valid forms of bare content hexdigests and algorithm-prefixed digests.
CONTENT_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
PREFIXED_DIGEST_RE = re.compile(r"sha256-v1:[0-9a-f]{64}\Z")

# Streaming chunk size: never load entire large files; this affects I/O count, not the digest.
CHUNK_BYTES = 1024 * 1024


def prefixed(hexdigest: str) -> str:
    """Wrap a bare hexdigest with the algorithm prefix."""

    return f"{HASH_ALGORITHM}:{hexdigest}"


def unprefixed(digest: str) -> str:
    """Remove a valid algorithm prefix; otherwise return the input unchanged so caller comparisons detect a mismatch."""

    if PREFIXED_DIGEST_RE.match(digest):
        return digest[len(HASH_ALGORITHM) + 1 :]
    return digest


def digest_stream(
    read: Callable[[int], bytes],
    *,
    collect_content: bool = False,
) -> tuple[str, int, bytes | None]:
    """Consume ``read`` in chunks until EOF; return (hexdigest, byte count, optional original bytes).

    Callers provide their byte source, including an open file or raw fd, so extra
    ``read``-time checks such as fd-version comparison do not require another hash loop.
    """

    digest = hashlib.sha256()
    size = 0
    chunks: list[bytes] | None = [] if collect_content else None
    for chunk in iter(lambda: read(CHUNK_BYTES), b""):
        digest.update(chunk)
        size += len(chunk)
        if chunks is not None:
            chunks.append(chunk)
    return digest.hexdigest(), size, b"".join(chunks) if chunks is not None else None


class TextModeDigest:
    """Stream the sha256 Windows C-runtime text mode would produce: collapse CRLF to LF and stop at the first 0x1A.

    Feed chunks through ``update``. Retain a boundary ``\\r`` until the next chunk
    determines whether to collapse it, matching a whole-file read.
    """

    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._pending_cr = False
        self._truncated = False

    def update(self, chunk: bytes) -> None:
        if self._truncated or not chunk:
            return
        if self._pending_cr:
            chunk = b"\r" + chunk
            self._pending_cr = False
        head, delimiter, _rest = chunk.partition(b"\x1a")
        if delimiter:
            self._truncated = True
        elif head.endswith(b"\r"):
            head = head[:-1]
            self._pending_cr = True
        self._digest.update(head.replace(b"\r\n", b"\n"))

    def hexdigest(self) -> str:
        digest = self._digest.copy()
        if self._pending_cr:
            digest.update(b"\r")
        return digest.hexdigest()


# Cache capacity in entries. Store only a 64-character digest and size; evict the least recently used entry.
# Estimate: worst-case files for 90 storyboard episodes at about 250 bytes each; 65536 entries use about 16MB without repeated sequential-scan eviction.
FILE_DIGEST_CACHE_CAPACITY = 65536
# Do not cache recently written files: within this window after reading, another write may leave every
# stat identity unchanged (coarse timestamps range from milliseconds to 2 seconds), hiding a second write.
# Cache only hashes computed after the window; reads inside it still hash normally.
FILE_DIGEST_RACY_WINDOW_NS = 3_000_000_000

# These stat errnos mean no readable regular file, matching errors ignored by Path.is_file.
_ABSENT_PATH_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})

#: File stat identity: (st_dev, st_ino, st_size, st_mtime_ns).
FileIdentity = tuple[int, int, int, int]


def file_identity(file_stat: os.stat_result) -> FileIdentity:
    """File-digest cache key: device, inode, size and nanosecond mtime."""

    return file_stat.st_dev, file_stat.st_ino, file_stat.st_size, file_stat.st_mtime_ns


class _FileDigestCache:
    """Bounded, process-local LRU file-digest cache; thread-safe because callers may hash in worker threads."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._entries: OrderedDict[FileIdentity, str] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, identity: FileIdentity) -> str | None:
        with self._lock:
            hexdigest = self._entries.get(identity)
            if hexdigest is not None:
                self._entries.move_to_end(identity)
            return hexdigest

    def put(self, identity: FileIdentity, hexdigest: str) -> None:
        with self._lock:
            self._entries[identity] = hexdigest
            self._entries.move_to_end(identity)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_FILE_DIGEST_CACHE = _FileDigestCache(FILE_DIGEST_CACHE_CAPACITY)


def cached_file_digest(file_stat: os.stat_result) -> str | None:
    """Return the cached content digest for a stat identity, or ``None`` if unseen or changed."""

    return _FILE_DIGEST_CACHE.get(file_identity(file_stat))


def remember_file_digest(file_stat: os.stat_result, hexdigest: str) -> None:
    """Cache a hash only after confirming unchanged pre/post-read stat; exclude recent mtimes (``FILE_DIGEST_RACY_WINDOW_NS``).

    ``file_stat`` must have been captured before reading, and the caller must have
    verified the identity did not change by the end of the read.
    """

    if time.time_ns() - file_stat.st_mtime_ns < FILE_DIGEST_RACY_WINDOW_NS:
        return
    _FILE_DIGEST_CACHE.put(file_identity(file_stat), hexdigest)


def reset_file_digest_cache_for_tests() -> None:
    """Clear the file-digest cache for test isolation."""

    _FILE_DIGEST_CACHE.clear()


def sha256_file_with_size(path: Path) -> tuple[str, int]:
    """Stream the file digest and byte count; raise ``FileNotFoundError`` for a non-regular-file path.

    Cache by stat identity (device, inode, size, nanosecond mtime): reuse unchanged
    identities and rehash changed ones. Digests always represent content. Copy,
    import or restore mtime changes trigger one recalculation; equal content still
    has an equal digest.
    """

    try:
        file_stat = path.stat()
    except OSError as exc:
        # Match Path.is_file: absent paths, non-directory parents and symlink loops count as absent;
        # propagate other errors such as permission denial rather than pretending the file is missing.
        if exc.errno in _ABSENT_PATH_ERRNOS:
            raise FileNotFoundError(path) from None
        raise
    if not stat.S_ISREG(file_stat.st_mode):
        raise FileNotFoundError(path)
    cached = cached_file_digest(file_stat)
    if cached is not None:
        return cached, file_stat.st_size
    with path.open("rb") as handle:
        opened_stat = os.fstat(handle.fileno())
        hexdigest, size, _ = digest_stream(handle.read)
        completed_stat = os.fstat(handle.fileno())
    if (
        file_identity(opened_stat) == file_identity(completed_stat)
        and opened_stat.st_ctime_ns == completed_stat.st_ctime_ns
    ):
        remember_file_digest(opened_stat, hexdigest)
    return hexdigest, size


def sha256_file(path: Path) -> str:
    """Bare hexdigest of file contents."""

    return sha256_file_with_size(path)[0]


def prefixed_sha256_file(path: Path) -> str:
    """File-content digest with its algorithm prefix."""

    return prefixed(sha256_file(path))


def canonical_json(value: object, *, allow_nan: bool = True) -> str:
    """Serialize canonical JSON: key order and whitespace do not affect it; semantic changes do.

    ``allow_nan`` governs non-finite floats (NaN / Infinity). Artifact provenance
    rejects them so any JSON implementation can reproduce inputs. Project/script
    snapshots retain ``json``'s default acceptance, avoiding files that ``json.loads`` can
    read but for which a revision cannot be computed.
    """

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=allow_nan)


def canonical_json_bytes(value: object, *, allow_nan: bool = True) -> bytes:
    """UTF-8 canonical JSON bytes for callers needing both a digest and retained source."""

    return canonical_json(value, allow_nan=allow_nan).encode("utf-8")


def canonical_json_digest(value: object, *, allow_nan: bool = True) -> str:
    """Bare hexdigest of canonical JSON."""

    return hashlib.sha256(canonical_json_bytes(value, allow_nan=allow_nan)).hexdigest()


def prefixed_canonical_json_digest(value: object, *, allow_nan: bool = True) -> str:
    """Canonical JSON digest with its algorithm prefix."""

    return prefixed(canonical_json_digest(value, allow_nan=allow_nan))


__all__ = [
    "CHUNK_BYTES",
    "CONTENT_DIGEST_RE",
    "FILE_DIGEST_CACHE_CAPACITY",
    "FILE_DIGEST_RACY_WINDOW_NS",
    "HASH_ALGORITHM",
    "PREFIXED_DIGEST_RE",
    "FileIdentity",
    "cached_file_digest",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_json_digest",
    "digest_stream",
    "file_identity",
    "prefixed",
    "prefixed_canonical_json_digest",
    "prefixed_sha256_file",
    "remember_file_digest",
    "reset_file_digest_cache_for_tests",
    "sha256_file",
    "sha256_file_with_size",
    "unprefixed",
]
