"""Tests for ``lib.agent.profile_manifest`` module utilities.

Cover only manifest utilities (sha256, load/save, enumeration, deterministic serialization,
schema_version compatibility). The 15-row decision matrix is tested end to end in
``tests/test_project_manager_symlink.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.agent.profile_manifest import (
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
)
from lib.project import project_trash

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
    """Symmetric with enumerate_dest_files: sources include only CLAUDE-family files, preventing new
    top-level files from misclassifying destinations as d_exists=False tombstones."""
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "CLAUDE.md").write_text("top")
    (profile / "README.md").write_text("noise")
    (profile / "CHANGELOG.md").write_text("noise")
    (profile / "foo.narration.md").write_text("noise variant")  # Not a CLAUDE-family variant.

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
    """Cross-platform Docker-volume manifest keys must not use backslashes."""
    profile = tmp_path / "profile"
    (profile / ".claude" / "skills" / "a" / "b").mkdir(parents=True)
    (profile / ".claude" / "skills" / "a" / "b" / "c.md").write_text("x")

    files = enumerate_profile_files(profile)
    assert ".claude/skills/a/b/c.md" in files
    # No key may contain backslashes, even when Windows Path uses \.
    for rel in files:
        assert "\\" not in rel


# ---------- Manifest dataclass ----------


def test_manifest_normalized_bytes_deterministic_sort_keys(tmp_path: Path) -> None:
    """Repeated serialization of one manifest yields identical bytes."""
    m1 = Manifest.empty()
    m1.entries["b.md"] = {"sha256": "bb", "size": 2, "source": "profile"}
    m1.entries["a.md"] = {"sha256": "aa", "size": 1, "source": "profile"}

    m2 = Manifest.empty()
    m2.entries["a.md"] = {"sha256": "aa", "size": 1, "source": "profile"}
    m2.entries["b.md"] = {"sha256": "bb", "size": 2, "source": "profile"}

    assert m1.normalized_bytes() == m2.normalized_bytes()
    # Serialize entries in lexicographic key order too.
    text = m1.normalized_bytes().decode("utf-8")
    assert text.index('"a.md"') < text.index('"b.md"')


def test_manifest_no_top_level_synced_at_field() -> None:
    """Schema hygiene: no top-level synced_at, avoiding startup rewrites and noisy git diffs."""
    m = Manifest.empty()
    data = json.loads(m.normalized_bytes())
    assert "synced_at" not in data


def test_manifest_entries_no_per_entry_synced_at_field() -> None:
    """No entry-level synced_at either; tombstone deleted_at is stable write-once data and is allowed."""
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
    """Future schema incompatibility follows the hard-upgrade path: version mismatch → reset."""
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
    """Changing profile changes source, equivalent to reset."""
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
    """load returns (manifest, raw_bytes); raw bytes support pre-write comparison."""
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
    # Leave no .tmp files.
    assert not (project / (MANIFEST_FILENAME + ".tmp")).exists()


def test_save_manifest_skips_write_when_unchanged(tmp_path: Path) -> None:
    """Skip atomic writes when disk bytes equal newly normalized bytes."""
    project = tmp_path / "proj"
    project.mkdir()
    m = Manifest.empty()
    save_manifest(project, m)
    raw = (project / MANIFEST_FILENAME).read_bytes()

    # Identical manifest plus original_bytes must return False.
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
    """First migration: new manifest without original_bytes must persist."""
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
    """Nondict entries are corrupt and reset, not _apply_decision AttributeError."""
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
    """Unknown ``source`` resets, preventing silently ignored drift into a third state."""
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
    """source=profile without sha256 is corruption and resets."""
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
    """Propagate PermissionError / other OSError, never swallowing them as None and triggering destructive reset.

    Temporary disk I/O failures, locks from other processes, and broken permissions must not become
    missing-manifest full resets of .claude/CLAUDE.md.
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
    """Reject absolute paths, `..`, the manifest itself, and empty strings."""
    with pytest.raises(ValueError, match="profile sync"):
        _normalize_profile_rel_path(evil)


@pytest.mark.parametrize("bad", [None, 42, [], {}])
def test_normalize_rel_path_rejects_non_string(bad) -> None:
    """Reject non-str directly, avoiding downstream TypeError."""
    with pytest.raises(ValueError, match="profile sync"):
        _normalize_profile_rel_path(bad)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (".claude/skills/demo/SKILL.md", ".claude/skills/demo/SKILL.md"),
        # PurePosixPath collapses repeated slashes; normalized paths have no doubles and remain valid.
        ("a//b", "a/b"),
        # PurePosixPath strips `.` segments, matching equivalent paths without `.`.
        ("a/./b", "a/b"),
    ],
)
def test_normalize_rel_path_accepts_and_canonicalizes(raw: str, expected: str) -> None:
    """Valid relative paths return POSIX form; pathlib normalization (collapse ``//``, strip ``.``) suffices.

    Specifically cover CodeRabbit’s second-round ``a//b`` case: PurePosixPath leaves no empty parts,
    so ``_normalize_profile_rel_path``’s ``..`` check does not trigger from empties. Acceptance proves
    the old ``part == ""`` check unreachable.
    """
    assert _normalize_profile_rel_path(raw) == expected


# ---------- ProfileMisconfiguredError ----------


def test_profile_misconfigured_error_is_runtime_error() -> None:
    """Same deployment-error level as ProfileMissingError / ProfileEmptyError."""
    from lib.agent.profile_manifest import ProfileMisconfiguredError

    assert issubclass(ProfileMisconfiguredError, RuntimeError)


def test_valid_content_modes_constant() -> None:
    from lib.agent.profile_manifest import VALID_CONTENT_MODES

    assert frozenset({"narration", "drama", "ad"}) == VALID_CONTENT_MODES


# ---------- resolve_profile_files_for_mode ----------


def _make_profile(tmp_path: Path) -> Path:
    """Build a typical profile: common files plus paired narration/drama/ad variants."""
    profile = tmp_path / "profile"
    (profile / ".claude" / "skills" / "video-workflow").mkdir(parents=True)
    (profile / ".claude" / "agents").mkdir(parents=True)
    # Common files.
    (profile / ".claude" / "agents" / "generate-assets.md").write_text("common")
    # CLAUDE.md variant pairs.
    (profile / "CLAUDE.narration.md").write_text("narration top")
    (profile / "CLAUDE.drama.md").write_text("drama top")
    (profile / "CLAUDE.ad.md").write_text("ad top")
    # SKILL.md variant pairs.
    (profile / ".claude" / "skills" / "video-workflow" / "SKILL.narration.md").write_text("nar skill")
    (profile / ".claude" / "skills" / "video-workflow" / "SKILL.drama.md").write_text("dra skill")
    (profile / ".claude" / "skills" / "video-workflow" / "SKILL.ad.md").write_text("ad skill")
    return profile


def test_resolve_for_narration_picks_narration_variants(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    mapping = resolve_profile_files_for_mode(profile, "narration")

    assert mapping == {
        "CLAUDE.md": "CLAUDE.narration.md",
        ".claude/agents/generate-assets.md": ".claude/agents/generate-assets.md",
        ".claude/skills/video-workflow/SKILL.md": ".claude/skills/video-workflow/SKILL.narration.md",
    }


def test_resolve_for_drama_picks_drama_variants(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    mapping = resolve_profile_files_for_mode(profile, "drama")

    assert mapping[".claude/skills/video-workflow/SKILL.md"] == ".claude/skills/video-workflow/SKILL.drama.md"
    assert mapping["CLAUDE.md"] == "CLAUDE.drama.md"


def test_resolve_for_ad_picks_ad_variants(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    mapping = resolve_profile_files_for_mode(profile, "ad")

    assert mapping["CLAUDE.md"] == "CLAUDE.ad.md"
    assert mapping[".claude/skills/video-workflow/SKILL.md"] == ".claude/skills/video-workflow/SKILL.ad.md"


def test_repo_profile_resolves_for_every_content_mode() -> None:
    """Shipped variant pairs cover every content_mode, allowing project materialization in every mode."""
    from lib.agent.profile_manifest import VALID_CONTENT_MODES, resolve_profile_files_for_mode

    repo_profile = Path(__file__).resolve().parents[4] / "agent_runtime_profile"
    for mode in sorted(VALID_CONTENT_MODES):
        mapping = resolve_profile_files_for_mode(repo_profile, mode)
        assert mapping["CLAUDE.md"] == f"CLAUDE.{mode}.md"
        assert mapping[".claude/skills/video-workflow/SKILL.md"] == f".claude/skills/video-workflow/SKILL.{mode}.md"


def test_sync_ad_project_writes_ad_variant(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = tmp_path / "proj_root_ad"
    project.mkdir(parents=True)

    sync_profile_to_project(profile, project, content_mode="ad")

    assert (project / "CLAUDE.md").read_text() == "ad top"
    assert (project / ".claude" / "skills" / "video-workflow" / "SKILL.md").read_text() == "ad skill"
    assert not (project / "CLAUDE.ad.md").exists()


def test_resolve_unpaired_variant_raises(tmp_path: Path) -> None:
    """Narration without drama variants raises ProfileMisconfiguredError."""
    from lib.agent.profile_manifest import ProfileMisconfiguredError, resolve_profile_files_for_mode

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "CLAUDE.narration.md").write_text("only narration")

    with pytest.raises(ProfileMisconfiguredError, match="missing variant"):
        resolve_profile_files_for_mode(profile, "narration")


def test_resolve_common_plus_variant_collision_raises(tmp_path: Path) -> None:
    """Common and variant files sharing logical_rel raise ProfileMisconfiguredError."""
    from lib.agent.profile_manifest import ProfileMisconfiguredError, resolve_profile_files_for_mode

    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "CLAUDE.md").write_text("common")
    (profile / "CLAUDE.narration.md").write_text("variant")
    (profile / "CLAUDE.drama.md").write_text("variant")

    with pytest.raises(ProfileMisconfiguredError, match=r"common.*variant"):
        resolve_profile_files_for_mode(profile, "narration")


def test_resolve_invalid_mode_raises(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import resolve_profile_files_for_mode

    profile = _make_profile(tmp_path)
    with pytest.raises(ValueError, match="content_mode"):
        resolve_profile_files_for_mode(profile, "reference_video")


def test_resolve_double_dot_filename_not_treated_as_variant(tmp_path: Path) -> None:
    """`foo.narration.bar.md` is not a variant; only the final stem segment is recognized."""
    from lib.agent.profile_manifest import resolve_profile_files_for_mode

    profile = tmp_path / "profile"
    (profile / ".claude").mkdir(parents=True)
    (profile / ".claude" / "weird.narration.bar.md").write_text("not a variant")

    mapping = resolve_profile_files_for_mode(profile, "narration")
    assert mapping == {".claude/weird.narration.bar.md": ".claude/weird.narration.bar.md"}


# ---------- Manifest.content_mode field ----------


def test_manifest_empty_has_none_content_mode() -> None:
    m = Manifest.empty()
    assert m.content_mode is None


def test_manifest_serialize_omits_none_content_mode() -> None:
    """None content_mode is omitted from JSON, preserving compact backward compatibility."""
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
    """Old manifests without content_mode load with None."""
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
    """Present but invalid content_mode is corruption and resets."""
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "content_mode": "garbage",
        "entries": {},
    }
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(payload))
    assert load_manifest(tmp_path) is None


# ---------- sync_profile_to_project end to end ----------


def _fresh_project(tmp_path: Path, name: str = "proj") -> Path:
    d = tmp_path / name
    d.mkdir(parents=True)
    return d


def test_sync_narration_project_writes_narration_variant(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="narration")

    assert (project / "CLAUDE.md").read_text() == "narration top"
    assert (project / ".claude" / "skills" / "video-workflow" / "SKILL.md").read_text() == "nar skill"
    assert not (project / "CLAUDE.narration.md").exists()
    assert not (project / "CLAUDE.drama.md").exists()


def test_sync_drama_project_writes_drama_variant(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="drama")
    assert (project / "CLAUDE.md").read_text() == "drama top"


def test_sync_writes_manifest_content_mode(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="narration")
    manifest_data = json.loads((project / MANIFEST_FILENAME).read_text())
    assert manifest_data["content_mode"] == "narration"


def test_sync_mode_mismatch_triggers_reset(tmp_path: Path) -> None:
    """Existing narration manifest synced as drama follows reset, overwriting destinations."""
    from lib.agent.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")

    sync_profile_to_project(profile, project, content_mode="narration")
    assert (project / "CLAUDE.md").read_text() == "narration top"

    sync_profile_to_project(profile, project, content_mode="drama")
    assert (project / "CLAUDE.md").read_text() == "drama top"


def test_sync_legacy_manifest_migrates_without_reset(tmp_path: Path) -> None:
    """Old manifest without content_mode + untouched CLAUDE.md → decision #4 upgrade and mode write."""
    from lib.agent.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    # 1) Materialize narration and generate a manifest.
    sync_profile_to_project(profile, project, content_mode="narration")
    # 2) Manually remove content_mode to simulate an old manifest.
    manifest_path = project / MANIFEST_FILENAME
    data = json.loads(manifest_path.read_text())
    data.pop("content_mode", None)
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True))
    # 3) Sync again: needs_migration follows #3 unchanged and writes mode.
    sync_profile_to_project(profile, project, content_mode="narration")
    after = json.loads(manifest_path.read_text())
    assert after["content_mode"] == "narration"
    # Contents stay unchanged.
    assert (project / "CLAUDE.md").read_text() == "narration top"


def test_sync_invalid_mode_raises(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    with pytest.raises(ValueError, match="content_mode"):
        sync_profile_to_project(profile, project, content_mode="reference_video")


# ---------- force_resync_profile ----------


def test_force_resync_picks_correct_variant(tmp_path: Path) -> None:
    """Logical CLAUDE.md resolves to the mode-selected source variant."""
    from lib.agent.profile_manifest import force_resync_profile, sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    sync_profile_to_project(profile, project, content_mode="narration")

    # User edits CLAUDE.md manually.
    (project / "CLAUDE.md").write_text("user-edited")

    # force_resync must overwrite using the narration variant.
    force_resync_profile(profile, project, content_mode="narration", paths=["CLAUDE.md"])
    assert (project / "CLAUDE.md").read_text() == "narration top"


def test_force_resync_full_uses_mapping(tmp_path: Path) -> None:
    """Full restoration with paths=None still applies variant projection."""
    from lib.agent.profile_manifest import force_resync_profile

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    force_resync_profile(profile, project, content_mode="drama")
    assert (project / "CLAUDE.md").read_text() == "drama top"


@pytest.mark.parametrize("mode", ["narration", "drama", "ad"])
def test_variants_to_common_upgrade_preserves_modified_legacy_files_and_reports_them(tmp_path: Path, mode: str) -> None:
    from lib.agent.profile_manifest import get_profile_status, sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    sync_profile_to_project(profile, project, content_mode=mode)
    old_agent = project / ".claude" / "agents" / "generate-assets.md"
    old_agent.write_text("user customized legacy agent", encoding="utf-8")

    for path in profile.glob("CLAUDE.*.md"):
        path.unlink()
    workflow = profile / ".claude" / "skills" / "video-workflow"
    for path in workflow.glob("SKILL.*.md"):
        path.unlink()
    (profile / "CLAUDE.md").write_text("common top", encoding="utf-8")
    (workflow / "SKILL.md").write_text("common skill", encoding="utf-8")
    (profile / ".claude" / "agents" / "generate-assets.md").unlink()
    references = profile / ".claude" / "references"
    references.mkdir()
    for variant in ("narration", "drama", "ad"):
        (references / f"workflow-mode.{variant}.md").write_text(variant, encoding="utf-8")

    sync_profile_to_project(profile, project, content_mode=mode)

    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == "common top"
    assert (project / ".claude" / "skills" / "video-workflow" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "common skill"
    assert (project / ".claude" / "references" / "workflow-mode.md").read_text(encoding="utf-8") == mode
    assert old_agent.read_text(encoding="utf-8") == "user customized legacy agent"
    assert get_profile_status(profile, project, content_mode=mode) == {
        "customized": True,
        "customized_files": [".claude/agents/generate-assets.md"],
    }


def test_full_force_reset_discards_all_customized_files_and_restores_builtin(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import force_resync_profile, get_profile_status, sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    sync_profile_to_project(profile, project, content_mode="narration")
    (project / "CLAUDE.md").write_text("custom", encoding="utf-8")
    user_only = project / ".claude" / "skills" / "private" / "SKILL.md"
    user_only.parent.mkdir(parents=True)
    user_only.write_text("private", encoding="utf-8")
    assert get_profile_status(profile, project, content_mode="narration") == {
        "customized": True,
        "customized_files": [".claude/skills/private/SKILL.md", "CLAUDE.md"],
    }

    force_resync_profile(profile, project, content_mode="narration")

    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == "narration top"
    assert not user_only.exists()
    assert get_profile_status(profile, project, content_mode="narration") == {
        "customized": False,
        "customized_files": [],
    }
    # Discarded custom/project-only files become one deletion record; built-in-identical files stay out of trash.
    [record] = project_trash.list_records(project)
    assert record.files == (".claude/skills/private/SKILL.md", "CLAUDE.md")
    project_trash.restore(project, record.id)
    assert user_only.read_text(encoding="utf-8") == "private"
    assert (project / "CLAUDE (1).md").read_text(encoding="utf-8") == "custom"


def test_profile_reset_preserves_project_private_area(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import force_resync_profile

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    memory = project / ".arcreel" / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("keep", encoding="utf-8")

    assert ".arcreel/memory/MEMORY.md" not in enumerate_dest_files(project)

    force_resync_profile(profile, project, content_mode="narration")

    assert memory.read_text(encoding="utf-8") == "keep"


def test_profile_status_reports_user_deletion_before_next_sync(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import get_profile_status, sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    sync_profile_to_project(profile, project, content_mode="narration")
    (project / "CLAUDE.md").unlink()

    assert get_profile_status(profile, project, content_mode="narration") == {
        "customized": True,
        "customized_files": ["CLAUDE.md"],
    }


def test_profile_status_reports_newly_projected_file_missing_from_manifest(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import get_profile_status, sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    sync_profile_to_project(profile, project, content_mode="narration")
    (profile / ".claude" / "agents" / "new.md").write_text("new built-in", encoding="utf-8")

    assert get_profile_status(profile, project, content_mode="narration") == {
        "customized": True,
        "customized_files": [".claude/agents/new.md"],
    }


@pytest.mark.parametrize("manifest_payload", [None, "{invalid"])
def test_profile_status_reports_missing_builtins_without_trusted_manifest(
    tmp_path: Path, manifest_payload: str | None
) -> None:
    from lib.agent.profile_manifest import get_profile_status

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    if manifest_payload is not None:
        (project / MANIFEST_FILENAME).write_text(manifest_payload, encoding="utf-8")

    assert get_profile_status(profile, project, content_mode="narration") == {
        "customized": True,
        "customized_files": [
            ".claude/agents/generate-assets.md",
            ".claude/skills/video-workflow/SKILL.md",
            "CLAUDE.md",
        ],
    }


def test_profile_status_reports_nested_directory_symlink(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import get_profile_status, sync_profile_to_project

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    sync_profile_to_project(profile, project, content_mode="narration")
    outside = tmp_path / "outside"
    outside.mkdir()
    custom = project / ".claude" / "skills" / "custom"
    custom.symlink_to(outside, target_is_directory=True)

    assert get_profile_status(profile, project, content_mode="narration") == {
        "customized": True,
        "customized_files": [".claude/skills/custom"],
    }


def test_profile_status_rejects_symlinked_profile_tree(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import get_profile_status

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    (project / ".claude").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="profile tree is a symlink"):
        get_profile_status(profile, project, content_mode="narration")


def test_force_resync_invalid_mode_raises(tmp_path: Path) -> None:
    from lib.agent.profile_manifest import force_resync_profile

    profile = _make_profile(tmp_path)
    project = _fresh_project(tmp_path / "proj_root")
    with pytest.raises(ValueError, match="content_mode"):
        force_resync_profile(profile, project, content_mode="bad")


# ---------- ProjectManager integration ----------


def _setup_pm_with_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    """Build ProjectManager pointing at _make_profile’s generated profile directory."""
    from lib.project import project_manager as pm_module

    profile = _make_profile(tmp_path)
    monkeypatch.setattr(pm_module, "agent_profile_dir", lambda: profile)
    pm = pm_module.ProjectManager(data_root=str(tmp_path / "projects"))
    return pm, profile


def test_create_project_with_drama_mode_materializes_drama_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="drama")
    assert (project_dir / "CLAUDE.md").read_text() == "drama top"
    assert (project_dir / ".claude" / "skills" / "video-workflow" / "SKILL.md").read_text() == "dra skill"


def test_create_project_default_is_narration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Old callers omitting content_mode default to narration, matching product defaults."""
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo")
    assert (project_dir / "CLAUDE.md").read_text() == "narration top"


def test_sync_agent_profile_reads_content_mode_from_project_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="narration")
    # Edit project.json to simulate an old project without mode.
    pj_path = project_dir / "project.json"
    pj_path.write_text(json.dumps({"title": "demo", "content_mode": "drama"}))
    # Resync reads drama from project.json and triggers mode-mismatch reset.
    pm.sync_agent_profile(project_dir)
    assert (project_dir / "CLAUDE.md").read_text() == "drama top"


def test_sync_agent_profile_missing_mode_fallback_narration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="narration")
    # Simulate an old project: no content_mode in project.json.
    pj_path = project_dir / "project.json"
    pj_path.write_text(json.dumps({"title": "demo"}))
    pm.sync_agent_profile(project_dir)
    # Fall back to narration with unchanged contents.
    assert (project_dir / "CLAUDE.md").read_text() == "narration top"


def test_sync_agent_profile_invalid_mode_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="narration")
    pj_path = project_dir / "project.json"
    pj_path.write_text(json.dumps({"title": "demo", "content_mode": "garbage"}))
    with pytest.raises(ValueError, match="content_mode"):
        pm.sync_agent_profile(project_dir)


def test_sync_agent_profile_corrupt_json_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Corrupt project.json raises, preventing silent drama→narration fallback from destructively resetting
    to the wrong narration variant."""
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    project_dir = pm.create_project("demo", content_mode="drama")
    (project_dir / "project.json").write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        pm.sync_agent_profile(project_dir)


def test_sync_all_agent_profiles_isolates_corrupt_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One corrupt project.json increments failed_projects while others sync; preserve its manifest’s
    mode instead of destructively resetting to narration."""
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    pm.create_project("good", content_mode="narration")
    bad_dir = pm.create_project("bad", content_mode="drama")
    (bad_dir / "project.json").write_text("{not json")

    stats = pm.sync_all_agent_profiles()
    assert stats.get("aborted") is not True
    assert stats["failed_projects"] == 1
    assert (pm.projects_dir / "good" / "CLAUDE.md").read_text() == "narration top"
    # Corrupt-project CLAUDE.md keeps previous drama content without an erroneous narration switch.
    assert (bad_dir / "CLAUDE.md").read_text() == "drama top"


def test_sync_all_agent_profiles_per_project_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm, _ = _setup_pm_with_profile(tmp_path, monkeypatch)
    pm.create_project("a", content_mode="narration")
    pm.create_project("b", content_mode="drama")
    # Edit both projects to simulate an upgraded profile before server startup.
    stats = pm.sync_all_agent_profiles()
    assert stats.get("aborted") is not True
    assert (pm.projects_dir / "a" / "CLAUDE.md").read_text() == "narration top"
    assert (pm.projects_dir / "b" / "CLAUDE.md").read_text() == "drama top"
