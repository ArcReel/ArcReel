import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from lib.artifacts.version_manager import (
    InstalledVersionCommit,
    StagedVersionCommit,
    VersionManager,
    VersionSnapshotCorruptedError,
    _get_versions_file_lock,
)
from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.project.project_activity import ACTIVITY_FILENAME, record_project_activity, recorded_project_activity


class TestVersionManager:
    def test_lock_is_reused_for_same_file(self, tmp_path):
        file_a = tmp_path / "a" / "versions.json"
        file_a.parent.mkdir(parents=True)
        lock1 = _get_versions_file_lock(file_a)
        lock2 = _get_versions_file_lock(file_a)
        assert lock1 is lock2

    def test_get_versions_invalid_type_and_helpers(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)

        with pytest.raises(BadRequestError):
            vm.get_versions("bad", "x")

        assert vm.get_current_version("characters", "Alice") == 0
        assert vm.get_version_file_url("characters", "Alice", 1) is None
        assert vm.get_version_prompt("characters", "Alice", 1) is None
        assert vm.get_version_created_at("characters", "Alice", 1) is None
        assert vm.has_versions("characters", "Alice") is False

    def test_add_backup_restore_paths(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)

        current = project / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"png-v1")

        assert vm.backup_current("characters", "Alice", current, "p1") == 1
        assert vm.ensure_current_tracked("characters", "Alice", current, "p2") is None

        # create v2
        current.write_bytes(b"png-v2")
        assert vm.add_version("characters", "Alice", "p2", source_file=current) == 2

        info = vm.get_versions("characters", "Alice")
        assert info["current_version"] == 2
        assert len(info["versions"]) == 2
        assert vm.get_version_file_url("characters", "Alice", 2)
        assert vm.get_version_prompt("characters", "Alice", 2) == "p2"
        # get_version_created_at 返回版本的原始入库时间
        assert vm.get_version_created_at("characters", "Alice", 1)
        assert vm.get_version_created_at("characters", "Alice", 99) is None
        assert vm.has_versions("characters", "Alice")

        restored = vm.restore_version("characters", "Alice", 1, current)
        assert restored["restored_version"] == 1
        assert restored["current_version"] == 1

        info = vm.get_versions("characters", "Alice")
        assert info["current_version"] == 1
        assert len(info["versions"]) == 2

        current.write_bytes(b"png-v3")
        assert vm.add_version("characters", "Alice", "p3", source_file=current) == 3

    def test_restore_callback_runs_under_selection_and_failure_restores_old_current(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "audio" / "segment_E1S01.wav"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"audio-v1")
        v1 = vm.add_version(
            "audio",
            "E1S01",
            "v1",
            source_file=current,
            artifact_audio_basis={"kind": "narration-delivery/tts-audio", "digest": "sha256-v1:" + "1" * 64},
        )
        current.write_bytes(b"audio-v2")
        v2 = vm.add_version("audio", "E1S01", "v2", source_file=current)
        observed = []

        def _fail(record):
            observed.append(record)
            raise RuntimeError("manifest restore failed")

        with pytest.raises(RuntimeError, match="manifest restore failed"):
            vm.restore_version("audio", "E1S01", v1, current, on_restore=_fail)

        assert current.read_bytes() == b"audio-v2"
        assert vm.get_current_version("audio", "E1S01") == v2
        assert observed[0]["artifact_audio_basis"]["kind"] == "narration-delivery/tts-audio"

    @pytest.mark.parametrize(
        ("transaction", "message"),
        [
            ("activation", "version activation failed and durable rollback was incomplete"),
            ("rejection", "version rejection failed and durable rollback was incomplete"),
            ("restore", "version restore failed and durable rollback was incomplete"),
        ],
    )
    def test_failed_media_rollback_preserves_the_recovery_backup(
        self,
        tmp_path,
        monkeypatch,
        transaction,
        message,
    ):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "audio" / "segment_E1S01.wav"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"old-current")
        old_version = vm.add_version("audio", "E1S01", "old", source_file=current)
        operation_failure = RuntimeError("registration failed")
        rollback_failure = OSError("media rollback failed")
        original_replace = os.replace

        def _fail_media_rollback(source, destination):
            if Path(source).suffix == ".rollback" and Path(destination) == current:
                raise rollback_failure
            return original_replace(source, destination)

        def _fail_registration(*_args):
            raise operation_failure

        if transaction == "activation":
            staged = current.with_name(".segment_E1S01.new.wav")
            staged.write_bytes(b"new-current")

            def _run_transaction():
                vm.commit_staged_version(
                    "audio",
                    "E1S01",
                    "new",
                    staged_file=staged,
                    current_file=current,
                    on_commit=_fail_registration,
                )

        elif transaction == "rejection":
            current.write_bytes(b"new-current")
            rejected_version = vm.add_version("audio", "E1S01", "new", source_file=current)

            def _run_transaction():
                vm.reject_current_version(
                    "audio",
                    "E1S01",
                    rejected_version=rejected_version,
                    current_file=current,
                    on_reject=_fail_registration,
                )

        else:
            current.write_bytes(b"new-current")
            vm.add_version("audio", "E1S01", "new", source_file=current)

            def _run_transaction():
                vm.restore_version(
                    "audio",
                    "E1S01",
                    old_version,
                    current,
                    on_restore=_fail_registration,
                )

        monkeypatch.setattr("lib.artifacts.version_manager.os.replace", _fail_media_rollback)

        with pytest.raises(RuntimeError, match=message) as caught:
            _run_transaction()

        backups = list(current.parent.glob(".*.rollback"))
        assert len(backups) == 1
        expected_backup = b"old-current" if transaction == "activation" else b"new-current"
        assert backups[0].read_bytes() == expected_backup
        assert caught.value.__cause__ is rollback_failure
        assert rollback_failure.__cause__ is operation_failure

    def test_restore_errors_and_missing_current(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "characters" / "Alice.png"

        assert vm.backup_current("characters", "Alice", current, "p") is None
        assert vm.ensure_current_tracked("characters", "Alice", current, "p") is None

        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"png")
        with pytest.raises(ValueError, match=r"不支持的资源类型: bad"):
            vm.ensure_current_tracked("bad", "Alice", current, "p")

        with pytest.raises(BadRequestError):
            vm.restore_version("bad", "Alice", 1, current)

        with pytest.raises(NotFoundError):
            vm.restore_version("characters", "missing", 1, current)

        # create record and delete version file to hit FileNotFoundError branch
        vm.add_version("characters", "Alice", "p", source_file=current)
        version_file = project / vm.get_versions("characters", "Alice")["versions"][0]["file"]
        version_file.unlink()

        with pytest.raises(FileNotFoundError):
            vm.restore_version("characters", "Alice", 1, current)

        with pytest.raises(NotFoundError):
            vm.restore_version("characters", "Alice", 99, current)

    def test_commit_staged_version_tracks_unversioned_old_current_and_promotes_new(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "audio" / "segment_E1S01.wav"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"old-paid-audio")
        staged = current.with_name(".segment_E1S01.new.wav")
        staged.write_bytes(b"new-paid-audio")

        version = vm.commit_staged_version(
            resource_type="audio",
            resource_id="E1S01",
            prompt="new text",
            staged_file=staged,
            current_file=current,
            tts_basis_digest="digest-new",
        )

        assert version == 2
        assert current.read_bytes() == b"new-paid-audio"
        assert not staged.exists()
        history = vm.get_versions("audio", "E1S01")
        assert history["current_version"] == 2
        assert [record["prompt"] for record in history["versions"]] == ["", "new text"]
        old_snapshot = project / history["versions"][0]["file"]
        assert old_snapshot.read_bytes() == b"old-paid-audio"

    def test_reject_current_version_keeps_paid_result_in_history_and_restores_previous_media(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"old-video")
        old_version = vm.add_version("videos", "E1S01", "old", source_file=current)
        current.write_bytes(b"short-paid-video")
        rejected_version = vm.add_version("videos", "E1S01", "new", source_file=current)

        assert vm.reject_current_version(
            "videos",
            "E1S01",
            rejected_version=rejected_version,
            current_file=current,
        )

        assert current.read_bytes() == b"old-video"
        history = vm.get_versions("videos", "E1S01")
        assert history["current_version"] == old_version
        assert len(history["versions"]) == 2
        assert history["versions"][-1]["is_current"] is False

    def test_reject_current_version_restores_the_explicit_pre_generation_selection(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True)
        versions: list[int] = []
        for number in range(1, 4):
            current.write_bytes(f"video-v{number}".encode())
            versions.append(vm.add_version("videos", "E1S01", f"v{number}", source_file=current))
        vm.restore_version("videos", "E1S01", versions[0], current)
        current.write_bytes(b"short-paid-video")
        rejected_version = vm.add_version("videos", "E1S01", "rejected", source_file=current)

        assert vm.reject_current_version(
            "videos",
            "E1S01",
            rejected_version=rejected_version,
            restore_version=versions[0],
            current_file=current,
        )

        assert current.read_bytes() == b"video-v1"
        history = vm.get_versions("videos", "E1S01")
        assert history["current_version"] == versions[0]
        assert len(history["versions"]) == 4

    def test_commit_paid_version_can_append_history_without_exposing_it_as_current(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"selected-video")
        selected_version = vm.add_version("videos", "E1S01", "selected", source_file=current)
        staged = current.with_name(".scene_E1S01.late.mp4")
        staged.write_bytes(b"late-paid-video")

        outcome = vm.commit_staged_paid_version(
            resource_type="videos",
            resource_id="E1S01",
            prompt="late",
            staged_file=staged,
            current_file=current,
            select_current=False,
            artifact_video_basis={"kind": "artifact-components/video"},
        )

        assert outcome.selected is False
        assert outcome.version == selected_version + 1
        assert current.read_bytes() == b"selected-video"
        assert not staged.exists()
        history = vm.get_versions("videos", "E1S01")
        assert history["current_version"] == selected_version
        assert len(history["versions"]) == 2
        assert history["versions"][-1]["is_current"] is False
        assert (project / history["versions"][-1]["file"]).read_bytes() == b"late-paid-video"

    def test_paid_version_expected_zero_ignores_internal_legacy_current_bootstrap(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"legacy-current")
        staged = current.with_name(".scene_E1S01.new.mp4")
        staged.write_bytes(b"new-paid-video")

        outcome = vm.commit_staged_paid_version(
            resource_type="videos",
            resource_id="E1S01",
            prompt="new",
            staged_file=staged,
            current_file=current,
            select_current=True,
            expected_current_version=0,
        )

        assert outcome.selected is True
        assert current.read_bytes() == b"new-paid-video"
        history = vm.get_versions("videos", "E1S01")
        assert history["current_version"] == outcome.version
        assert [record["prompt"] for record in history["versions"]] == ["", "new"]
        assert (project / history["versions"][0]["file"]).read_bytes() == b"legacy-current"

    def test_paid_version_history_rollback_preserves_operation_and_cleanup_failures(self, tmp_path, monkeypatch):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        staged = current.with_name(".scene_E1S01.new.mp4")
        staged.parent.mkdir(parents=True)
        staged.write_bytes(b"new-paid-video")
        operation_failure = RuntimeError("versions persistence failed")
        cleanup_failure = OSError("staged cleanup failed")
        original_unlink = Path.unlink

        def _fail_save(_data):
            raise operation_failure

        def _fail_staged_cleanup(path: Path, *, missing_ok: bool = False):
            if path == staged:
                raise cleanup_failure
            return original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(vm, "_save_versions", _fail_save)
        monkeypatch.setattr(Path, "unlink", _fail_staged_cleanup)

        with pytest.raises(RuntimeError, match="history commit failed and rollback was incomplete") as exc_info:
            vm.commit_staged_paid_version(
                resource_type="videos",
                resource_id="E1S01",
                prompt="new",
                staged_file=staged,
                current_file=current,
                select_current=True,
            )

        assert exc_info.value.__cause__ is cleanup_failure
        assert cleanup_failure.__cause__ is operation_failure

    def test_paid_version_cleanup_attempts_backup_after_staged_cleanup_failure(self, tmp_path, monkeypatch, caplog):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"old-current")
        vm.add_version("videos", "E1S01", "old", source_file=current)
        staged = current.with_name(".scene_E1S01.new.mp4")
        staged.write_bytes(b"new-paid-video")
        cleanup_failure = OSError("staged cleanup failed")
        original_unlink = Path.unlink
        cleanup_attempts: list[Path] = []

        def _fail_staged_cleanup(path: Path, *, missing_ok: bool = False):
            cleanup_attempts.append(path)
            if path == staged:
                raise cleanup_failure
            return original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", _fail_staged_cleanup)

        outcome = vm.commit_staged_paid_version(
            resource_type="videos",
            resource_id="E1S01",
            prompt="new",
            staged_file=staged,
            current_file=current,
            select_current=True,
        )

        assert outcome.selected is True
        assert current.read_bytes() == b"new-paid-video"
        assert cleanup_attempts[0] == staged
        assert len(cleanup_attempts) == 2
        assert not list(current.parent.glob(".*.rollback"))
        assert "failed to remove temporary version file" in caplog.text

    def test_paid_version_selection_failure_keeps_history_and_old_current(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"selected-video")
        selected_version = vm.add_version("videos", "E1S01", "selected", source_file=current)
        staged = current.with_name(".scene_E1S01.new.mp4")
        staged.write_bytes(b"new-paid-video")

        def _registration_failure() -> None:
            raise RuntimeError("manifest registration failed")

        with pytest.raises(RuntimeError, match="manifest registration failed"):
            vm.commit_staged_paid_version(
                resource_type="videos",
                resource_id="E1S01",
                prompt="new",
                staged_file=staged,
                current_file=current,
                select_current=True,
                on_select=_registration_failure,
            )

        assert current.read_bytes() == b"selected-video"
        history = vm.get_versions("videos", "E1S01")
        assert history["current_version"] == selected_version
        assert len(history["versions"]) == 2
        assert history["versions"][-1]["is_current"] is False
        assert (project / history["versions"][-1]["file"]).read_bytes() == b"new-paid-video"

    def test_paid_version_backup_copy_failure_keeps_the_selected_media_intact(self, tmp_path, monkeypatch):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"selected-video")
        vm.add_version("videos", "E1S01", "selected", source_file=current)
        staged = current.with_name(".scene_E1S01.new.mp4")
        staged.write_bytes(b"new-paid-video")
        real_copy2 = shutil.copy2

        def _fail_partial_backup(source, destination, *args, **kwargs):
            destination = Path(destination)
            if destination.suffix == ".rollback":
                destination.write_bytes(b"partial-backup")
                raise OSError("backup copy failed")
            return real_copy2(source, destination, *args, **kwargs)

        monkeypatch.setattr(shutil, "copy2", _fail_partial_backup)

        with pytest.raises(OSError, match="backup copy failed"):
            vm.commit_staged_paid_version(
                resource_type="videos",
                resource_id="E1S01",
                prompt="new",
                staged_file=staged,
                current_file=current,
                select_current=True,
            )

        assert current.read_bytes() == b"selected-video"
        assert not list(current.parent.glob(".*.rollback"))

    def test_restore_backup_copy_failure_keeps_the_selected_media_intact(self, tmp_path, monkeypatch):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"video-v1")
        first = vm.add_version("videos", "E1S01", "v1", source_file=current)
        current.write_bytes(b"video-v2")
        vm.add_version("videos", "E1S01", "v2", source_file=current)
        real_copy2 = shutil.copy2

        def _fail_partial_backup(source, destination, *args, **kwargs):
            destination = Path(destination)
            if destination.suffix == ".rollback":
                destination.write_bytes(b"partial-backup")
                raise OSError("backup copy failed")
            return real_copy2(source, destination, *args, **kwargs)

        monkeypatch.setattr(shutil, "copy2", _fail_partial_backup)

        with pytest.raises(OSError, match="backup copy failed"):
            vm.restore_version("videos", "E1S01", first, current)

        assert current.read_bytes() == b"video-v2"
        assert not list(current.parent.glob(".*.rollback"))

    def test_paid_version_selection_decision_runs_after_history_is_durable_under_the_version_lock(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"selected-video")
        selected_version = vm.add_version("videos", "E1S01", "selected", source_file=current)
        staged = current.with_name(".scene_E1S01.late.mp4")
        staged.write_bytes(b"late-paid-video")
        observed: list[tuple[int, int, bytes]] = []

        def _still_current() -> bool:
            history = vm.get_versions("videos", "E1S01")
            observed.append((history["current_version"], len(history["versions"]), current.read_bytes()))
            return False

        outcome = vm.commit_staged_paid_version(
            resource_type="videos",
            resource_id="E1S01",
            prompt="late",
            staged_file=staged,
            current_file=current,
            select_current=_still_current,
        )

        assert outcome.selected is False
        assert observed == [(selected_version, 2, b"selected-video")]
        assert current.read_bytes() == b"selected-video"

    def test_paid_version_selection_token_rejects_a_late_result_without_running_the_basis_callback(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "videos" / "scene_E1S01.mp4"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"first")
        submitted_parent = vm.add_version("videos", "E1S01", "first", source_file=current)
        current.write_bytes(b"user-restored")
        user_selection = vm.add_version("videos", "E1S01", "user", source_file=current)
        staged = current.with_name(".scene_E1S01.late.mp4")
        staged.write_bytes(b"late-paid")

        outcome = vm.commit_staged_paid_version(
            resource_type="videos",
            resource_id="E1S01",
            prompt="late",
            staged_file=staged,
            current_file=current,
            select_current=lambda: pytest.fail("a changed selection token must short-circuit basis comparison"),
            expected_current_version=submitted_parent,
        )

        assert outcome.selected is False
        assert current.read_bytes() == b"user-restored"
        history = vm.get_versions("videos", "E1S01")
        assert history["current_version"] == user_selection
        assert (project / history["versions"][-1]["file"]).read_bytes() == b"late-paid"


@pytest.mark.parametrize(
    "operation",
    [
        "add",
        "staged",
        "installed",
        "batch",
        "paid",
        "reject",
        "reject_batch",
        "failed_reject_batch",
        "restore",
        "metadata",
        "rename",
        "purge",
        "failed_restore",
    ],
)
def test_version_changes_advance_activity_only_after_commit(tmp_path, operation):
    vm = VersionManager(tmp_path)
    current = tmp_path / "characters" / "Alice.png"
    current.parent.mkdir()
    current.write_bytes(b"old")
    vm.add_version("characters", "Alice", "old", source_file=current)
    current.write_bytes(b"new")
    vm.add_version("characters", "Alice", "new", source_file=current)
    (tmp_path / ACTIVITY_FILENAME).write_text("2026-03-01T08:00:00+00:00", encoding="utf-8")
    before = recorded_project_activity(tmp_path)

    def fail_restore(_record):
        raise RuntimeError("registration failed")

    if operation == "add":
        vm.add_version("characters", "Alice", "third", source_file=current)
    elif operation in {"staged", "batch", "paid"}:
        staged = tmp_path / ".candidate.png"
        staged.write_bytes(b"third")
        if operation == "staged":
            vm.commit_staged_version("characters", "Alice", "third", staged_file=staged, current_file=current)
        elif operation == "batch":
            vm.commit_staged_versions((StagedVersionCommit("characters", "Alice", "third", staged, current, {}),))
        else:
            vm.commit_staged_paid_version(
                "characters", "Alice", "third", staged_file=staged, current_file=current, select_current=False
            )
    elif operation == "installed":
        vm.commit_installed_versions((InstalledVersionCommit("characters", "Alice", "third", current, {}),))
    elif operation == "reject":
        assert vm.reject_current_version("characters", "Alice", rejected_version=2, current_file=current)
    elif operation in {"reject_batch", "failed_reject_batch"}:

        def fail_reject(_identities):
            raise RuntimeError("registration failed")

        if operation == "failed_reject_batch":
            with pytest.raises(RuntimeError, match="registration failed"):
                vm.reject_current_versions({("characters", "Alice"): (2, current)}, on_reject=fail_reject)
        else:
            assert vm.reject_current_versions({("characters", "Alice"): (2, current)})
    elif operation == "restore":
        vm.restore_version("characters", "Alice", 1, current)
    elif operation == "metadata":
        assert vm.update_version_metadata("characters", "Alice", 1, prompt="corrected")
    elif operation == "rename":
        vm.rename_resource("characters", "Alice", "Bob")
    elif operation == "purge":
        vm.purge_resource("characters", "Alice")
    else:
        with pytest.raises(RuntimeError, match="registration failed"):
            vm.restore_version("characters", "Alice", 1, current, on_restore=fail_restore)

    after = recorded_project_activity(tmp_path)
    assert before is not None
    assert after is not None
    if operation in {"failed_restore", "failed_reject_batch"}:
        assert after == before
    else:
        assert after > before


def test_failed_batch_rejection_keeps_activity_recorded_by_other_writers(tmp_path):
    # 账本是项目全局的，草稿、记忆等写入方不经过版本管理的锁：批量拒绝回滚时不能抹掉它们同期的记账。
    vm = VersionManager(tmp_path)
    current = tmp_path / "characters" / "Alice.png"
    current.parent.mkdir()
    current.write_bytes(b"old")
    vm.add_version("characters", "Alice", "old", source_file=current)
    current.write_bytes(b"new")
    vm.add_version("characters", "Alice", "new", source_file=current)
    (tmp_path / ACTIVITY_FILENAME).write_text("2026-03-01T08:00:00+00:00", encoding="utf-8")
    before = recorded_project_activity(tmp_path)

    def other_writer_then_fail(_identities):
        record_project_activity(tmp_path)
        raise RuntimeError("registration failed")

    with pytest.raises(RuntimeError, match="registration failed"):
        vm.reject_current_versions({("characters", "Alice"): (2, current)}, on_reject=other_writer_then_fail)

    after = recorded_project_activity(tmp_path)
    assert before is not None
    assert after is not None
    assert after > before


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _record(vm: VersionManager, resource_type: str, resource_id: str, version: int) -> dict:
    return next(v for v in vm.get_versions(resource_type, resource_id)["versions"] if v["version"] == version)


@pytest.mark.parametrize("operation", ["add", "staged", "batch", "paid", "installed"])
@pytest.mark.parametrize(
    ("resource_type", "resource_id", "relative"),
    [
        ("videos", "E1S01", "videos/scene_E1S01.mp4"),
        ("reference_videos", "E1U1", "reference_videos/E1U1.mp4"),
        ("audio", "E1S01", "audio/segment_E1S01.wav"),
    ],
)
def test_every_new_media_version_records_its_output_content_digest(
    tmp_path, operation, resource_type, resource_id, relative
):
    vm = VersionManager(tmp_path)
    current = tmp_path / relative
    current.parent.mkdir(parents=True)
    staged = current.with_name(".staged" + current.suffix)
    staged.write_bytes(b"new-media")
    if operation == "add":
        version = vm.add_version(resource_type, resource_id, "p", source_file=staged)
    elif operation == "staged":
        version = vm.commit_staged_version(resource_type, resource_id, "p", staged_file=staged, current_file=current)
    elif operation == "batch":
        commit = StagedVersionCommit(resource_type, resource_id, "p", staged, current, {})
        version = vm.commit_staged_versions((commit,))[(resource_type, resource_id)]
    elif operation == "paid":
        version = vm.commit_staged_paid_version(
            resource_type, resource_id, "p", staged_file=staged, current_file=current, select_current=False
        ).version
    else:
        staged.replace(current)
        version = vm.commit_installed_version(resource_type, resource_id, "p", current_file=current)

    assert _record(vm, resource_type, resource_id, version)["content_digest"] == _digest(b"new-media")


def test_image_versions_do_not_record_a_content_digest(tmp_path):
    vm = VersionManager(tmp_path)
    current = tmp_path / "characters" / "Alice.png"
    current.parent.mkdir()
    current.write_bytes(b"png")
    version = vm.add_version("characters", "Alice", "p", source_file=current)

    assert "content_digest" not in _record(vm, "characters", "Alice", version)


def _two_audio_versions(tmp_path):
    vm = VersionManager(tmp_path)
    current = tmp_path / "audio" / "segment_E1S01.wav"
    current.parent.mkdir()
    current.write_bytes(b"audio-v1")
    v1 = vm.add_version("audio", "E1S01", "v1", source_file=current)
    current.write_bytes(b"audio-v2")
    v2 = vm.add_version("audio", "E1S01", "v2", source_file=current)
    return vm, current, v1, v2


def test_restoring_a_tampered_snapshot_fails_and_keeps_the_formal_file(tmp_path):
    vm, current, v1, v2 = _two_audio_versions(tmp_path)
    (tmp_path / _record(vm, "audio", "E1S01", v1)["file"]).write_bytes(b"tampered")

    with pytest.raises(VersionSnapshotCorruptedError) as caught:
        vm.restore_version("audio", "E1S01", v1, current)

    assert caught.value.key == "version_snapshot_corrupted"
    assert caught.value.params == {"resource_type": "audio", "resource_id": "E1S01", "version": v1}
    assert current.read_bytes() == b"audio-v2"
    assert vm.get_current_version("audio", "E1S01") == v2


def test_restore_replace_failure_leaves_the_complete_prior_formal_file(tmp_path, monkeypatch):
    vm, current, v1, v2 = _two_audio_versions(tmp_path)
    original_replace = os.replace

    def _fail_install(source, destination):
        if Path(destination) == current and Path(source).suffix != ".rollback":
            raise OSError("power lost")
        return original_replace(source, destination)

    monkeypatch.setattr("lib.artifacts.version_manager.os.replace", _fail_install)

    with pytest.raises(OSError, match="power lost"):
        vm.restore_version("audio", "E1S01", v1, current)

    assert current.read_bytes() == b"audio-v2"
    assert vm.get_current_version("audio", "E1S01") == v2
    assert sorted(p.name for p in current.parent.iterdir()) == [current.name]


def test_legacy_version_without_a_content_digest_still_restores(tmp_path):
    vm, current, v1, _v2 = _two_audio_versions(tmp_path)
    data = json.loads(vm.versions_file.read_text(encoding="utf-8"))
    for record in data["audio"]["E1S01"]["versions"]:
        del record["content_digest"]
    vm.versions_file.write_text(json.dumps(data), encoding="utf-8")

    vm.restore_version("audio", "E1S01", v1, current)

    assert current.read_bytes() == b"audio-v1"
    assert vm.get_current_version("audio", "E1S01") == v1


def test_rejection_refuses_to_reinstall_a_tampered_prior_snapshot(tmp_path):
    vm, current, v1, v2 = _two_audio_versions(tmp_path)
    (tmp_path / _record(vm, "audio", "E1S01", v1)["file"]).write_bytes(b"tampered")

    with pytest.raises(VersionSnapshotCorruptedError):
        vm.reject_current_version("audio", "E1S01", rejected_version=v2, current_file=current)

    assert current.read_bytes() == b"audio-v2"
    assert vm.get_current_version("audio", "E1S01") == v2


def test_verify_snapshots_finds_the_record_by_snapshot_path(tmp_path):
    vm, _current, v1, v2 = _two_audio_versions(tmp_path)
    v1_file = _record(vm, "audio", "E1S01", v1)["file"]
    v2_file = _record(vm, "audio", "E1S01", v2)["file"]
    (tmp_path / v1_file).write_bytes(b"tampered")

    vm.verify_snapshots("audio", {v2_file, "audio/unrecorded.wav"})
    with pytest.raises(VersionSnapshotCorruptedError) as caught:
        vm.verify_snapshots("audio", {v1_file, v2_file})

    assert caught.value.params == {"resource_type": "audio", "resource_id": "E1S01", "version": v1}
