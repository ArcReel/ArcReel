"""内容摘要原语：文件字节与规范 JSON 的 sha256 单一实现。

产物清单、依据、检查点、profile 物化都要对「同一份内容」取同一个摘要，实现分散会让
口径悄悄漂移（chunk 大小、前缀、是否带长度）。本模块只提供最小原语，语义策略（比如带不带
``sha256-v1:`` 前缀、要不要连带文件长度）由各薄包装表达，读字节与哈希的循环只此一处。
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

# 摘要算法标识：带此前缀的摘要串出现在产物清单、依据与项目修订号里，跨进程持久化。
HASH_ALGORITHM = "sha256-v1"
# 裸 hexdigest（内容摘要）与带算法前缀的摘要串各自的合法形态。
CONTENT_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
PREFIXED_DIGEST_RE = re.compile(r"sha256-v1:[0-9a-f]{64}\Z")

# 流式读取粒度：大文件不整体载入内存；取值只影响 I/O 次数，不影响摘要值。
CHUNK_BYTES = 1024 * 1024


def prefixed(hexdigest: str) -> str:
    """把裸 hexdigest 包成带算法前缀的摘要串。"""

    return f"{HASH_ALGORITHM}:{hexdigest}"


def unprefixed(digest: str) -> str:
    """去掉合法的算法前缀，得到裸 hexdigest；不带合法前缀的输入原样返回，由调用方的比对判为不一致。"""

    if PREFIXED_DIGEST_RE.match(digest):
        return digest[len(HASH_ALGORITHM) + 1 :]
    return digest


def digest_stream(
    read: Callable[[int], bytes],
    *,
    collect_content: bool = False,
) -> tuple[str, int, bytes | None]:
    """按块消费 ``read`` 直到 EOF，返回 (hexdigest, 字节数, 可选原字节)。

    ``read`` 让调用方自带字节来源——已打开的文件对象、原始 fd 都可以，因此需要在读取期间
    额外校验（如 fd 版本比对）的调用方不必自己重写哈希循环。
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
    """流式复现 Windows C 运行时文本模式读同一份字节得到的 sha256：CRLF 折叠为 LF，读到首个 0x1A 为止。

    按块喂入 ``update``，块边界上的 ``\\r`` 会留到下一块再决定是否折叠，结果与整体读取一致。
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


# 文件摘要缓存的容量上限（条目数）。每条只存一个 64 字符摘要与大小，满了淘汰最久未用的一条。
# 估算：90 集分镜模式最坏情况的文件数 × 每条约 250 字节，65536 条约 16MB，按序扫描不会反复冲刷。
FILE_DIGEST_CACHE_CAPACITY = 65536
# 刚被写过的文件不入缓存：mtime 距「读完」不足此窗口时，同一时间戳下的再次写入可能不改变任何一项
# stat 标识（粗粒度文件系统的时间戳精度可达数毫秒到 2 秒），缓存会把「写了两次」当成「没变」。
# 超过窗口之后再哈希的文件才入缓存，窗口内的读取照常哈希。
FILE_DIGEST_RACY_WINDOW_NS = 3_000_000_000

# stat 时这些错误码说明路径上没有可读的常规文件（与 ``Path.is_file`` 忽略的错误码一致）。
_ABSENT_PATH_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})

#: 文件的 stat 标识：(st_dev, st_ino, st_size, st_mtime_ns)。
FileIdentity = tuple[int, int, int, int]


def file_identity(file_stat: os.stat_result) -> FileIdentity:
    """文件摘要缓存的键：设备、inode、大小、纳秒 mtime。"""

    return file_stat.st_dev, file_stat.st_ino, file_stat.st_size, file_stat.st_mtime_ns


class _FileDigestCache:
    """进程内、有容量上限的文件摘要缓存（LRU）；线程安全，调用方可能在工作线程里取摘要。"""

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
    """按 stat 标识取已缓存的内容摘要；标识没见过或已变则返回 ``None``。"""

    return _FILE_DIGEST_CACHE.get(file_identity(file_stat))


def remember_file_digest(file_stat: os.stat_result, hexdigest: str) -> None:
    """登记「读取前后 stat 一致」的那次哈希结果；mtime 过新的文件不入缓存（见 ``FILE_DIGEST_RACY_WINDOW_NS``）。

    ``file_stat`` 必须是读取开始前取得的，且调用方已确认读取结束后标识未变。
    """

    if time.time_ns() - file_stat.st_mtime_ns < FILE_DIGEST_RACY_WINDOW_NS:
        return
    _FILE_DIGEST_CACHE.put(file_identity(file_stat), hexdigest)


def reset_file_digest_cache_for_tests() -> None:
    """清空文件摘要缓存（测试隔离用）。"""

    _FILE_DIGEST_CACHE.clear()


def sha256_file_with_size(path: Path) -> tuple[str, int]:
    """流式取文件摘要与字节数；路径不是常规文件时抛 ``FileNotFoundError``。

    摘要按 stat 标识（设备、inode、大小、纳秒 mtime）缓存：标识不变直接复用，变了重新哈希。
    摘要值始终是内容哈希，复制、导入、恢复改变 mtime 只会触发一次重算，内容相同则摘要相同。
    """

    try:
        file_stat = path.stat()
    except OSError as exc:
        # 与 ``Path.is_file`` 同一口径：路径不存在、上级不是目录、符号链接成环判为不在场；
        # 权限不足等其它错误照常抛出，不冒充「文件不存在」。
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
    """文件内容的裸 hexdigest。"""

    return sha256_file_with_size(path)[0]


def prefixed_sha256_file(path: Path) -> str:
    """文件内容摘要，带算法前缀。"""

    return prefixed(sha256_file(path))


def canonical_json(value: object, *, allow_nan: bool = True) -> str:
    """规范化 JSON 序列化：键序与空白不影响结果，语义变更才影响。

    ``allow_nan`` 决定非有限浮点（NaN / Infinity）的待遇：产物依据一类要求输入可被任何
    JSON 实现原样复现，故拒绝；项目与剧本快照沿用 ``json`` 默认的接受口径，避免一份能被
    ``json.loads`` 读进来的文件反而算不出修订号。
    """

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=allow_nan)


def canonical_json_bytes(value: object, *, allow_nan: bool = True) -> bytes:
    """规范化 JSON 的 UTF-8 字节，供既要摘要又要留存原文的调用方使用。"""

    return canonical_json(value, allow_nan=allow_nan).encode("utf-8")


def canonical_json_digest(value: object, *, allow_nan: bool = True) -> str:
    """规范化 JSON 的裸 hexdigest。"""

    return hashlib.sha256(canonical_json_bytes(value, allow_nan=allow_nan)).hexdigest()


def prefixed_canonical_json_digest(value: object, *, allow_nan: bool = True) -> str:
    """规范化 JSON 摘要，带算法前缀。"""

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
