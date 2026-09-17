"""市场源地址解析：用户输入 → 索引地址 + 规范键。

接受四种形态：``owner/repo``、``owner/repo@ref``、``https://github.com/owner/repo``（可带
``/tree/<ref>``）、以 ``arcreel-market.json`` 结尾的任意 ``https://`` 直链。GitHub 形态默认 ref
``HEAD``，解析到 raw 文件地址；规范键是 ``github:owner/repo@ref`` 或 ``url:<index_url>``，同键
视为同一个市场源。``http://``、本地路径与 ``git@`` 一律拒绝。纯逻辑，不发请求。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from .issues import INDEX_FILENAME

GITHUB_RAW_HOST = "raw.githubusercontent.com"
DEFAULT_GITHUB_REF = "HEAD"
#: 解析出的索引地址与规范键的长度上限，与 ``market_source`` 表对应列宽一致。
MAX_RESOLVED_LENGTH = 2048

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


class SourceAddressErrorCode(StrEnum):
    EMPTY = "empty"
    INSECURE_SCHEME = "insecure_scheme"
    UNSUPPORTED = "unsupported"


class SourceAddressError(ValueError):
    def __init__(self, code: SourceAddressErrorCode) -> None:
        super().__init__(f"market source address rejected: {code.value}")
        self.code = code


@dataclass(frozen=True)
class ResolvedSourceAddress:
    index_url: str
    canonical_key: str


def resolve_source_address(address: str) -> ResolvedSourceAddress:
    """解析一个市场源地址；不合法时抛 :class:`SourceAddressError`。"""
    text = address.strip()
    if not text:
        raise SourceAddressError(SourceAddressErrorCode.EMPTY)
    lowered = text.lower()
    if lowered.startswith("http://"):
        raise SourceAddressError(SourceAddressErrorCode.INSECURE_SCHEME)
    resolved = _resolve_https(text) if lowered.startswith("https://") else _resolve_shorthand(text)
    if max(len(resolved.index_url), len(resolved.canonical_key)) > MAX_RESOLVED_LENGTH:
        raise SourceAddressError(SourceAddressErrorCode.UNSUPPORTED)
    return resolved


def github_source(owner: str, repo: str, ref: str = DEFAULT_GITHUB_REF) -> ResolvedSourceAddress:
    return ResolvedSourceAddress(
        index_url=f"https://{GITHUB_RAW_HOST}/{owner}/{repo}/{ref}/{INDEX_FILENAME}",
        canonical_key=f"github:{owner}/{repo}@{ref}",
    )


def same_source_identity(left: str, right: str) -> bool:
    """比较规范键；GitHub owner/repo 忽略大小写，ref 保持大小写敏感。"""
    if left == right:
        return True
    prefix = "github:"
    if not left.startswith(prefix) or not right.startswith(prefix):
        return False
    left_repo, left_separator, left_ref = left.removeprefix(prefix).rpartition("@")
    right_repo, right_separator, right_ref = right.removeprefix(prefix).rpartition("@")
    return (
        bool(left_separator and right_separator)
        and left_repo.casefold() == right_repo.casefold()
        and left_ref == right_ref
    )


def _resolve_shorthand(text: str) -> ResolvedSourceAddress:
    repository, has_ref, ref = text.partition("@")
    parts = repository.split("/")
    if len(parts) != 2 or (has_ref and not ref):
        raise SourceAddressError(SourceAddressErrorCode.UNSUPPORTED)
    return _github(parts[0], parts[1], ref or DEFAULT_GITHUB_REF)


def _resolve_https(text: str) -> ResolvedSourceAddress:
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
        parts.port  # noqa: B018 -- 访问即校验端口，非法端口抛 ValueError
    except ValueError as exc:
        raise SourceAddressError(SourceAddressErrorCode.UNSUPPORTED) from exc
    if not hostname or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise SourceAddressError(SourceAddressErrorCode.UNSUPPORTED)
    if hostname in _GITHUB_HOSTS:
        return _resolve_github_page(parts.path)
    if not parts.path.endswith(f"/{INDEX_FILENAME}"):
        raise SourceAddressError(SourceAddressErrorCode.UNSUPPORTED)
    return ResolvedSourceAddress(index_url=text, canonical_key=f"url:{text}")


def _resolve_github_page(path: str) -> ResolvedSourceAddress:
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if len(segments) < 2:
        raise SourceAddressError(SourceAddressErrorCode.UNSUPPORTED)
    owner, repo = segments[0], segments[1].removesuffix(".git")
    rest = segments[2:]
    if not rest:
        return _github(owner, repo, DEFAULT_GITHUB_REF)
    if rest[0] != "tree" or len(rest) < 2:
        raise SourceAddressError(SourceAddressErrorCode.UNSUPPORTED)
    return _github(owner, repo, "/".join(rest[1:]))


def _github(owner: str, repo: str, ref: str) -> ResolvedSourceAddress:
    if not _OWNER_RE.match(owner) or not _REPO_RE.match(repo) or repo in {".", ".."}:
        raise SourceAddressError(SourceAddressErrorCode.UNSUPPORTED)
    if not _REF_RE.match(ref) or ".." in ref:
        raise SourceAddressError(SourceAddressErrorCode.UNSUPPORTED)
    return github_source(owner, repo, ref)
