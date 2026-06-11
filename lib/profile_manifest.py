"""Profile manifest sync system.

把 agent_runtime_profile/.claude/ 和 CLAUDE.md 同步到各项目 {project}/.claude/ +
CLAUDE.md。manifest + sha256 区分三种状态：未改的内置 skill / 用户修改 / 用户主动删除。

manifest 落在 ``{project_dir}/.arcreel_profile_manifest.json``（项目根，跨 .claude
和顶层 CLAUDE.md 一并管理）。schema 版本化，``profile_id`` 不匹配等价于 reset。

决策表共 15 行覆盖 ``{P 存/缺} × {D 存/缺} × {M 无/active/tombstone}``，由
``_apply_decision`` 用 match 实现 exhaustive，任何未列状态显式 NotImplementedError。
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, cast

import portalocker

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = ".arcreel_profile_manifest.json"
LOCK_FILENAME = ".profile_sync.lock"
MANIFEST_SCHEMA_VERSION = 1
EXPECTED_PROFILE_ID = "arcreel/builtin"
SHA256_CHUNK_BYTES = 64 * 1024
LOCK_TIMEOUT_SECONDS = 10

# profile 端要同步的两个根：``.claude/**`` 目录树 + 顶层 ``CLAUDE.md``
_PROFILE_TREE_ROOT = ".claude"
_PROFILE_TOP_FILE = "CLAUDE.md"


class ProfileMissingError(RuntimeError):
    """profile 目录不存在 → 部署错误。sync 拒绝运行以防 mass prune 所有项目。"""


class ProfileEmptyError(RuntimeError):
    """profile 目录无可同步文件 → 部署错误。同上拒绝运行。"""


class ProfileMisconfiguredError(RuntimeError):
    """profile 端变体文件不合法（成对缺失或与通用文件并存）→ 部署错误。sync 拒绝运行。"""


ContentMode = Literal["narration", "drama", "ad"]
VALID_CONTENT_MODES: frozenset[str] = frozenset({"narration", "drama", "ad"})


# ---------- 基础工具 ----------


def sha256_file(path: Path) -> str:
    """64KiB chunk 流式 sha256，避免大文件 OOM。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(SHA256_CHUNK_BYTES):
            h.update(chunk)
    return h.hexdigest()


def _is_skippable_dest(rel: str) -> bool:
    return rel in (MANIFEST_FILENAME, LOCK_FILENAME)


def _walk_files(root: Path, rel_to: Path) -> set[str]:
    out: set[str] = set()
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if p.is_file():
            out.add(p.relative_to(rel_to).as_posix())
    return out


def _parse_variant_suffix(rel: str) -> tuple[str, str | None]:
    """把 ``foo.narration.md`` 拆成 (logical_rel="foo.md", mode="narration")。
    非变体文件返回 (rel, None)。只识别 stem 中最后一段。
    """
    path = PurePosixPath(rel)
    # path.stem 是去掉最后一个扩展名的部分；再 split 一次拿"次外层后缀"
    stem_parts = path.stem.rsplit(".", 1)
    if len(stem_parts) == 2 and stem_parts[1] in VALID_CONTENT_MODES:
        # 重组为 logical_parent/<logical_stem><ext>；顶层文件时 parent == PurePosixPath('.')，
        # ``PurePosixPath('.') / 'foo.md'`` 仍然产生 ``PurePosixPath('foo.md')``，无需单独分支
        logical_name = stem_parts[0] + path.suffix
        return (path.parent / logical_name).as_posix(), stem_parts[1]
    return rel, None


def enumerate_profile_files(profile_dir: Path) -> set[str]:
    """profile 内所有源文件的 POSIX 相对路径集合（含 CLAUDE.<mode>.md 变体）。

    顶层只收 CLAUDE 家族：``CLAUDE.md`` + ``CLAUDE.<mode>.md``。其它顶层文件被
    刻意忽略，以与 ``enumerate_dest_files`` 口径对称——否则源端会扫到、目标端
    枚举不到，state machine 会把那些"目标缺失"的文件判进 tombstone 分支。
    扩展需要新增顶层逻辑文件时，应同时更新这里和 ``enumerate_dest_files``。
    """
    files: set[str] = set()
    if profile_dir.is_dir():
        for p in profile_dir.iterdir():
            if not p.is_file():
                continue
            logical, _ = _parse_variant_suffix(p.name)
            if logical == _PROFILE_TOP_FILE:
                files.add(p.name)
    files |= _walk_files(profile_dir / _PROFILE_TREE_ROOT, profile_dir)
    return files


def resolve_profile_files_for_mode(
    profile_dir: Path,
    content_mode: ContentMode,
) -> dict[str, str]:
    """把 profile 端文件树投影成 ``{logical_rel: source_rel}`` 映射。

    通用文件：logical_rel == source_rel。
    变体文件：仅保留匹配 ``content_mode`` 的一份，logical_rel 去掉 ``.<mode>`` 后缀。

    Raises:
        ValueError: content_mode 不在 ``VALID_CONTENT_MODES``
        ProfileMisconfiguredError: 任一变体配对缺失 / 通用+变体并存
    """
    if content_mode not in VALID_CONTENT_MODES:
        raise ValueError(f"content_mode must be one of {VALID_CONTENT_MODES}, got {content_mode!r}")

    profile_files = enumerate_profile_files(profile_dir)

    # variants[logical_rel][mode] = source_rel
    variants: dict[str, dict[str, str]] = {}
    commons: dict[str, str] = {}
    for src in profile_files:
        logical, mode = _parse_variant_suffix(src)
        if mode is None:
            commons[logical] = src
        else:
            variants.setdefault(logical, {})[mode] = src

    # 校验 1：通用 + 变体互斥
    collisions = set(commons) & set(variants)
    if collisions:
        sample = sorted(collisions)[0]
        raise ProfileMisconfiguredError(
            f"profile has both common and variant for {sample!r}; remove one. all collisions: {sorted(collisions)}"
        )

    # 校验 2：变体配对完整
    for logical, by_mode in variants.items():
        missing = VALID_CONTENT_MODES - set(by_mode)
        if missing:
            raise ProfileMisconfiguredError(
                f"profile variant {logical!r} missing variant for mode(s): {sorted(missing)}; "
                f"all variants of a logical file must exist together"
            )

    mapping: dict[str, str] = dict(commons)
    for logical, by_mode in variants.items():
        mapping[logical] = by_mode[content_mode]
    return mapping


@contextlib.contextmanager
def _project_lock(project_dir: Path):
    """``portalocker.Lock`` 对 path 内部 ``open()`` 会跟符号链接。攻击者预置
    ``.profile_sync.lock`` symlink → ``/etc/x`` 时，加锁阶段就会先 truncate 项目外
    文件。这里改用 ``os.open(O_CREAT|O_WRONLY|O_NOFOLLOW)`` 自己开 fd，确保
    symlink 形态的锁文件直接 ELOOP 拒绝；拿到真实 fd 后用 portalocker 的 lower-level
    ``lock()`` / ``unlink()`` 加锁，timeout 自轮询。

    Windows 上 ``os`` 没有 ``O_NOFOLLOW``（POSIX 专属），改用 lstat 预检 +
    无 flag 的 ``os.open`` 降级。Windows 创建 symlink 需要 SeCreateSymbolicLinkPrivilege
    或开发者模式，攻击模型本就低；预检与 open 之间的 TOCTOU 窗口与 ArcReel
    本地用户、portalocker 持锁的部署模型一致。
    """
    lock_path = project_dir / LOCK_FILENAME
    if lock_path.is_symlink():
        raise ValueError(f"lock path is a symlink, refusing: {lock_path}")
    o_nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY | o_nofollow, 0o600)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ValueError(f"lock path is a symlink, refusing: {lock_path}") from e
        raise
    f = os.fdopen(fd, "wb")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                portalocker.lock(f, portalocker.LOCK_EX | portalocker.LOCK_NB)
                break
            except (portalocker.AlreadyLocked, portalocker.LockException):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        try:
            yield
        finally:
            portalocker.unlock(f)
    finally:
        f.close()


def _ensure_dest_within(project_dir: Path, rel: str) -> Path:
    """解析 ``project_dir / rel`` 后必须仍在 ``project_dir`` 内，否则 ``raise``。

    防止恶意 symlink 把同步 I/O 引到项目根之外。攻击模型：导入的归档 / 用户手放
    的 symlink 让 ``.claude`` 或其祖先目录指向 ``/etc`` 等外部位置，sync 跑
    ``_safe_copy`` / ``_safe_unlink_if_file`` 时就会读写项目外文件。

    rel 本身已经过 ``_normalize_profile_rel_path``（force_resync）或 enumerate
    走文件系统真实路径（主入口），形态可信；这层只防 dest 端 symlink 跳板。
    """
    base = project_dir.resolve()
    # rel 路径段可能还未存在 → resolve() 走到第一个不存在段就停，仍正确反映前缀。
    candidate = (project_dir / rel).resolve()
    if candidate != base and not candidate.is_relative_to(base):
        raise ValueError(f"dest path escapes project_dir: {rel!r} → {candidate}")
    return candidate


def _normalize_profile_rel_path(rel: str) -> str:
    """force_resync 的 ``paths`` 来自 UI / 外部输入，必须拒掉绝对路径和 ``..``，
    否则 ``profile_dir / rel`` 和 ``project_dir / rel`` 会逃逸到 profile / 项目
    根目录之外，读写任意可写文件。

    校验规则：POSIX 相对路径，无 ``..``，不能是 manifest 自身或锁文件。
    返回规范化后的 POSIX 字符串。
    """
    if not isinstance(rel, str) or rel == "":
        raise ValueError(f"Invalid profile sync path: {rel!r}")
    pp = PurePosixPath(rel)
    # PurePosixPath 已折叠连续斜杠并剥掉 ``.`` 段，所以 ``parts`` 不可能含空字符串；
    # ``..`` 段会保留，必须显式拒掉以堵路径穿越。
    if pp.is_absolute() or ".." in pp.parts:
        raise ValueError(f"Invalid profile sync path: {rel!r}")
    out = pp.as_posix()
    if _is_skippable_dest(out):
        raise ValueError(f"Path not eligible for profile sync: {rel!r}")
    return out


def enumerate_dest_files(project_dir: Path) -> set[str]:
    """项目内 ``.claude/**`` + ``CLAUDE.md`` 集合，跳过 manifest 和锁文件自身。"""
    files: set[str] = set()
    if (project_dir / _PROFILE_TOP_FILE).is_file():
        files.add(_PROFILE_TOP_FILE)
    files |= {rel for rel in _walk_files(project_dir / _PROFILE_TREE_ROOT, project_dir) if not _is_skippable_dest(rel)}
    return files


# ---------- Manifest 数据类 ----------


@dataclasses.dataclass
class Manifest:
    schema_version: int
    profile_id: str
    entries: dict[str, dict]
    # None ≡ "未迁移": 来自 content_mode 字段引入前写的老 manifest；首次新 sync
    # 会通过 needs_migration 路径回填实际 mode，不触发破坏性 reset。
    # 非空 ≡ 上次 sync 时使用的 content_mode；与本次请求不一致会触发 reset。
    content_mode: ContentMode | None = None

    @classmethod
    def empty(cls) -> Manifest:
        return cls(
            schema_version=MANIFEST_SCHEMA_VERSION,
            profile_id=EXPECTED_PROFILE_ID,
            entries={},
            content_mode=None,
        )

    def to_jsonable(self) -> dict:
        data: dict = {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "entries": dict(sorted(self.entries.items())),
        }
        # None 时省略字段：兼容老 manifest 紧凑形态 + 减少 diff 噪音
        if self.content_mode is not None:
            data["content_mode"] = self.content_mode
        return data

    def normalized_bytes(self) -> bytes:
        """deterministic 序列化：sort_keys + indent + UTF-8，用于 diff 友好 + 写前比对。"""
        return json.dumps(
            self.to_jsonable(),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")


def load_manifest(project_dir: Path) -> tuple[Manifest, bytes] | None:
    """读 manifest 并返回 ``(manifest, raw_bytes)``。

    任一情况返回 None（触发首次迁移分支）：
    - 文件不存在
    - JSON 损坏
    - schema_version 不匹配（destructive wipe 比兼容旧版本逻辑干净）
    - profile_id 不匹配（换 profile = 换源 = 等价 reset）
    """
    path = project_dir / MANIFEST_FILENAME
    # symlink 形态的 manifest 拒绝信任：``read_bytes`` 会跟 symlink 读外部文件
    # （信息泄露 + 让 sync 基于错误的 manifest 决策）。视同损坏 → 走 reset。
    if path.is_symlink():
        logger.warning("manifest %s is a symlink, refusing to follow; will reset", path)
        return None
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    # 故意不吞 PermissionError / OSError —— 那些是真实 I/O 故障，
    # 静默 reset 会把暂时性问题升级成破坏性覆盖项目内 .claude/CLAUDE.md。
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("manifest %s corrupt, will reset", path)
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        logger.info("manifest %s schema_version mismatch, will reset", path)
        return None
    if data.get("profile_id") != EXPECTED_PROFILE_ID:
        logger.info("manifest %s profile_id mismatch, will reset", path)
        return None
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return None
    # 每条 entry 必须是 dict，且 active/tombstone 两类形状都得规整。
    # 不规整就视同损坏 manifest，走 reset 而不是让下游 _apply_decision
    # 撞 AttributeError 整体崩。
    for key, entry in entries.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            logger.warning("manifest %s has malformed entry %r, will reset", path, key)
            return None
        source = entry.get("source")
        if source == "profile":
            if not isinstance(entry.get("sha256"), str):
                logger.warning("manifest %s entry %s missing sha256, will reset", path, key)
                return None
        elif source == "tombstone":
            pass
        else:
            logger.warning("manifest %s entry %s unknown source=%r, will reset", path, key, source)
            return None
    raw_mode = data.get("content_mode")
    content_mode: ContentMode | None
    if raw_mode is None:
        content_mode = None
    elif isinstance(raw_mode, str) and raw_mode in VALID_CONTENT_MODES:
        content_mode = cast(ContentMode, raw_mode)
    else:
        logger.warning("manifest %s invalid content_mode=%r, will reset", path, raw_mode)
        return None
    return (
        Manifest(
            schema_version=data["schema_version"],
            profile_id=data["profile_id"],
            entries=entries,
            content_mode=content_mode,
        ),
        raw,
    )


def save_manifest(
    project_dir: Path,
    manifest: Manifest,
    original_bytes: bytes | None = None,
) -> bool:
    """原子写 + in-memory 写前比对。返回是否实际写盘。

    ``original_bytes is None``（首次迁移）直接落盘；否则规范化字节等于 original
    则跳过写。
    """
    new_bytes = manifest.normalized_bytes()
    if original_bytes is not None and new_bytes == original_bytes:
        return False
    path = project_dir / MANIFEST_FILENAME
    # 用 mkstemp 替代 ``path.with_suffix("...tmp")``：predictable 名字会被攻击者
    # 预置成 symlink → /etc/x，写时跟 symlink 截断项目外文件。mkstemp 用
    # O_CREAT|O_EXCL|O_RDWR + 不可预测名字 + same dir，跨平台拒绝 symlink。
    # os.replace 用 rename(2)，dst 是 symlink 也只替换 symlink 本体不跟 target。
    parent = path.parent
    fd, tmp_str = tempfile.mkstemp(prefix=MANIFEST_FILENAME + ".", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new_bytes)
        os.replace(tmp_str, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_str)
        raise
    return True


# ---------- entry / 时间戳工具 ----------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _profile_active_entry(sha: str, size: int) -> dict:
    return {"sha256": sha, "size": size, "source": "profile"}


def _tombstone_entry() -> dict:
    return {"source": "tombstone", "deleted_at": _now_iso()}


def _safe_unlink_if_file(path: Path) -> None:
    # ``is_file`` 跟符号链接（走 stat），``unlink`` 不跟（lstat）。组合起来的语义：
    # symlink to file → 识别成 True，unlink 删 symlink 本体而非 target；
    # 真 file → 识别 True + unlink。这是安全的，target 文件永远不会被误删。
    if path.is_file():
        path.unlink()


def _safe_copy(source: Path, dest: Path) -> None:
    """copy src → dest，先剥掉 symlink 形态的 dest。

    TOCTOU 防御：``_ensure_dest_within`` guard 之后到这里之间 dest 可能被外部进程
    race 替换成 symlink 指向项目外。若直接 ``shutil.copy2`` 会跟 symlink 写到项目外
    文件——即便参数本身是 resolve 过的绝对路径，``open()`` 写入时仍会按字符串
    解析。所以 dest 是 symlink 时先 ``unlink``（lstat 不解引用，删的是 symlink
    本体）再写，把 file-level race 窗口压到 unlink→open 之间的微秒级。

    残余风险：dest 父目录（``.claude`` 或更上层）被 race 替换成指向项目外的
    symlink 时仍跟。ArcReel 项目目录由 server 自创、普通用户无 shell + portalocker
    持锁，此攻击需要外部 root 进程，接受残余风险。彻底防需要 openat +
    O_NOFOLLOW 沿路径每级验证，工程成本超出攻击模型边界。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        dest.unlink()
    # 拒绝 dest 是真实目录的情况：``shutil.copy2(src, dest_dir)`` 会变成 copy 到
    # ``dest_dir/src.name`` 这种意外路径而不是失败。决策表里 rel 永远是文件级别，
    # 出现 dest 是目录 = 用户/归档预置的同名目录，应显式失败让上层计 errors。
    if dest.exists() and dest.is_dir():
        raise ValueError(f"dest is a directory, refusing copy: {dest}")
    shutil.copy2(source, dest)


def _new_stats() -> dict:
    return {
        # 向后兼容 4 个 key
        "created": 0,
        "repaired": 0,
        "skipped": 0,
        "errors": 0,
        # 新增 stat
        "upgraded": 0,
        "user_modified": 0,
        "user_only": 0,
        "pruned": 0,
        "orphaned": 0,
        "deleted_user": 0,
        "tombstoned": 0,
        "unchanged": 0,
        "collision": 0,
        "migrated_total": 0,
    }


# ---------- 首次迁移分支 ----------


def _full_reset_from_profile(
    profile_dir: Path,
    project_dir: Path,
    mapping: dict[str, str],
    *,
    content_mode: ContentMode | None = None,
) -> dict:
    """删除 dest，从 profile 全量物化，写 manifest baseline。

    场景：manifest 缺失 / 损坏 / schema_version 不匹配 / profile_id 不匹配 /
    content_mode 不匹配（destructive 切换模式）。

    Args:
        mapping: ``{logical_rel: source_rel}`` 映射，由 resolve_profile_files_for_mode 生成。
        content_mode: 写入 manifest 的 content_mode 字段；None 时省略（老 force_resync 路径）。
    """
    stats = _new_stats()

    # 清空 dest 端两个根——必须覆盖所有文件系统类型，否则后续 mkdir()/_safe_copy()
    # 会被"错误类型"的占位（普通文件 ``.claude`` / 目录 ``CLAUDE.md``）挡住，导致
    # reset 留下半完成状态 + 不完整 manifest。
    dest_tree = project_dir / _PROFILE_TREE_ROOT
    dest_top = project_dir / _PROFILE_TOP_FILE
    if dest_tree.is_symlink() or dest_tree.is_file():
        dest_tree.unlink()
    elif dest_tree.is_dir():
        shutil.rmtree(dest_tree)
    if dest_top.is_symlink() or dest_top.is_file():
        dest_top.unlink()
    elif dest_top.is_dir():
        shutil.rmtree(dest_top)

    manifest = Manifest.empty()
    for rel in sorted(mapping):
        source_rel = mapping[rel]
        source = profile_dir / source_rel
        try:
            _safe_copy(source, project_dir / rel)
            sha = sha256_file(source)
            size = source.stat().st_size
            manifest.entries[rel] = _profile_active_entry(sha, size)
            stats["migrated_total"] += 1
            stats["created"] += 1
        except OSError as e:
            logger.warning("profile reset skip %s: %s", rel, e)
            stats["errors"] += 1

    manifest.content_mode = content_mode
    save_manifest(project_dir, manifest, original_bytes=None)
    return stats


# ---------- 决策表（15 行 exhaustive match） ----------


def _apply_decision(
    profile_dir: Path,
    project_dir: Path,
    rel: str,
    source_rel: str,
    p_exists: bool,
    d_exists: bool,
    m: dict | None,
    manifest: Manifest,
    stats: dict,
) -> None:
    """对单个文件应用决策表。OSError 由调用方捕获。

    Args:
        rel: dest 端逻辑相对路径（无 .<mode> 后缀），用于写入 manifest.entries 和 dest 路径。
        source_rel: profile 端实际文件相对路径（可能带 .<mode> 后缀），用于读取 profile 文件。
    """
    if m is None:
        m_kind = "none"
    elif m.get("source") == "tombstone":
        m_kind = "tombstone"
    else:
        m_kind = "active"

    p = profile_dir / source_rel
    d = project_dir / rel
    # p_hash/p_size 只在 p_exists=True 的 match 分支中被读取，所以 not-exists 时给空字符串/0
    # 占位即可（永远不会出现在写入路径），同时让类型系统看到非 Optional，省掉 4 处 narrow assert。
    p_hash = sha256_file(p) if p_exists else ""
    p_size = p.stat().st_size if p_exists else 0
    d_hash: str | None = sha256_file(d) if d_exists else None
    m_hash: str | None = m.get("sha256") if (m_kind == "active" and m is not None) else None

    match (p_exists, d_exists, m_kind):
        case (True, False, "none"):
            # #1 首次下发
            _safe_copy(p, d)
            manifest.entries[rel] = _profile_active_entry(p_hash, p_size)
            stats["created"] += 1
        case (True, False, "active"):
            # #2 用户删过 active 内置 skill → 转 tombstone，不补回
            manifest.entries[rel] = _tombstone_entry()
            stats["deleted_user"] += 1
            stats["skipped"] += 1
        case (True, True, "active"):
            if d_hash == p_hash and m_hash == p_hash:
                # #3 三态一致
                stats["unchanged"] += 1
                stats["skipped"] += 1
            elif d_hash == m_hash and d_hash != p_hash:
                # #4 用户未改，profile 升级 → 覆盖 + 刷 manifest
                _safe_copy(p, d)
                manifest.entries[rel] = _profile_active_entry(p_hash, p_size)
                stats["upgraded"] += 1
                stats["repaired"] += 1
            elif d_hash != m_hash and d_hash == p_hash:
                # #5 状态机回流：用户改完恰好 = profile 当前版
                manifest.entries[rel] = _profile_active_entry(p_hash, p_size)
                stats["unchanged"] += 1
                stats["skipped"] += 1
            else:
                # #6 用户改过（d_hash != m_hash 且 != p_hash）
                stats["user_modified"] += 1
                stats["skipped"] += 1
        case (False, True, "active"):
            if d_hash == m_hash:
                # #7 profile 上游删，用户未改 → 同步删除 D + tombstone
                _safe_unlink_if_file(d)
                manifest.entries[rel] = _tombstone_entry()
                stats["pruned"] += 1
                stats["repaired"] += 1
            else:
                # #8 profile 上游删，用户改过 → 保留 D + 清 entry（不是 tombstone）
                manifest.entries.pop(rel, None)
                stats["orphaned"] += 1
                stats["skipped"] += 1
        case (False, True, "none"):
            # #9 项目独有 skill
            stats["user_only"] += 1
            stats["skipped"] += 1
        case (True, True, "tombstone"):
            # #10 用户删过又手动恢复 → 清 tombstone，下轮按 user_only
            manifest.entries.pop(rel, None)
            stats["user_only"] += 1
            stats["skipped"] += 1
        case (True, False, "tombstone"):
            # #11 稳态：用户已删 + profile 仍在
            stats["tombstoned"] += 1
            stats["skipped"] += 1
        case (False, True, "tombstone"):
            # #12 P 没了 tombstone 不适用，D 是孤儿 → 清 entry
            manifest.entries.pop(rel, None)
            stats["user_only"] += 1
            stats["skipped"] += 1
        case (False, False, "tombstone"):
            # #13 双方都没，tombstone 延续 → no-op
            stats["tombstoned"] += 1
            stats["skipped"] += 1
        case (False, False, "active"):
            # #14 双方同轮删 → 转 tombstone（隐含假设：D 缺=用户主动删，
            # 卷切换 / 故障导致 D 临时空时需 force_resync_profile 清 tombstone）
            manifest.entries[rel] = _tombstone_entry()
            stats["pruned"] += 1
            stats["repaired"] += 1
        case (True, True, "none"):
            # #15 命名碰撞：profile 新增 + 项目恰好已有同名
            if d_hash == p_hash:
                # 内容一致 → 视为已下发，写 active entry
                manifest.entries[rel] = _profile_active_entry(p_hash, p_size)
            # 内容不一致 → 保留 D，不写 entry（下轮归 #9 user_only）
            stats["collision"] += 1
            stats["skipped"] += 1
        case _:
            raise NotImplementedError(f"unreachable case: {p_exists=} {d_exists=} {m_kind=}")


# ---------- 公开 API ----------


def sync_profile_to_project(
    profile_dir: Path,
    project_dir: Path,
    content_mode: ContentMode,
) -> dict:
    """profile → project_dir 同步主入口。

    Raises:
        ProfileMissingError: profile 目录不存在
        ProfileEmptyError: profile 目录无可同步文件
        ProfileMisconfiguredError: 变体文件配置违规
        ValueError: content_mode 非 narration/drama
    """
    if not profile_dir.exists():
        raise ProfileMissingError(f"Profile dir not found: {profile_dir}")
    mapping = resolve_profile_files_for_mode(profile_dir, content_mode)
    if not mapping:
        raise ProfileEmptyError(f"Profile dir empty, likely deploy misconfig: {profile_dir}")

    project_dir.mkdir(parents=True, exist_ok=True)

    with _project_lock(project_dir):
        loaded = load_manifest(project_dir)
        if loaded is None:
            return _full_reset_from_profile(profile_dir, project_dir, mapping, content_mode=content_mode)
        manifest, original_bytes = loaded

        # mode_status 判定：missing(None)=needs_migration，存在但不匹配=mismatch → reset
        if manifest.content_mode is not None and manifest.content_mode != content_mode:
            logger.info(
                "manifest %s content_mode %r != requested %r, resetting",
                project_dir / MANIFEST_FILENAME,
                manifest.content_mode,
                content_mode,
            )
            return _full_reset_from_profile(profile_dir, project_dir, mapping, content_mode=content_mode)

        stats = _new_stats()
        dest_files = enumerate_dest_files(project_dir)
        all_keys = mapping.keys() | dest_files | manifest.entries.keys()

        for rel in sorted(all_keys):
            p_exists = rel in mapping
            d_exists = rel in dest_files
            m = manifest.entries.get(rel)
            try:
                _ensure_dest_within(project_dir, rel)
                _apply_decision(
                    profile_dir,
                    project_dir,
                    rel,
                    mapping.get(rel, rel),  # source_rel；rel 不在 mapping 时（p 不存在）用 rel 占位
                    p_exists,
                    d_exists,
                    m,
                    manifest,
                    stats,
                )
            except ValueError as e:
                logger.warning("profile sync skip %s (escape guard): %s", rel, e)
                stats["errors"] += 1
            except OSError as e:
                logger.warning("profile sync skip %s: %s", rel, e)
                stats["errors"] += 1

        # sync 主体完成后写入 mode（无论 needs_migration 还是 match）
        manifest.content_mode = content_mode
        save_manifest(project_dir, manifest, original_bytes)
        return stats


def force_resync_profile(
    profile_dir: Path,
    project_dir: Path,
    content_mode: ContentMode,
    *,
    paths: Iterable[str] | None = None,
) -> dict:
    """强制按 P 覆盖 D 并更新 manifest，清除 tombstone。

    给 UI"恢复内置 skill"按钮使用。``paths=None`` 表示全量。
    ``paths`` 是**逻辑路径**（如 ``CLAUDE.md``），内部按 content_mode 查源路径。
    """
    if not profile_dir.exists():
        raise ProfileMissingError(f"Profile dir not found: {profile_dir}")
    mapping = resolve_profile_files_for_mode(profile_dir, content_mode)
    if not mapping:
        raise ProfileEmptyError(f"Profile dir empty, likely deploy misconfig: {profile_dir}")

    if paths is not None:
        target = {_normalize_profile_rel_path(rel) for rel in paths}
    else:
        target = set(mapping)

    project_dir.mkdir(parents=True, exist_ok=True)

    with _project_lock(project_dir):
        loaded = load_manifest(project_dir)
        if loaded is None:
            if paths is None:
                return _full_reset_from_profile(profile_dir, project_dir, mapping, content_mode=content_mode)
            manifest, original_bytes = Manifest.empty(), None
        else:
            manifest, original_bytes = loaded

        stats = _new_stats()
        for rel in sorted(target):
            source_rel = mapping.get(rel)
            p = profile_dir / source_rel if source_rel else None
            if p is None or not p.is_file():
                logger.warning("force_resync skip missing profile file: %s", rel)
                continue
            try:
                _ensure_dest_within(project_dir, rel)
                d = project_dir / rel
                _safe_copy(p, d)
                sha = sha256_file(p)
                manifest.entries[rel] = _profile_active_entry(sha, p.stat().st_size)
                stats["created"] += 1
                stats["repaired"] += 1
            except ValueError as e:
                logger.warning("force_resync skip %s (escape guard): %s", rel, e)
                stats["errors"] += 1
            except OSError as e:
                logger.warning("force_resync skip %s: %s", rel, e)
                stats["errors"] += 1

        manifest.content_mode = content_mode
        save_manifest(project_dir, manifest, original_bytes)
        return stats
