"""Profile manifest sync system.

把 agent_runtime_profile/.claude/ 和 CLAUDE.md 同步到各项目 {project}/.claude/ +
CLAUDE.md。manifest + sha256 区分三种状态：未改的内置 skill / 用户修改 / 用户主动删除。

manifest 落在 ``{project_dir}/.arcreel_profile_manifest.json``（项目根，跨 .claude
和顶层 CLAUDE.md 一并管理）。schema 版本化，``profile_id`` 不匹配等价于 reset。

决策表共 15 行覆盖 ``{P 存/缺} × {D 存/缺} × {M 无/active/tombstone}``，由
``_apply_decision`` 用 match 实现 exhaustive，任何未列状态显式 NotImplementedError。

完整规格见: /Users/pollochen/.claude/plans/temporal-foraging-tulip.md
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

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


def enumerate_profile_files(profile_dir: Path) -> set[str]:
    """profile 内所有要同步文件的 POSIX 相对路径集合。"""
    files: set[str] = set()
    if (profile_dir / _PROFILE_TOP_FILE).is_file():
        files.add(_PROFILE_TOP_FILE)
    files |= _walk_files(profile_dir / _PROFILE_TREE_ROOT, profile_dir)
    return files


def _normalize_profile_rel_path(rel: str) -> str:
    """force_resync 的 ``paths`` 来自 UI / 外部输入，必须拒掉绝对路径和 ``..``，
    否则 ``profile_dir / rel`` 和 ``project_dir / rel`` 会逃逸到 profile / 项目
    根目录之外，读写任意可写文件。

    校验规则：POSIX 相对路径，无空段，无 ``..``，不能是 manifest 自身或锁文件。
    返回规范化后的 POSIX 字符串。
    """
    if not isinstance(rel, str) or rel == "":
        raise ValueError(f"Invalid profile sync path: {rel!r}")
    pp = PurePosixPath(rel)
    if pp.is_absolute() or any(part in ("", "..") for part in pp.parts):
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

    @classmethod
    def empty(cls) -> Manifest:
        return cls(
            schema_version=MANIFEST_SCHEMA_VERSION,
            profile_id=EXPECTED_PROFILE_ID,
            entries={},
        )

    def to_jsonable(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "entries": dict(sorted(self.entries.items())),
        }

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
    return (
        Manifest(
            schema_version=data["schema_version"],
            profile_id=data["profile_id"],
            entries=entries,
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(new_bytes)
    tmp.replace(path)
    return True


# ---------- entry / 时间戳工具 ----------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _profile_active_entry(sha: str, size: int) -> dict:
    return {"sha256": sha, "size": size, "source": "profile"}


def _tombstone_entry() -> dict:
    return {"source": "tombstone", "deleted_at": _now_iso()}


def _safe_unlink_if_file(path: Path) -> None:
    if path.is_file():
        path.unlink()


def _safe_copy(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
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
    profile_files: set[str],
) -> dict:
    """删除 dest，从 profile 全量物化，写 manifest baseline。

    场景：manifest 缺失 / 损坏 / schema_version 不匹配 / profile_id 不匹配。
    用户决策"忽略已有"——目前无产品级 skill 编辑途径，直接 reset 安全。
    """
    stats = _new_stats()

    # 清空 dest 端两个根
    dest_tree = project_dir / _PROFILE_TREE_ROOT
    dest_top = project_dir / _PROFILE_TOP_FILE
    if dest_tree.is_symlink():
        dest_tree.unlink()
    elif dest_tree.is_dir():
        shutil.rmtree(dest_tree)
    if dest_top.is_symlink() or dest_top.is_file():
        dest_top.unlink()

    manifest = Manifest.empty()
    for rel in sorted(profile_files):
        try:
            _safe_copy(profile_dir / rel, project_dir / rel)
            sha = sha256_file(profile_dir / rel)
            size = (profile_dir / rel).stat().st_size
            manifest.entries[rel] = _profile_active_entry(sha, size)
            stats["migrated_total"] += 1
            stats["created"] += 1
        except OSError as e:
            logger.warning("profile reset skip %s: %s", rel, e)
            stats["errors"] += 1

    save_manifest(project_dir, manifest, original_bytes=None)
    return stats


# ---------- 决策表（15 行 exhaustive match） ----------


def _apply_decision(
    profile_dir: Path,
    project_dir: Path,
    rel: str,
    p_exists: bool,
    d_exists: bool,
    m: dict | None,
    manifest: Manifest,
    stats: dict,
) -> None:
    """对单个文件应用决策表。OSError 由调用方捕获。

    决策表完整定义见 plan: temporal-foraging-tulip.md §同步算法/决策矩阵
    """
    if m is None:
        m_kind = "none"
    elif m.get("source") == "tombstone":
        m_kind = "tombstone"
    else:
        m_kind = "active"

    p = profile_dir / rel
    d = project_dir / rel
    p_hash = sha256_file(p) if p_exists else None
    p_size = p.stat().st_size if p_exists else None
    d_hash = sha256_file(d) if d_exists else None
    m_hash = m.get("sha256") if m_kind == "active" else None

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


def sync_profile_to_project(profile_dir: Path, project_dir: Path) -> dict:
    """profile → project_dir 同步主入口。

    Raises:
        ProfileMissingError: profile 目录不存在
        ProfileEmptyError: profile 目录无可同步文件
    """
    if not profile_dir.exists():
        raise ProfileMissingError(f"Profile dir not found: {profile_dir}")
    profile_files = enumerate_profile_files(profile_dir)
    if not profile_files:
        raise ProfileEmptyError(f"Profile dir empty, likely deploy misconfig: {profile_dir}")

    project_dir.mkdir(parents=True, exist_ok=True)
    lock_path = project_dir / LOCK_FILENAME

    # 默认 flags=EXCLUSIVE|NON_BLOCKING 让 timeout 真正生效（轮询直到拿到锁或超时）
    with portalocker.Lock(lock_path, "w", timeout=LOCK_TIMEOUT_SECONDS):
        loaded = load_manifest(project_dir)
        if loaded is None:
            return _full_reset_from_profile(profile_dir, project_dir, profile_files)
        manifest, original_bytes = loaded

        stats = _new_stats()
        dest_files = enumerate_dest_files(project_dir)
        all_keys = profile_files | dest_files | set(manifest.entries.keys())

        for rel in sorted(all_keys):
            p_exists = rel in profile_files
            d_exists = rel in dest_files
            m = manifest.entries.get(rel)
            try:
                _apply_decision(
                    profile_dir,
                    project_dir,
                    rel,
                    p_exists,
                    d_exists,
                    m,
                    manifest,
                    stats,
                )
            except OSError as e:
                logger.warning("profile sync skip %s: %s", rel, e)
                stats["errors"] += 1

        save_manifest(project_dir, manifest, original_bytes)
        return stats


def force_resync_profile(
    profile_dir: Path,
    project_dir: Path,
    *,
    paths: Iterable[str] | None = None,
) -> dict:
    """强制按 P 覆盖 D 并更新 manifest，清除 tombstone。

    给 UI"恢复内置 skill"按钮使用。``paths=None`` 表示全量。
    边界：若指定 paths 中某文件 profile 已删（P 不存在）→ skip + log warn，不算 error。
    意图是"恢复"不是"删"，强行走 #7 删除会与用户意图反向。
    """
    if not profile_dir.exists():
        raise ProfileMissingError(f"Profile dir not found: {profile_dir}")
    profile_files = enumerate_profile_files(profile_dir)
    # 镜像主入口的空 profile 防御：profile 存在但无文件 + paths=None + 无 manifest
    # 时若不抛会调 _full_reset 把项目清空；paths 非空的语义"恢复"同样不能在空源下成立。
    if not profile_files:
        raise ProfileEmptyError(f"Profile dir empty, likely deploy misconfig: {profile_dir}")

    # paths 来自外部 → 必须先校验拒掉路径穿越，再用 set 化
    if paths is not None:
        target = {_normalize_profile_rel_path(rel) for rel in paths}
    else:
        target = profile_files

    project_dir.mkdir(parents=True, exist_ok=True)
    lock_path = project_dir / LOCK_FILENAME

    # 默认 flags=EXCLUSIVE|NON_BLOCKING 让 timeout 真正生效（轮询直到拿到锁或超时）
    with portalocker.Lock(lock_path, "w", timeout=LOCK_TIMEOUT_SECONDS):
        loaded = load_manifest(project_dir)
        if loaded is None:
            # 等价于首次接入，行为与 reset 一致
            return _full_reset_from_profile(profile_dir, project_dir, profile_files)
        manifest, original_bytes = loaded

        stats = _new_stats()
        for rel in sorted(target):
            p = profile_dir / rel
            if not p.is_file():
                logger.warning("force_resync skip missing profile file: %s", rel)
                continue
            d = project_dir / rel
            try:
                _safe_copy(p, d)
                sha = sha256_file(p)
                manifest.entries[rel] = _profile_active_entry(sha, p.stat().st_size)
                stats["created"] += 1
                stats["repaired"] += 1
            except OSError as e:
                logger.warning("force_resync skip %s: %s", rel, e)
                stats["errors"] += 1

        save_manifest(project_dir, manifest, original_bytes)
        return stats
