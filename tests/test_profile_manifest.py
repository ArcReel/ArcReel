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


def test_enumerate_profile_files_includes_claude_variants(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "CLAUDE.narration.md").write_text("n")
    (profile / "CLAUDE.drama.md").write_text("d")

    files = enumerate_profile_files(profile)
    assert files == {"CLAUDE.narration.md", "CLAUDE.drama.md"}


def test_enumerate_profile_files_ignores_unrelated_top_files(tmp_path: Path) -> None:
    """与 enumerate_dest_files 对称：源端只收 CLAUDE 家族，避免顶层加新文件
    时把目标当作 d_exists=False 误判进 tombstone 分支。"""
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "CLAUDE.md").write_text("top")
    (profile / "README.md").write_text("noise")
    (profile / "CHANGELOG.md").write_text("noise")
    (profile / "foo.narration.md").write_text("noise variant")  # 非 CLAUDE 家族变体

    files = enumerate_profile_files(profile)
    assert files == {"CLAUDE.md"}


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


# ---------- ProfileMisconfiguredError ----------


def test_profile_misconfigured_error_is_runtime_error() -> None:
    """与 ProfileMissingError / ProfileEmptyError 同层级，都是部署级错误。"""
    from lib.profile_manifest import ProfileMisconfiguredError

    assert issubclass(ProfileMisconfiguredError, RuntimeError)


def test_valid_content_modes_constant() -> None:
    from lib.profile_manifest import VALID_CONTENT_MODES

    assert VALID_CONTENT_MODES == frozenset({"narration", "drama", "ad"})


# ---------- resolve_profile_files_for_mode ----------


def _make_profile(tmp_path: Path) -> Path:
    """构造典型 profile：通用文件 + narration/drama/ad 变体配对。"""
    profile = tmp_path / "profile"
    (profile / ".claude" / "skills" / "manga-workflow").mkdir(parents=True)
    (profile / ".claude" / "agents").mkdir(parents=True)
    # 通用文件
    (profile / ".claude" / "agents" / "generate-assets.md").write_text("common")
    # CLAUDE.md 变体配对
    (profile / "CLAUDE.narration.md").write_text("narration top")
    (profile / "CLAUDE.drama.md").write_text("drama top")
    (profile / "CLAUDE.ad.md").write_text("ad top")
    # SKILL.md 变体配对
    (profile / ".claude" / "skills" / "manga-workflow" / "SKILL.narration.md").write_text("nar skill")
    (profile / ".claude" / "skills" / "manga-workflow" / "SKILL.drama.md").write_text("dra skill")
    (profile / ".claude" / "skills" / "manga-workflow" / "SKILL.ad.md").write_text("ad skill")
    return profile


def test_resolve_for_narration_picks_narration_variants(tmp_path: Path) -> None:
    from lib.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    mapping = resolve_profile_files_for_mode(profile, "narration")

    assert mapping == {
        "CLAUDE.md": "CLAUDE.narration.md",
        ".claude/agents/generate-assets.md": ".claude/agents/generate-assets.md",
        ".claude/skills/manga-workflow/SKILL.md": ".claude/skills/manga-workflow/SKILL.narration.md",
    }


def test_resolve_for_drama_picks_drama_variants(tmp_path: Path) -> None:
    from lib.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    mapping = resolve_profile_files_for_mode(profile, "drama")

    assert mapping[".claude/skills/manga-workflow/SKILL.md"] == ".claude/skills/manga-workflow/SKILL.drama.md"
    assert mapping["CLAUDE.md"] == "CLAUDE.drama.md"


def test_resolve_for_ad_picks_ad_variants(tmp_path: Path) -> None:
    from lib.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    mapping = resolve_profile_files_for_mode(profile, "ad")

    assert mapping["CLAUDE.md"] == "CLAUDE.ad.md"
    assert mapping[".claude/skills/manga-workflow/SKILL.md"] == ".claude/skills/manga-workflow/SKILL.ad.md"


def test_repo_profile_resolves_for_every_content_mode() -> None:
    """仓库内置 profile 的变体配对必须覆盖全部 content_mode——任一模式建项目都能物化。"""
    from lib.profile_manifest import VALID_CONTENT_MODES, resolve_profile_files_for_mode

    repo_profile = Path(__file__).resolve().parent.parent / "agent_runtime_profile"
    for mode in sorted(VALID_CONTENT_MODES):
        mapping = resolve_profile_files_for_mode(repo_profile, mode)  # type: ignore[arg-type]
        assert mapping["CLAUDE.md"] == f"CLAUDE.{mode}.md"
        assert mapping[".claude/skills/manga-workflow/SKILL.md"] == f".claude/skills/manga-workflow/SKILL.{mode}.md"


def test_sync_ad_project_writes_ad_variant(tmp_path: Path) -> None:
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = tmp_path / "proj_root_ad"
    project.mkdir(parents=True)

    sync_profile_to_project(profile, project, content_mode="ad")

    assert (project / "CLAUDE.md").read_text() == "ad top"
    assert (project / ".claude" / "skills" / "manga-workflow" / "SKILL.md").read_text() == "ad skill"
    assert not (project / "CLAUDE.ad.md").exists()


def test_resolve_unpaired_variant_raises(tmp_path: Path) -> None:
    """只有 narration 变体没有 drama 变体 → ProfileMisconfiguredError。"""
    from lib.profile_manifest import ProfileMisconfiguredError, resolve_profile_files_for_mode

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "CLAUDE.narration.md").write_text("only narration")

    with pytest.raises(ProfileMisconfiguredError, match="missing variant"):
        resolve_profile_files_for_mode(profile, "narration")


def test_resolve_common_plus_variant_collision_raises(tmp_path: Path) -> None:
    """同一 logical_rel 既有通用文件又有变体 → ProfileMisconfiguredError。"""
    from lib.profile_manifest import ProfileMisconfiguredError, resolve_profile_files_for_mode

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "CLAUDE.md").write_text("common")
    (profile / "CLAUDE.narration.md").write_text("variant")
    (profile / "CLAUDE.drama.md").write_text("variant")

    with pytest.raises(ProfileMisconfiguredError, match="common.*variant"):
        resolve_profile_files_for_mode(profile, "narration")


def test_resolve_invalid_mode_raises(tmp_path: Path) -> None:
    from lib.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    with pytest.raises(ValueError, match="content_mode"):
        resolve_profile_files_for_mode(profile, "reference_video")  # type: ignore[arg-type]


def test_resolve_double_dot_filename_not_treated_as_variant(tmp_path: Path) -> None:
    """`foo.narration.bar.md` 不认作变体（只识别最后一段 stem）。"""
    from lib.profile_manifest import resolve_profile_files_for_mode

    profile = tmp_path / "profile"
    (profile / ".claude").mkdir(parents=True)
    (profile / ".claude" / "weird.narration.bar.md").write_text("not a variant")

    mapping = resolve_profile_files_for_mode(profile, "narration")
    assert mapping == {".claude/weird.narration.bar.md": ".claude/weird.narration.bar.md"}


# ---------- Manifest.content_mode 字段 ----------


def test_manifest_empty_has_none_content_mode() -> None:
    m = Manifest.empty()
    assert m.content_mode is None


def test_manifest_serialize_omits_none_content_mode() -> None:
    """None content_mode 不出现在 JSON 中，保持向后兼容紧凑形态。"""
    m = Manifest.empty()
    data = json.loads(m.normalized_bytes().decode("utf-8"))
    assert "content_mode" not in data


def test_manifest_serialize_includes_set_content_mode() -> None:
    m = Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        profile_id=EXPECTED_PROFILE_ID,
        content_mode="narration",
        entries={},
    )
    data = json.loads(m.normalized_bytes().decode("utf-8"))
    assert data["content_mode"] == "narration"


def test_load_manifest_legacy_no_content_mode_field(tmp_path: Path) -> None:
    """老 manifest（无 content_mode 字段）→ load 成功，字段为 None。"""
    legacy = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "entries": {},
    }
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(legacy))
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    manifest, _raw = loaded
    assert manifest.content_mode is None


def test_load_manifest_new_with_content_mode(tmp_path: Path) -> None:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "content_mode": "drama",
        "entries": {},
    }
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(payload))
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    manifest, _raw = loaded
    assert manifest.content_mode == "drama"


def test_load_manifest_invalid_content_mode_returns_none(tmp_path: Path) -> None:
    """content_mode 字段存在但值非法 → 视为损坏，触发 reset。"""
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "content_mode": "garbage",
        "entries": {},
    }
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(payload))
    assert load_manifest(tmp_path) is None


# ---------- sync_profile_to_project 端到端 ----------


def _fresh_project(tmp_path: Path, name: str = "proj") -> Path:
    d = tmp_path / name
    d.mkdir(parents=True)
    return d


def test_sync_narration_project_writes_narration_variant(tmp_path: Path) -> None:
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="narration")

    assert (project / "CLAUDE.md").read_text() == "narration top"
    assert (project / ".claude" / "skills" / "manga-workflow" / "SKILL.md").read_text() == "nar skill"
    assert not (project / "CLAUDE.narration.md").exists()
    assert not (project / "CLAUDE.drama.md").exists()


def test_sync_drama_project_writes_drama_variant(tmp_path: Path) -> None:
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="drama")
    assert (project / "CLAUDE.md").read_text() == "drama top"


def test_sync_writes_manifest_content_mode(tmp_path: Path) -> None:
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="narration")
    manifest_data = json.loads((project / MANIFEST_FILENAME).read_text())
    assert manifest_data["content_mode"] == "narration"


def test_sync_mode_mismatch_triggers_reset(tmp_path: Path) -> None:
    """已有 manifest 标记 narration，下次 sync 传 drama → reset 路径覆盖 dest。"""
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="narration")
    assert (project / "CLAUDE.md").read_text() == "narration top"

    sync_profile_to_project(profile, project, content_mode="drama")
    assert (project / "CLAUDE.md").read_text() == "drama top"


def test_sync_legacy_manifest_migrates_without_reset(tmp_path: Path) -> None:
    """老 manifest（无 content_mode）+ 未改的 CLAUDE.md → 决策 #4 升级 + 写入 mode。"""
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    # 1) 先按 narration 物化一份（生成 manifest）
    sync_profile_to_project(profile, project, content_mode="narration")
    # 2) 手工把 manifest 改成"老 manifest"形态（删 content_mode 字段）
    manifest_path = project / MANIFEST_FILENAME
    data = json.loads(manifest_path.read_text())
    data.pop("content_mode", None)
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True))
    # 3) 再次 sync，应当被认作 needs_migration，正常走 #3 unchanged，写回 mode
    sync_profile_to_project(profile, project, content_mode="narration")
    after = json.loads(manifest_path.read_text())
    assert after["content_mode"] == "narration"
    # 内容不变
    assert (project / "CLAUDE.md").read_text() == "narration top"


def test_sync_invalid_mode_raises(tmp_path: Path) -> None:
    from lib.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    with pytest.raises(ValueError, match="content_mode"):
        sync_profile_to_project(profile, project, content_mode="reference_video")  # type: ignore[arg-type]


# ---------- force_resync_profile ----------


def test_force_resync_picks_correct_variant(tmp_path: Path) -> None:
    """传逻辑路径 'CLAUDE.md'，按 mode 选对应变体源文件。"""
    from lib.profile_manifest import force_resync_profile, sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    sync_profile_to_project(profile, project, content_mode="narration")

    # 用户手动改 CLAUDE.md
    (project / "CLAUDE.md").write_text("user-edited")

    # force_resync 应当用 narration 变体覆盖
    force_resync_profile(profile, project, content_mode="narration", paths=["CLAUDE.md"])
    assert (project / "CLAUDE.md").read_text() == "narration top"


def test_force_resync_full_uses_mapping(tmp_path: Path) -> None:
    """paths=None 全量恢复时也走变体投影。"""
    from lib.profile_manifest import force_resync_profile

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    force_resync_profile(profile, project, content_mode="drama")
    assert (project / "CLAUDE.md").read_text() == "drama top"


def test_force_resync_invalid_mode_raises(tmp_path: Path) -> None:
    from lib.profile_manifest import force_resync_profile

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    with pytest.raises(ValueError, match="content_mode"):
        force_resync_profile(profile, project, content_mode="bad")  # type: ignore[arg-type]


# ---------- ProjectManager 集成 ----------


def _setup_pm_with_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    """构造 ProjectManager + 指向 _make_profile 生成的 profile 目录。"""
    from lib import project_manager as pm_module

    profile = _make_profile(tmp_path)
    monkeypatch.setattr(pm_module, "agent_profile_dir", lambda: profile)
    pm = pm_module.ProjectManager(projects_root=str(tmp_path / "projects"))
    return pm, profile


def test_create_project_with_drama_mode_materializes_drama_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="drama")
    assert (project_dir / "CLAUDE.md").read_text() == "drama top"
    assert (project_dir / ".claude" / "skills" / "manga-workflow" / "SKILL.md").read_text() == "dra skill"


def test_create_project_default_is_narration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """老 caller 不传 content_mode → 默认 narration（与产品默认一致）。"""
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo")
    assert (project_dir / "CLAUDE.md").read_text() == "narration top"


def test_sync_agent_profile_reads_content_mode_from_project_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="narration")
    # 改 project.json 模拟"老项目缺 mode 字段"
    pj_path = project_dir / "project.json"
    pj_path.write_text(json.dumps({"title": "demo", "content_mode": "drama"}))
    # 再次 sync，应当读 project.json 拿到 drama，触发 mode mismatch reset
    pm.sync_agent_profile(project_dir)
    assert (project_dir / "CLAUDE.md").read_text() == "drama top"


def test_sync_agent_profile_missing_mode_fallback_narration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="narration")
    # 模拟老项目：project.json 没有 content_mode 字段
    pj_path = project_dir / "project.json"
    pj_path.write_text(json.dumps({"title": "demo"}))
    pm.sync_agent_profile(project_dir)
    # 回退 narration，内容不变
    assert (project_dir / "CLAUDE.md").read_text() == "narration top"


def test_sync_agent_profile_invalid_mode_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="narration")
    pj_path = project_dir / "project.json"
    pj_path.write_text(json.dumps({"title": "demo", "content_mode": "garbage"}))
    with pytest.raises(ValueError, match="content_mode"):
        pm.sync_agent_profile(project_dir)


def test_sync_agent_profile_corrupt_json_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """project.json 损坏 → raise，避免 drama 项目被静默回退到 narration 触发
    destructive reset 错切回说书变体。"""
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="drama")
    (project_dir / "project.json").write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        pm.sync_agent_profile(project_dir)


def test_sync_all_agent_profiles_isolates_corrupt_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """一个项目的 project.json 损坏 → failed_projects++，其它项目正常 sync，
    损坏项目的 manifest 不被错误切回 narration（不触发破坏性 reset）。"""
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    pm.create_project("good", content_mode="narration")
    bad_dir = pm.create_project("bad", content_mode="drama")
    (bad_dir / "project.json").write_text("{not json")

    stats = pm.sync_all_agent_profiles()
    assert stats.get("aborted") is not True
    assert stats["failed_projects"] == 1
    assert (pm.projects_root / "good" / "CLAUDE.md").read_text() == "narration top"
    # 损坏项目的 CLAUDE.md 保持上次 sync 的 drama 内容，未被错切回 narration
    assert (bad_dir / "CLAUDE.md").read_text() == "drama top"


def test_sync_all_agent_profiles_per_project_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    pm.create_project("a", content_mode="narration")
    pm.create_project("b", content_mode="drama")
    # 改两个项目的内容（模拟 server 启动前 profile 已升级）
    stats = pm.sync_all_agent_profiles()
    assert stats.get("aborted") is not True
    assert (pm.projects_root / "a" / "CLAUDE.md").read_text() == "narration top"
    assert (pm.projects_root / "b" / "CLAUDE.md").read_text() == "drama top"
