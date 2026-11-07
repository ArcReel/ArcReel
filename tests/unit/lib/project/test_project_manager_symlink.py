"""Tests for manifest-driven profile sync via ``ProjectManager.sync_agent_profile``.

Retain historical filename ``test_project_manager_symlink.py`` for external test selectors.
Materialization uses manifest + sha256:
- Profile upgrades automatically propagate built-in skills to existing projects (row #4).
- User-deleted built-in skills do not reappear (rows #2/#11).
- Cover all 15 decision-table rows, including name collisions, state-machine convergence, and upstream deletion.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lib.agent.profile_manifest import (
    EXPECTED_PROFILE_ID,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    ProfileEmptyError,
    ProfileMissingError,
)
from lib.project.project_manager import ProjectManager

# Shared fixtures.


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a standard environment: profile_dir, data root, and one project directory.

    The profile contains one demo skill and top-level CLAUDE.md.
    """
    profile_dir = tmp_path / "profile"
    (profile_dir / ".claude" / "skills" / "demo").mkdir(parents=True)
    (profile_dir / ".claude" / "skills" / "demo" / "SKILL.md").write_text("demo v1")
    (profile_dir / "CLAUDE.md").write_text("prompt v1")

    monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_dir))

    pm = ProjectManager(tmp_path / "data")
    project_dir = pm.projects_dir / "proj"
    _make_project(project_dir)
    return pm, profile_dir, project_dir


def _make_project(project_dir: Path) -> None:
    project_dir.mkdir()
    (project_dir / "project.json").write_text(json.dumps({"content_mode": "narration"}))


def _read_manifest(project_dir: Path) -> dict:
    return json.loads((project_dir / MANIFEST_FILENAME).read_text())


def _skill_path(project_dir: Path, name: str = "demo") -> Path:
    return project_dir / ".claude" / "skills" / name / "SKILL.md"


def _profile_skill_path(profile_dir: Path, name: str = "demo") -> Path:
    return profile_dir / ".claude" / "skills" / name / "SKILL.md"


# Initial-migration branches.


class TestFirstSyncMigration:
    def test_first_sync_full_reset_when_no_manifest(self, env):
        pm, _profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)

        assert _skill_path(project_dir).read_text() == "demo v1"
        assert (project_dir / "CLAUDE.md").read_text() == "prompt v1"
        manifest = _read_manifest(project_dir)
        assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
        assert manifest["profile_id"] == EXPECTED_PROFILE_ID
        assert ".claude/skills/demo/SKILL.md" in manifest["entries"]
        assert "CLAUDE.md" in manifest["entries"]

    def test_first_sync_resets_even_with_existing_user_content(self, env):
        """User choice to ignore existing content overwrites dest directly at first adoption."""
        pm, _, project_dir = env
        _skill_path(project_dir).parent.mkdir(parents=True, exist_ok=True)
        _skill_path(project_dir).write_text("legacy junk")
        (project_dir / "CLAUDE.md").write_text("legacy prompt")

        pm.sync_agent_profile(project_dir)

        assert _skill_path(project_dir).read_text() == "demo v1"
        assert (project_dir / "CLAUDE.md").read_text() == "prompt v1"

    def test_first_sync_replaces_wrong_type_placeholders(self, env):
        """When ``.claude`` is a regular file / ``CLAUDE.md`` is a directory, reset must remove it first;
        otherwise subsequent mkdir()/_safe_copy() fails, leaving a partial state and incomplete manifest."""
        pm, _, project_dir = env
        # Wrong dest_tree type: regular file, a common mistake when copying README instructions manually.
        (project_dir / ".claude").write_text("wrong type — should be a dir")
        # Wrong dest_top type: directory from manual mkdir CLAUDE.md/.
        (project_dir / "CLAUDE.md").mkdir()
        (project_dir / "CLAUDE.md" / "stray.txt").write_text("stray")

        pm.sync_agent_profile(project_dir)

        assert (project_dir / ".claude").is_dir()
        assert _skill_path(project_dir).read_text() == "demo v1"
        assert (project_dir / "CLAUDE.md").is_file()
        assert (project_dir / "CLAUDE.md").read_text() == "prompt v1"

    def test_create_project_invokes_sync(self, env):
        pm, _, _ = env
        new_dir = pm.create_project("brand-new")

        assert (new_dir / ".claude").is_dir()
        assert not (new_dir / ".claude").is_symlink()
        assert (new_dir / MANIFEST_FILENAME).is_file()
        assert (new_dir / "CLAUDE.md").read_text() == "prompt v1"


# Coverage for all 15 decision-table rows.


class TestDecisionTable:
    def test_decision_2_user_delete_not_resurrected(self, env):
        """#2: profile exists + dest missing + manifest active → tombstone, without restoring dest."""
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).unlink()
        os.rmdir(_skill_path(project_dir).parent)

        stats = pm.sync_agent_profile(project_dir)

        assert not _skill_path(project_dir).exists()
        assert stats["deleted_user"] == 1
        entries = _read_manifest(project_dir)["entries"]
        assert entries[".claude/skills/demo/SKILL.md"]["source"] == "tombstone"

    def test_decision_3_no_op_when_three_hashes_match(self, env):
        """#3: all three states agree → no-op, byte-identical manifest through compare-before-write."""
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        raw1 = (project_dir / MANIFEST_FILENAME).read_bytes()
        mtime1 = _skill_path(project_dir).stat().st_mtime_ns

        stats = pm.sync_agent_profile(project_dir)

        raw2 = (project_dir / MANIFEST_FILENAME).read_bytes()
        assert raw1 == raw2
        assert _skill_path(project_dir).stat().st_mtime_ns == mtime1
        assert stats["unchanged"] >= 1

    def test_decision_4_profile_upgrade_propagates_when_user_clean(self, env):
        """#4: unchanged user file + upgraded profile → overwrite and refresh manifest hash, the core benefit of option C."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        _profile_skill_path(profile_dir).write_text("demo v2")

        stats = pm.sync_agent_profile(project_dir)

        assert _skill_path(project_dir).read_text() == "demo v2"
        assert stats["upgraded"] == 1

    def test_decision_5_user_edit_converging_to_profile_version(self, env):
        """#5: user edits now equal current profile → converge by refreshing manifest; next round follows #3."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        _profile_skill_path(profile_dir).write_text("demo v2")
        _skill_path(project_dir).write_text("demo v2")

        stats = pm.sync_agent_profile(project_dir)

        assert stats["unchanged"] >= 1
        assert stats["user_modified"] == 0
        stats2 = pm.sync_agent_profile(project_dir)
        assert stats2["unchanged"] >= 1
        assert stats2["user_modified"] == 0
        assert stats2["upgraded"] == 0

    def test_decision_6_user_edit_preserved_against_profile_upgrade(self, env):
        """#6: user edits + upgraded profile → preserve user version."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).write_text("user customized")
        _profile_skill_path(profile_dir).write_text("demo v2")

        stats = pm.sync_agent_profile(project_dir)

        assert _skill_path(project_dir).read_text() == "user customized"
        assert stats["user_modified"] == 1

    def test_decision_7_profile_deletion_propagates_to_unmodified_dest(self, env):
        """#7: upstream profile deletion + unchanged user file → delete dest and tombstone during materialization."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        _profile_skill_path(profile_dir).unlink()

        stats = pm.sync_agent_profile(project_dir)

        assert not _skill_path(project_dir).exists()
        assert stats["pruned"] == 1
        entries = _read_manifest(project_dir)["entries"]
        assert entries[".claude/skills/demo/SKILL.md"]["source"] == "tombstone"

    def test_decision_8_profile_deletion_orphans_user_modified(self, env):
        """#8: upstream profile deletion + user edits → preserve dest and baseline so settings can mark customization."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).write_text("user owned now")
        _profile_skill_path(profile_dir).unlink()

        stats = pm.sync_agent_profile(project_dir)

        assert _skill_path(project_dir).read_text() == "user owned now"
        assert stats["orphaned"] == 1
        entries = _read_manifest(project_dir)["entries"]
        assert entries[".claude/skills/demo/SKILL.md"]["source"] == "profile"

    def test_decision_9_user_only_file_untouched(self, env):
        """#9: project-only skill (absent from profile/manifest) → completely untouched."""
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        user_skill = project_dir / ".claude" / "skills" / "user_only" / "SKILL.md"
        user_skill.parent.mkdir(parents=True)
        user_skill.write_text("private workflow")

        for _ in range(3):
            pm.sync_agent_profile(project_dir)

        assert user_skill.read_text() == "private workflow"
        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/user_only/SKILL.md" not in entries

    def test_decision_10_user_manually_restores_deleted_file(self, env):
        """#10: user manually rewrites D while tombstoned → clear tombstone; next round follows #9 user_only."""
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).unlink()
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).write_text("user restored version")

        stats = pm.sync_agent_profile(project_dir)

        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/demo/SKILL.md" not in entries
        assert stats["user_only"] >= 1

    def test_decision_11_tombstone_steady_state(self, env):
        """#11: user deletion while profile remains → stable no-op over N repairs."""
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).unlink()
        pm.sync_agent_profile(project_dir)

        for _ in range(5):
            stats = pm.sync_agent_profile(project_dir)
            assert not _skill_path(project_dir).exists()
            assert stats["created"] == 0
            assert stats["upgraded"] == 0
            assert stats["tombstoned"] >= 1

    def test_decision_12_orphaned_dest_with_tombstone_clears_entry(self, env):
        """#12: profile deleted + dest remains + manifest tombstone → clear entry."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).unlink()
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).write_text("user re-added")
        _profile_skill_path(profile_dir).unlink()

        stats = pm.sync_agent_profile(project_dir)

        assert _skill_path(project_dir).exists()
        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/demo/SKILL.md" not in entries
        assert stats["user_only"] >= 1

    def test_decision_13_tombstone_persists_when_both_missing(self, env):
        """#13: profile and dest absent + manifest tombstone → no-op, retaining tombstone."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).unlink()
        pm.sync_agent_profile(project_dir)
        _profile_skill_path(profile_dir).unlink()

        stats = pm.sync_agent_profile(project_dir)

        entries = _read_manifest(project_dir)["entries"]
        assert entries.get(".claude/skills/demo/SKILL.md", {}).get("source") == "tombstone"
        assert stats["tombstoned"] >= 1

    def test_decision_14_double_delete_creates_tombstone(self, env):
        """#14: both delete in the same round (active entry) → tombstone."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        _profile_skill_path(profile_dir).unlink()
        _skill_path(project_dir).unlink()

        stats = pm.sync_agent_profile(project_dir)

        entries = _read_manifest(project_dir)["entries"]
        assert entries[".claude/skills/demo/SKILL.md"]["source"] == "tombstone"
        assert stats["pruned"] == 1

    def test_decision_14_tombstone_blocks_future_readd_unless_force_resync(self, env):
        """#14 implicit assumption: readding profile after tombstoning still follows #11 without revival; force_resync is required."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        _profile_skill_path(profile_dir).unlink()
        _skill_path(project_dir).unlink()
        pm.sync_agent_profile(project_dir)
        _profile_skill_path(profile_dir).parent.mkdir(parents=True, exist_ok=True)
        _profile_skill_path(profile_dir).write_text("demo v2 readded")

        stats = pm.sync_agent_profile(project_dir)

        assert not _skill_path(project_dir).exists()
        assert stats["tombstoned"] >= 1
        pm.force_resync_profile(project_dir, paths=[".claude/skills/demo/SKILL.md"])
        assert _skill_path(project_dir).read_text() == "demo v2 readded"

    def test_decision_15_collision_preserves_user_content(self, env):
        """#15: user independently creates a same-named file (D≠P) → retain D without an entry."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)  # Build the baseline.
        # Manually add user_x and profile_x with the same name but different content.
        user_x = project_dir / ".claude" / "skills" / "X" / "SKILL.md"
        user_x.parent.mkdir(parents=True)
        user_x.write_text("user version A")
        profile_x = profile_dir / ".claude" / "skills" / "X" / "SKILL.md"
        profile_x.parent.mkdir(parents=True)
        profile_x.write_text("profile version B")

        stats = pm.sync_agent_profile(project_dir)

        assert user_x.read_text() == "user version A"
        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/X/SKILL.md" not in entries
        assert stats["collision"] == 1

    def test_decision_15_collision_when_hashes_match_writes_active_entry(self, env):
        """#15: D=P counts as distributed; write active entry and follow #3 unchanged next round."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        user_x = project_dir / ".claude" / "skills" / "X" / "SKILL.md"
        user_x.parent.mkdir(parents=True)
        user_x.write_text("same content")
        profile_x = profile_dir / ".claude" / "skills" / "X" / "SKILL.md"
        profile_x.parent.mkdir(parents=True)
        profile_x.write_text("same content")

        stats = pm.sync_agent_profile(project_dir)

        entries = _read_manifest(project_dir)["entries"]
        assert ".claude/skills/X/SKILL.md" in entries
        assert entries[".claude/skills/X/SKILL.md"]["source"] == "profile"
        assert stats["collision"] == 1
        stats2 = pm.sync_agent_profile(project_dir)
        assert stats2["collision"] == 0
        assert stats2["unchanged"] >= 1


# ---------- force_resync ----------


class TestForceResync:
    def test_force_resync_overrides_user_edit(self, env):
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).write_text("user customized")

        stats = pm.force_resync_profile(project_dir, paths=[".claude/skills/demo/SKILL.md"])

        assert _skill_path(project_dir).read_text() == "demo v1"
        assert stats["created"] == 1

    def test_force_resync_skips_missing_profile_file(self, env, caplog):
        """paths containing profile-deleted files skip with warning, not error."""
        pm, profile_dir, project_dir = env
        pm.sync_agent_profile(project_dir)
        _profile_skill_path(profile_dir).unlink()

        with caplog.at_level("WARNING"):
            stats = pm.force_resync_profile(project_dir, paths=[".claude/skills/demo/SKILL.md"])

        assert stats["errors"] == 0
        assert stats["created"] == 0
        assert any("force_resync skip" in r.message for r in caplog.records)

    @pytest.mark.parametrize(
        "evil",
        [
            "../escape.txt",
            ".claude/../../etc/passwd",
            "/etc/passwd",
            ".arcreel_profile_manifest.json",
            ".profile_sync.lock",
            "",
        ],
    )
    def test_force_resync_rejects_path_traversal(self, env, evil):
        """``paths`` is external input: reject absolute paths / `..` / the manifest itself,
        preventing profile/project-root escape and arbitrary file reads/writes.
        """
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        with pytest.raises(ValueError, match="profile sync"):
            pm.force_resync_profile(project_dir, paths=[evil])

    def test_force_resync_with_paths_does_not_full_reset_when_manifest_missing(self, env):
        """``paths=["X"]`` without a manifest restores only X; do not call _full_reset to clear
        all .claude and overwrite other built-ins.

        Scenario: an old project has no manifest, and the user chooses restore demo skill in the UI. Reset
        only demo, preserving other edited skills and deletions.
        """
        pm, _, project_dir = env
        # Sync once for a full destination baseline and manifest.
        pm.sync_agent_profile(project_dir)
        # Simulate editing another skill.
        other_skill = project_dir / ".claude" / "skills" / "other" / "SKILL.md"
        other_skill.parent.mkdir(parents=True, exist_ok=True)
        other_skill.write_text("user-customized other skill")
        # Delete the manifest to simulate a legacy project without one.
        (project_dir / ".arcreel_profile_manifest.json").unlink()

        stats = pm.force_resync_profile(project_dir, paths=[".claude/skills/demo/SKILL.md"])

        # Restore demo.
        assert _skill_path(project_dir).read_text() == "demo v1"
        # Preserve the other skill.
        assert other_skill.read_text() == "user-customized other skill"
        # The new manifest contains only the demo entry, not every file.
        from lib.agent.profile_manifest import load_manifest

        loaded = load_manifest(project_dir)
        assert loaded is not None
        manifest, _ = loaded
        assert set(manifest.entries.keys()) == {".claude/skills/demo/SKILL.md"}
        assert stats["created"] == 1

    def test_sync_skips_dest_symlink_escape(self, env):
        """Skip escaping symlinks within ``.claude`` without reading/writing outside the project.

        Threat model: user/imported archives place ``.claude/evil_link`` pointing to an external sensitive
        file. Destination enumeration includes the entry (rglob dereferences the symlink to a file);
        decision-path _safe_copy / _safe_unlink_if_file could read/write outside the project.
        Before I/O, the escape guard must resolve and verify containment under project_dir.
        """
        import os

        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        # Add a .claude symlink pointing outside the project.
        outside = project_dir.parent.parent / "outside_secret.md"
        outside.write_text("MUST NOT be touched")
        escape_link = project_dir / ".claude" / "evil_link.md"
        escape_link.symlink_to(outside)

        # Deleting the manifest triggers initial migration, but _full_reset does not use the escape-guard
        # path: full copying does not encounter existing destinations. Exercise main materialization instead.
        # Insert a manifest entry so rel enters all_keys and follows #6 user_modified;
        # _apply_decision then calls sha256_file(d), which would follow the symlink outside.
        # The escape guard blocks I/O for this rel.
        manifest_path = project_dir / MANIFEST_FILENAME
        data = json.loads(manifest_path.read_text())
        data["entries"][".claude/evil_link.md"] = {
            "sha256": "0" * 64,
            "size": 100,
            "source": "profile",
        }
        manifest_path.write_text(json.dumps(data))

        stats = pm.sync_agent_profile(project_dir)

        # The outside file remains unchanged.
        assert outside.read_text() == "MUST NOT be touched"
        # Count this rel at least as an error, because the escape guard blocks it.
        assert stats["errors"] >= 1
        # The symlink itself is unchanged; rmtree occurs only in _full_reset, not this main path.
        assert os.path.islink(escape_link)

    def test_sync_refuses_symlink_lock_file(self, env):
        """A preexisting ``.profile_sync.lock`` symlink outside the project makes sync refuse to run,
        preventing external-file truncation through symlink following.

        Threat model: imported malicious ``.profile_sync.lock`` symlink → ``/etc/x``.
        Old portalocker ``open("w")`` followed symlinks and truncated targets; the new implementation
        opens its own fd with ``os.open(O_NOFOLLOW)``, raising ELOOP on symlinks.
        """
        pm, _, project_dir = env
        outside = project_dir.parent.parent / "outside_lock_target.txt"
        outside.write_text("MUST NOT be truncated")
        lock_link = project_dir / ".profile_sync.lock"
        lock_link.symlink_to(outside)

        with pytest.raises(ValueError, match="lock path is a symlink"):
            pm.sync_agent_profile(project_dir)

        assert outside.read_text() == "MUST NOT be truncated"

    def test_sync_works_without_o_nofollow(self, env, monkeypatch: pytest.MonkeyPatch):
        """Windows ``os`` lacks ``O_NOFOLLOW``. Locking must fall back to a Python-level
        ``is_symlink`` precheck; otherwise ``_project_lock`` immediately crashes with
        ``AttributeError: module 'os' has no attribute 'O_NOFOLLOW'``,
        preventing Windows users from creating projects.
        """
        pm, _, project_dir = env
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

        stats = pm.sync_agent_profile(project_dir)

        assert stats["created"] >= 1
        assert (project_dir / "CLAUDE.md").exists()

    def test_sync_refuses_symlink_lock_without_o_nofollow(self, env, monkeypatch: pytest.MonkeyPatch):
        """The Windows fallback must still reject symlink lock files; absence of
        ``O_NOFOLLOW`` must not disable symlink protection.
        """
        pm, _, project_dir = env
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
        outside = project_dir.parent.parent / "outside_lock_target_win.txt"
        outside.write_text("MUST NOT be truncated")
        lock_link = project_dir / ".profile_sync.lock"
        lock_link.symlink_to(outside)

        with pytest.raises(ValueError, match="lock path is a symlink"):
            pm.sync_agent_profile(project_dir)

        assert outside.read_text() == "MUST NOT be truncated"

    def test_save_manifest_tmp_uses_unpredictable_name(self, tmp_path: Path):
        """``save_manifest`` must not use predictable ``.arcreel_profile_manifest.json.tmp``
        names: an attacker could precreate that symlink → ``/etc/x``, causing tmp.write
        to follow it and truncate external files. Use ``tempfile.mkstemp`` with O_EXCL,
        unpredictable names, and the same directory.
        """
        from lib.agent.profile_manifest import (
            EXPECTED_PROFILE_ID,
            MANIFEST_FILENAME,
            MANIFEST_SCHEMA_VERSION,
            Manifest,
            save_manifest,
        )

        outside = tmp_path / "outside_tmp_target.txt"
        outside.write_text("MUST NOT be truncated")
        project = tmp_path / "proj"
        project.mkdir()
        evil_tmp = project / (MANIFEST_FILENAME + ".tmp")
        evil_tmp.symlink_to(outside)

        manifest = Manifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            profile_id=EXPECTED_PROFILE_ID,
            entries={},
        )
        save_manifest(project, manifest, original_bytes=None)

        # Do not truncate/overwrite outside content.
        assert outside.read_text() == "MUST NOT be truncated"
        # Write the actual manifest under project_dir.
        assert (project / MANIFEST_FILENAME).read_bytes() == manifest.normalized_bytes()

    def test_load_manifest_refuses_symlink_manifest(self, tmp_path: Path):
        """A preexisting ``.arcreel_profile_manifest.json`` symlink outside the project loads
        as corruption and resets, without reading external data (information leakage and incorrect reset decisions).
        """
        from lib.agent.profile_manifest import MANIFEST_FILENAME, load_manifest

        outside = tmp_path / "outside_manifest.json"
        outside.write_text('{"schema_version": 1, "profile_id": "evil/source", "entries": {}}')
        project = tmp_path / "proj"
        project.mkdir()
        (project / MANIFEST_FILENAME).symlink_to(outside)

        assert load_manifest(project) is None
        # Do not automatically remove the symlink; later _full_reset handles replacement through mkstemp + os.replace.
        assert (project / MANIFEST_FILENAME).is_symlink()

    def test_safe_copy_rejects_dir_dest(self, tmp_path: Path):
        """``_safe_copy`` rejects real-directory destinations; otherwise shutil.copy2 unexpectedly creates
        ``dest/source.name`` instead of failing.
        """
        from lib.agent.profile_manifest import _safe_copy

        src = tmp_path / "src.md"
        src.write_text("payload")
        dest_dir = tmp_path / "evil_dir"
        dest_dir.mkdir()

        with pytest.raises(ValueError, match="dest is a directory"):
            _safe_copy(src, dest_dir)
        # Destination directory and contents remain unchanged.
        assert dest_dir.is_dir()
        assert list(dest_dir.iterdir()) == []

    def test_safe_copy_unlinks_symlink_dest_before_write(self, tmp_path: Path):
        """``_safe_copy`` must unlink symlink destinations before writing to avoid following them
        and writing outside the project.

        Cover file-level TOCTOU: an external process replaces dest with a symlink between guard
        validation and _safe_copy; writing must not follow it.
        """
        from lib.agent.profile_manifest import _safe_copy

        outside = tmp_path / "outside.md"
        outside.write_text("ORIGINAL outside content")
        src = tmp_path / "src.md"
        src.write_text("new content")
        dest = tmp_path / "dest.md"
        dest.symlink_to(outside)

        _safe_copy(src, dest)

        # Outside content must remain unchanged.
        assert outside.read_text() == "ORIGINAL outside content"
        # Destination must be a real file, no longer a symlink, containing new content.
        assert not dest.is_symlink()
        assert dest.read_text() == "new content"

    def test_force_resync_raises_on_empty_profile(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """An existing profile without files raises ProfileEmptyError, matching the main entry.
        Otherwise paths=None without a manifest could invoke _full_reset and empty the project.
        """
        empty_profile = tmp_path / "empty_profile"
        empty_profile.mkdir()
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(empty_profile))
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "p1"
        project_dir.mkdir()

        from lib.agent.profile_manifest import ProfileEmptyError

        with pytest.raises(ProfileEmptyError):
            pm.force_resync_profile(project_dir)


# Entry defenses.


class TestProfileEntryGuards:
    def test_profile_missing_raises_protective_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Missing profiles raise ProfileMissingError, never silently mass-pruning dest."""
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(tmp_path / "nonexistent"))
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "proj"
        project_dir.mkdir()
        (project_dir / ".claude").mkdir()
        (project_dir / ".claude" / "skill.md").write_text("must not be pruned")

        with pytest.raises(ProfileMissingError):
            pm.sync_agent_profile(project_dir)

        assert (project_dir / ".claude" / "skill.md").exists()

    def test_profile_empty_raises_protective_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Existing profile directories without materializable files raise ProfileEmptyError."""
        empty_profile = tmp_path / "profile"
        empty_profile.mkdir()
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(empty_profile))
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        pm = ProjectManager(projects_root)
        project_dir = projects_root / "proj"
        project_dir.mkdir()

        with pytest.raises(ProfileEmptyError):
            pm.sync_agent_profile(project_dir)


# ---------- sync_all_agent_profiles ----------


class TestRepairAllSymlinks:
    def test_repair_all_returns_stats_with_aggregated_keys(self, env):
        pm, _, _ = env

        stats = pm.sync_all_agent_profiles()

        assert "created" in stats
        assert "repaired" in stats
        assert "skipped" in stats
        assert "errors" in stats
        assert "failed_projects" in stats
        assert "aborted" in stats
        assert stats["created"] >= 2

    def test_repair_all_skips_hidden_dirs(self, env):
        pm, _, _ = env
        (pm.projects_dir / ".hidden").mkdir()
        stats = pm.sync_all_agent_profiles()
        assert not (pm.projects_dir / ".hidden" / ".claude").exists()
        assert stats["aborted"] is False

    def test_repair_all_continues_on_single_project_failure(self, env, monkeypatch: pytest.MonkeyPatch):
        """One project's exception leaves other projects running; count failed_projects."""
        pm, _, _ = env
        _make_project(pm.projects_dir / "proj2")

        original = pm.sync_agent_profile

        def patched(project_dir: Path):
            if project_dir.name == "proj":
                raise RuntimeError("simulated failure on proj")
            return original(project_dir)

        monkeypatch.setattr(pm, "sync_agent_profile", patched)

        stats = pm.sync_all_agent_profiles()

        assert stats["failed_projects"] == 1
        assert (pm.projects_dir / "proj2" / ".claude").is_dir()
        assert stats["aborted"] is False

    def test_repair_all_aborts_on_profile_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """ProfileMissingError sets totals.aborted=True and skips all projects."""
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(tmp_path / "nonexistent"))
        pm = ProjectManager(tmp_path / "data")
        _make_project(pm.projects_dir / "proj1")
        _make_project(pm.projects_dir / "proj2")

        stats = pm.sync_all_agent_profiles()

        assert stats["aborted"] is True
        assert not (pm.projects_dir / "proj1" / MANIFEST_FILENAME).exists()
        assert not (pm.projects_dir / "proj2" / MANIFEST_FILENAME).exists()

    def test_skips_entries_that_are_not_projects(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Materialize only into projects: skip directories without project.json, invalid names, and system entries under the data root."""
        profile_dir = tmp_path / "profile"
        (profile_dir / ".claude").mkdir(parents=True)
        (profile_dir / ".claude" / "x.md").write_text("v1")
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_dir))

        pm = ProjectManager(tmp_path / "data")
        _make_project(pm.projects_dir / "proj")
        not_projects = [
            pm.projects_dir / "no-project-json",
            pm.projects_dir / "bad_name",
            pm.layout.global_assets_dir,
            pm.layout.trial_runs_dir,
            pm.layout.users_dir,
            pm.layout.runtime_dir,
        ]
        for directory in not_projects:
            directory.mkdir(parents=True, exist_ok=True)
        (pm.projects_dir / "bad_name" / "project.json").write_text(json.dumps({"content_mode": "narration"}))

        pm.sync_all_agent_profiles()

        assert (pm.projects_dir / "proj" / MANIFEST_FILENAME).exists()
        for directory in not_projects:
            assert not (directory / MANIFEST_FILENAME).exists(), directory
            assert not (directory / ".claude").exists(), directory


# Legacy symlink migration.


class TestLegacySymlinkMigration:
    def test_legacy_symlink_replaced_with_materialized_dir(self, env):
        """First repair removes legacy deployment symlinks and upgrades to materialization."""
        pm, profile_dir, project_dir = env
        (project_dir / ".claude").symlink_to(profile_dir / ".claude")
        (project_dir / "CLAUDE.md").symlink_to(profile_dir / "CLAUDE.md")

        pm.sync_agent_profile(project_dir)

        assert (project_dir / ".claude").is_dir()
        assert not (project_dir / ".claude").is_symlink()
        assert (project_dir / "CLAUDE.md").is_file()
        assert not (project_dir / "CLAUDE.md").is_symlink()
        assert (project_dir / MANIFEST_FILENAME).exists()


# Manifest field invariants.


class TestManifestInvariants:
    def test_manifest_uses_posix_path_keys(self, env):
        """Cross-platform path keys must use POSIX separators."""
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)

        entries = _read_manifest(project_dir)["entries"]
        for key in entries:
            assert "\\" not in key, f"manifest key has backslash: {key!r}"

    def test_manifest_skipped_when_unchanged_across_repair(self, env):
        """Repeated repairs preserve manifest bytes through compare-before-write."""
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        raw1 = (project_dir / MANIFEST_FILENAME).read_bytes()
        mtime1 = (project_dir / MANIFEST_FILENAME).stat().st_mtime_ns

        pm.sync_agent_profile(project_dir)

        raw2 = (project_dir / MANIFEST_FILENAME).read_bytes()
        mtime2 = (project_dir / MANIFEST_FILENAME).stat().st_mtime_ns
        assert raw1 == raw2
        assert mtime1 == mtime2

    def test_manifest_schema_version_mismatch_triggers_full_reset(self, env):
        """Old-schema manifests trigger _full_reset_from_profile."""
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        _skill_path(project_dir).write_text("user customized")
        manifest_path = project_dir / MANIFEST_FILENAME
        data = json.loads(manifest_path.read_text())
        data["schema_version"] = 999
        manifest_path.write_text(json.dumps(data))

        pm.sync_agent_profile(project_dir)

        assert _skill_path(project_dir).read_text() == "demo v1"
        assert _read_manifest(project_dir)["schema_version"] == MANIFEST_SCHEMA_VERSION

    def test_manifest_profile_id_mismatch_triggers_full_reset(self, env):
        """Mismatched profile_id is equivalent to reset."""
        pm, _, project_dir = env
        pm.sync_agent_profile(project_dir)
        manifest_path = project_dir / MANIFEST_FILENAME
        data = json.loads(manifest_path.read_text())
        data["profile_id"] = "other/foo"
        manifest_path.write_text(json.dumps(data))

        pm.sync_agent_profile(project_dir)

        assert _read_manifest(project_dir)["profile_id"] == EXPECTED_PROFILE_ID
