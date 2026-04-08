import time

from lib.asset_fingerprints import compute_asset_fingerprints


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

    def test_includes_thumbnails_and_characters_and_clues(self, tmp_path):
        for subdir, name in [
            ("thumbnails", "scene_E1S01.jpg"),
            ("characters", "Alice.png"),
            ("clues", "jade-pendant.png"),
        ]:
            (tmp_path / subdir).mkdir()
            (tmp_path / subdir / name).write_bytes(b"x")

        result = compute_asset_fingerprints(tmp_path)
        assert "thumbnails/scene_E1S01.jpg" in result
        assert "characters/Alice.png" in result
        assert "clues/jade-pendant.png" in result

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
        (tmp_path / "storyboards").mkdir()
        f = tmp_path / "storyboards" / "scene_E1S01.png"
        f.write_bytes(b"v1")
        fp1 = compute_asset_fingerprints(tmp_path)["storyboards/scene_E1S01.png"]

        time.sleep(0.1)
        f.write_bytes(b"v2")
        fp2 = compute_asset_fingerprints(tmp_path)["storyboards/scene_E1S01.png"]
        assert fp2 != fp1

    def test_scans_characters_refs_subdirectory(self, tmp_path):
        refs_dir = tmp_path / "characters" / "refs"
        refs_dir.mkdir(parents=True)
        (refs_dir / "Hero.png").write_bytes(b"ref")

        result = compute_asset_fingerprints(tmp_path)
        assert "characters/refs/Hero.png" in result
        assert isinstance(result["characters/refs/Hero.png"], int)

    def test_ignores_versions_subdirectory(self, tmp_path):
        versions_dir = tmp_path / "storyboards" / "versions"
        versions_dir.mkdir(parents=True)
        (versions_dir / "v1.png").write_bytes(b"old")

        result = compute_asset_fingerprints(tmp_path)
        assert not any("versions" in k for k in result)
