"""Tests for ``lib.profile_manifest`` module utilities.

仅覆盖 manifest 模块本身的 utility（sha256、load/save、enumerate、deterministic
序列化、schema_version 兼容性）。决策表 15 行的端到端测试在
``tests/test_project_manager_symlink.py`` 里覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.profile_manifest import (
    EXPECTED_PROFILE_ID,
    LOCK_FILENAME,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    Manifest,
    _normalize_profile_rel_path,
    enumerate_dest_files,
    enumerate_profile_files,
    load_manifest,
    save_manifest,
    sha256_file,
)

# ---------- sha256 ----------


def test_sha256_file_streaming_64kib_chunks(tmp_path: Path) -> None:
    """流式读避免大文件 OOM；结果应与标准 hashlib 一致。"""
    import hashlib

    big = tmp_path / "big.bin"
    payload = b"abc" * (256 * 1024)  # ~750KB，超过单个 64KiB chunk
    big.write_bytes(payload)
    assert sha256_file(big) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_empty(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.touch()
    # sha256("") = e3b0c4...
    assert sha256_file(empty).startswith("e3b0c442")


# ---------- enumerate ----------


def test_enumerate_profile_files_includes_top_md_and_claude_tree(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    (profile / ".claude" / "skills" / "demo").mkdir(parents=True)
    (profile / ".claude" / "skills" / "demo" / "SKILL.md").write_text("x")
    (profile / "CLAUDE.md").write_text("top")

    files = enumerate_profile_files(profile)
    assert files == {"CLAUDE.md", ".claude/skills/demo/SKILL.md"}


def test_enumerate_profile_files_empty_when_missing_roots(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    assert enumerate_profile_files(profile) == set()


def test_enumerate_dest_files_skips_manifest_self_and_lock(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "skill.md").write_text("x")
    (project / "CLAUDE.md").write_text("top")
    (project / MANIFEST_FILENAME).write_text("{}")
    (project / LOCK_FILENAME).write_text("")

    files = enumerate_dest_files(project)
    assert files == {"CLAUDE.md", ".claude/skill.md"}
    assert MANIFEST_FILENAME not in files
    assert LOCK_FILENAME not in files


def test_enumerate_files_uses_posix_separator(tmp_path: Path) -> None:
    """跨平台共享 docker volume 时 manifest key 不能用反斜杠。"""
    profile = tmp_path / "profile"
    (profile / ".claude" / "skills" / "a" / "b").mkdir(parents=True)
    (profile / ".claude" / "skills" / "a" / "b" / "c.md").write_text("x")

    files = enumerate_profile_files(profile)
    assert ".claude/skills/a/b/c.md" in files
    # 所有 key 都不能含反斜杠（即便 Windows 上 Path 用 \）
    for rel in files:
        assert "\\" not in rel


# ---------- Manifest dataclass ----------


def test_manifest_normalized_bytes_deterministic_sort_keys(tmp_path: Path) -> None:
    """同一份 manifest 多次序列化字节相等。"""
    m1 = Manifest.empty()
    m1.entries["b.md"] = {"sha256": "bb", "size": 2, "source": "profile"}
    m1.entries["a.md"] = {"sha256": "aa", "size": 1, "source": "profile"}

    m2 = Manifest.empty()
    m2.entries["a.md"] = {"sha256": "aa", "size": 1, "source": "profile"}
    m2.entries["b.md"] = {"sha256": "bb", "size": 2, "source": "profile"}

    assert m1.normalized_bytes() == m2.normalized_bytes()
    # entry 顺序也应在序列化时按 key 字典序
    text = m1.normalized_bytes().decode("utf-8")
    assert text.index('"a.md"') < text.index('"b.md"')


def test_manifest_no_top_level_synced_at_field() -> None:
    """schema 健康度：顶层不能有 synced_at（避免每次启动重写 + git diff 污染）。"""
    m = Manifest.empty()
    data = json.loads(m.normalized_bytes())
    assert "synced_at" not in data


def test_manifest_entries_no_per_entry_synced_at_field() -> None:
    """entry 内也不能有 synced_at（同上）。tombstone 的 deleted_at 是写一次稳定值，不算。"""
    m = Manifest.empty()
    m.entries["x"] = {"sha256": "h", "size": 1, "source": "profile"}
    data = json.loads(m.normalized_bytes())
    assert "synced_at" not in data["entries"]["x"]


def test_manifest_profile_id_present(tmp_path: Path) -> None:
    m = Manifest.empty()
    data = json.loads(m.normalized_bytes())
    assert data["profile_id"] == EXPECTED_PROFILE_ID


# ---------- load / save ----------


def test_load_manifest_missing_returns_none(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    assert load_manifest(project) is None


def test_load_manifest_corrupt_returns_none(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / MANIFEST_FILENAME).write_text("{not json")
    assert load_manifest(project) is None


def test_load_manifest_schema_version_mismatch_returns_none(tmp_path: Path) -> None:
    """未来 schema 演进时硬升级路径：版本不匹配 → reset。"""
    project = tmp_path / "proj"
    project.mkdir()
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION + 99,
        "profile_id": EXPECTED_PROFILE_ID,
        "entries": {},
    }
    (project / MANIFEST_FILENAME).write_text(json.dumps(payload))
    assert load_manifest(project) is None


def test_load_manifest_profile_id_mismatch_returns_none(tmp_path: Path) -> None:
    """换 profile = 换源 = 等价 reset。"""
    project = tmp_path / "proj"
    project.mkdir()
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": "other/foo",
        "entries": {},
    }
    (project / MANIFEST_FILENAME).write_text(json.dumps(payload))
    assert load_manifest(project) is None


def test_load_manifest_roundtrip_returns_raw_bytes(tmp_path: Path) -> None:
    """load 返回 (manifest, raw_bytes) tuple，raw 用于写前比对。"""
    project = tmp_path / "proj"
    project.mkdir()
    m = Manifest.empty()
    m.entries["x"] = {"sha256": "h", "size": 1, "source": "profile"}
    save_manifest(project, m)

    loaded = load_manifest(project)
    assert loaded is not None
    loaded_m, raw = loaded
    assert loaded_m.entries == m.entries
    assert raw == m.normalized_bytes()


def test_save_manifest_atomic_via_tmp_then_rename(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    m = Manifest.empty()
    save_manifest(project, m)
    assert (project / MANIFEST_FILENAME).exists()
    # 不应留下 .tmp
    assert not (project / (MANIFEST_FILENAME + ".tmp")).exists()


def test_save_manifest_skips_write_when_unchanged(tmp_path: Path) -> None:
    """写前比对：盘上字节等于新规范化字节 → 跳过原子写。"""
    project = tmp_path / "proj"
    project.mkdir()
    m = Manifest.empty()
    save_manifest(project, m)
    raw = (project / MANIFEST_FILENAME).read_bytes()

    # 用 original_bytes 调，传入相同 manifest → 应返回 False
    wrote = save_manifest(project, m, original_bytes=raw)
    assert wrote is False


def test_save_manifest_writes_when_changed(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    m = Manifest.empty()
    save_manifest(project, m)
    raw = (project / MANIFEST_FILENAME).read_bytes()

    m.entries["new.md"] = {"sha256": "h", "size": 1, "source": "profile"}
    wrote = save_manifest(project, m, original_bytes=raw)
    assert wrote is True
    new_raw = (project / MANIFEST_FILENAME).read_bytes()
    assert new_raw != raw


def test_save_manifest_first_write_no_original_bytes(tmp_path: Path) -> None:
    """首次迁移分支：manifest 新建，无 original_bytes → 必须落盘。"""
    project = tmp_path / "proj"
    project.mkdir()
    m = Manifest.empty()
    wrote = save_manifest(project, m, original_bytes=None)
    assert wrote is True
    assert (project / MANIFEST_FILENAME).exists()


# ---------- schema validation ----------


def test_load_manifest_entries_not_dict_returns_none(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "entries": ["not", "a", "dict"],
    }
    (project / MANIFEST_FILENAME).write_text(json.dumps(payload))
    assert load_manifest(project) is None


@pytest.mark.parametrize("garbage", ["null", "[]", '"string"', "42"])
def test_load_manifest_top_level_not_object_returns_none(tmp_path: Path, garbage: str) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / MANIFEST_FILENAME).write_text(garbage)
    assert load_manifest(project) is None


@pytest.mark.parametrize(
    "bad_entry",
    [
        "string-not-dict",
        42,
        ["list"],
        None,
    ],
)
def test_load_manifest_entry_not_dict_returns_none(tmp_path: Path, bad_entry) -> None:
    """entry value 非 dict → 当作损坏走 reset，而不是让 _apply_decision 撞 AttributeError。"""
    project = tmp_path / "proj"
    project.mkdir()
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "entries": {".claude/x.md": bad_entry},
    }
    (project / MANIFEST_FILENAME).write_text(json.dumps(payload))
    assert load_manifest(project) is None


def test_load_manifest_entry_unknown_source_returns_none(tmp_path: Path) -> None:
    """未知 ``source`` 值 → reset，避免静默忽略漂移到第三类状态。"""
    project = tmp_path / "proj"
    project.mkdir()
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "entries": {".claude/x.md": {"source": "alien", "sha256": "abc"}},
    }
    (project / MANIFEST_FILENAME).write_text(json.dumps(payload))
    assert load_manifest(project) is None


def test_load_manifest_profile_entry_without_sha_returns_none(tmp_path: Path) -> None:
    """source=profile 但缺 sha256 → 当作损坏走 reset。"""
    project = tmp_path / "proj"
    project.mkdir()
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "entries": {".claude/x.md": {"source": "profile"}},
    }
    (project / MANIFEST_FILENAME).write_text(json.dumps(payload))
    assert load_manifest(project) is None


def test_load_manifest_permission_error_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PermissionError / 其他 OSError 必须向上抛，不能被吞成 None 触发破坏性 reset。

    场景：磁盘暂时性 I/O 故障 / 文件被其他进程锁 / 权限被改坏。这些都不应升级成
    "manifest 缺失 → 全量重置 .claude/CLAUDE.md"。
    """
    project = tmp_path / "proj"
    project.mkdir()
    (project / MANIFEST_FILENAME).write_text("{}")

    from pathlib import Path as _PathCls

    original_read_bytes = _PathCls.read_bytes

    def _raise(self):
        if self.name == MANIFEST_FILENAME:
            raise PermissionError(13, "denied", str(self))
        return original_read_bytes(self)

    monkeypatch.setattr(_PathCls, "read_bytes", _raise)
    with pytest.raises(PermissionError):
        load_manifest(project)


# ---------- _normalize_profile_rel_path ----------


@pytest.mark.parametrize(
    "evil",
    [
        "../escape",
        ".claude/../../etc/passwd",
        "/etc/passwd",
        MANIFEST_FILENAME,
        LOCK_FILENAME,
        "",
    ],
)
def test_normalize_rel_path_rejects_traversal_and_self(evil: str) -> None:
    """绝对路径 / `..` / manifest 自身 / 空串都必须拒。"""
    with pytest.raises(ValueError, match="profile sync"):
        _normalize_profile_rel_path(evil)


@pytest.mark.parametrize("bad", [None, 42, [], {}])
def test_normalize_rel_path_rejects_non_string(bad) -> None:
    """非 str 输入直接拒，避免下游撞 TypeError。"""
    with pytest.raises(ValueError, match="profile sync"):
        _normalize_profile_rel_path(bad)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (".claude/skills/demo/SKILL.md", ".claude/skills/demo/SKILL.md"),
        # 连续斜杠：PurePosixPath 会折叠，规范化输出无双斜杠且 path 合法
        ("a//b", "a/b"),
        # `.` 段：PurePosixPath 会剥掉，结果与无 `.` 等价
        ("a/./b", "a/b"),
    ],
)
def test_normalize_rel_path_accepts_and_canonicalizes(raw: str, expected: str) -> None:
    """合法相对路径直接返回 POSIX 形式；pathlib 自带的规范化（折叠 ``//``、剥 ``.``）足够。

    特别覆盖 CodeRabbit 二轮建议的 ``a//b`` 用例：经 PurePosixPath 折叠后 parts 不含
    空段，所以 ``_normalize_profile_rel_path`` 中的 ``..`` 检查不会被空段触发，
    该路径被视为合法 → 这一行为证明早前的 ``part == ""`` 检查是 unreachable。
    """
    assert _normalize_profile_rel_path(raw) == expected
