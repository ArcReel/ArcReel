import os

import pytest

from lib.project.asset_derivatives import derivative_sheet_relative_path
from lib.project.asset_fingerprints import compute_asset_fingerprints
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE, version_snapshot_relative_path

#: Two pinned fingerprint-test mtimes need only differ; their particular values are immaterial.
_MTIME_BEFORE = 1_700_000_000
_MTIME_AFTER = _MTIME_BEFORE + 60


class TestComputeAssetFingerprints:
    def test_empty_project(self, tmp_path):
        result = compute_asset_fingerprints(tmp_path)
        assert result == {}

    def test_scans_media_subdirs(self, tmp_path):
        (tmp_path / "storyboards").mkdir()
        sb = tmp_path / "storyboards" / "scene_E1S01.png"
        sb.write_bytes(b"img")

        (tmp_path / "videos").mkdir()
        vid = tmp_path / "videos" / "scene_E1S01.mp4"
        vid.write_bytes(b"vid")

        result = compute_asset_fingerprints(tmp_path)
        assert "storyboards/scene_E1S01.png" in result
        assert "videos/scene_E1S01.mp4" in result
        assert isinstance(result["storyboards/scene_E1S01.png"], int)

    def test_includes_thumbnails_characters_scenes_props(self, tmp_path):
        for subdir, name in [
            ("thumbnails", "scene_E1S01.jpg"),
            ("characters", "Alice.png"),
            ("scenes", "庙宇.png"),
            ("props", "玉佩.png"),
            ("products", "手镯.png"),
        ]:
            (tmp_path / subdir).mkdir()
            (tmp_path / subdir / name).write_bytes(b"x")

        result = compute_asset_fingerprints(tmp_path)
        assert "thumbnails/scene_E1S01.jpg" in result
        assert "characters/Alice.png" in result
        assert "scenes/庙宇.png" in result
        assert "props/玉佩.png" in result
        assert "products/手镯.png" in result

    def test_includes_audio_and_bgm(self, tmp_path):
        """Voiceovers and project BGM also use public media endpoints; full refresh must retain their fingerprints."""
        for subdir, name in [("audio", "segment_E1S01.wav"), ("bgm", "theme.mp3")]:
            (tmp_path / subdir).mkdir()
            (tmp_path / subdir / name).write_bytes(b"x")

        result = compute_asset_fingerprints(tmp_path)
        assert "audio/segment_E1S01.wav" in result
        assert "bgm/theme.mp3" in result

    def test_includes_root_level_assets(self, tmp_path):
        (tmp_path / "style_reference.png").write_bytes(b"style")
        result = compute_asset_fingerprints(tmp_path)
        assert "style_reference.png" in result

    def test_ignores_non_media_files(self, tmp_path):
        (tmp_path / "project.json").write_text("{}")
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "ep01.json").write_text("{}")
        result = compute_asset_fingerprints(tmp_path)
        assert result == {}

    def test_fingerprint_changes_when_file_modified(self, tmp_path):
        """Changed files must receive new fingerprints.

        Set both mtimes explicitly with ``os.utime`` instead of sleeping for clock advancement. Filesystem mtime granularity
        varies by platform, so any sleep duration is a guess; explicit values are deterministic and take no waiting time.
        """
        (tmp_path / "storyboards").mkdir()
        f = tmp_path / "storyboards" / "scene_E1S01.png"
        f.write_bytes(b"v1")
        os.utime(f, (_MTIME_BEFORE, _MTIME_BEFORE))
        fp1 = compute_asset_fingerprints(tmp_path)["storyboards/scene_E1S01.png"]

        f.write_bytes(b"v2")
        os.utime(f, (_MTIME_AFTER, _MTIME_AFTER))
        fp2 = compute_asset_fingerprints(tmp_path)["storyboards/scene_E1S01.png"]

        assert fp2 != fp1

    def test_scans_characters_refs_subdirectory(self, tmp_path):
        refs_dir = tmp_path / "characters" / "refs"
        refs_dir.mkdir(parents=True)
        (refs_dir / "Hero.png").write_bytes(b"ref")

        result = compute_asset_fingerprints(tmp_path)
        assert "characters/refs/Hero.png" in result
        assert isinstance(result["characters/refs/Hero.png"], int)

    def test_scans_character_derivative_sheets(self, tmp_path):
        """Derivative asset images live at depth three and receive project-load fingerprints just like base asset images."""
        sheet_path = tmp_path / derivative_sheet_relative_path("Hero", "重甲")
        sheet_path.parent.mkdir(parents=True)
        sheet_path.write_bytes(b"derivative")

        result = compute_asset_fingerprints(tmp_path)
        assert "characters/derivatives/Hero/重甲.png" in result
        assert isinstance(result["characters/derivatives/Hero/重甲.png"], int)

    @pytest.mark.parametrize(
        "snapshot_rel",
        [
            # Actual snapshot location: project-root versions/{subdir}/, excluded by the _MEDIA_SUBDIRS allowlist.
            version_snapshot_relative_path(
                CHARACTER_DERIVATIVE_RESOURCE_TYPE, "Hero/重甲", version=1, timestamp="20260101_000000"
            ),
            # Same-named directories inside media subdirectories are excluded by versions skipping, at both depths two and four.
            "storyboards/versions/scene_E1S01_v1_20260101_000000.png",
            "characters/derivatives/Hero/versions/重甲_v1_20260101_000000.png",
        ],
    )
    def test_ignores_version_snapshots(self, tmp_path, snapshot_rel):
        """Snapshots address by version number and file routes mark them immutable by path; they do not participate in cache busting."""
        snapshot = tmp_path / snapshot_rel
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(b"old")

        assert compute_asset_fingerprints(tmp_path) == {}

    def test_ignores_symlinked_media_directories(self, tmp_path):
        """Do not descend directory symlinks; their targets are not the project's own media."""
        derivatives = tmp_path / "characters" / "derivatives"
        derivatives.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "Hero.png").write_bytes(b"elsewhere")
        (derivatives / "Hero").symlink_to(outside, target_is_directory=True)

        assert compute_asset_fingerprints(tmp_path) == {}

    def test_ignores_a_symlinked_media_subdir_itself(self, tmp_path):
        """Do not descend symlinked media subdirectories either, matching every nested level."""
        outside = tmp_path / "outside"
        (outside / "refs").mkdir(parents=True)
        (outside / "Hero.png").write_bytes(b"elsewhere")
        (outside / "refs" / "pose.png").write_bytes(b"elsewhere")
        (tmp_path / "characters").symlink_to(outside, target_is_directory=True)

        assert compute_asset_fingerprints(tmp_path) == {}
