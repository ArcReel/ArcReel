import pytest

from lib.api_errors import BadRequestError, NotFoundError
from lib.version_manager import VersionManager, _get_versions_file_lock

pytestmark = pytest.mark.unit


class TestVersionManagerMore:
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
        # 还原参考视频时要拿被还原版本的原始入库时间回填 video_generated_at
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

    def test_restore_errors_and_missing_current(self, tmp_path):
        project = tmp_path / "demo"
        vm = VersionManager(project)
        current = project / "characters" / "Alice.png"

        assert vm.backup_current("characters", "Alice", current, "p") is None
        assert vm.ensure_current_tracked("characters", "Alice", current, "p") is None

        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"png")
        with pytest.raises(ValueError):
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
