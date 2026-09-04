"""内容摘要原语：文件字节与规范 JSON 的 sha256 单一实现。

产物清单、依据、检查点、profile 物化都要对「同一份内容」取同一个摘要，实现分散会让
口径悄悄漂移（chunk 大小、前缀、是否带长度）。本模块只提供最小原语，语义策略（比如带不带
``sha256-v1:`` 前缀、要不要连带文件长度）由各薄包装表达，读字节与哈希的循环只此一处。
"""

from __future__ import annotations

import hashlib
import json
import re
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


def sha256_file_with_size(path: Path) -> tuple[str, int]:
    """流式取文件摘要与字节数；路径不是常规文件时抛 ``FileNotFoundError``。"""

    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as handle:
        hexdigest, size, _ = digest_stream(handle.read)
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
    "HASH_ALGORITHM",
    "PREFIXED_DIGEST_RE",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_json_digest",
    "digest_stream",
    "prefixed",
    "prefixed_canonical_json_digest",
    "prefixed_sha256_file",
    "sha256_file",
    "sha256_file_with_size",
]
