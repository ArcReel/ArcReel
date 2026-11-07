"""Asset-name path-safety validation.

Asset names are single path components throughout the chain: filenames (characters/{name}.png,
versions/{type}/{name}_v{n}_{ts}.png) and single-segment REST parameters
(PATCH/DELETE /projects/{p}/characters/{name}, POST .../generate/character/{name}).
Names containing separators create nested directories (version-registration shutil.copy2 fails without parents) and
unmatchable URLs (uvicorn decodes %2F to /, making single-segment parameters return 404), so reject them at every creation entry.
"""

import unicodedata

import pytest

from lib.project.asset_types import resolve_asset_key, validate_asset_name
from lib.project.project_manager import ProjectManager

_NAME_NFC = unicodedata.normalize("NFC", "Hiếu")
_NAME_NFD = unicodedata.normalize("NFD", "Hiếu")


class TestValidateAssetName:
    def test_valid_names_pass_and_are_stripped(self):
        assert validate_asset_name("李白") == "李白"
        assert validate_asset_name("  李白  ") == "李白"
        assert validate_asset_name("Mr. Smith-2") == "Mr. Smith-2"

    def test_nfd_input_normalized_to_nfc(self):
        """Registration consistently writes NFC; NFD input must not create visually duplicate assets under another encoding."""
        assert _NAME_NFD != _NAME_NFC
        assert validate_asset_name(_NAME_NFD) == _NAME_NFC
        assert validate_asset_name(_NAME_NFC) == _NAME_NFC
        assert validate_asset_name(f"  {_NAME_NFD}  ") == _NAME_NFC


class TestResolveAssetKey:
    def test_resolves_nfd_registered_key_by_nfc_name(self):
        bucket = {_NAME_NFD: {"description": "d"}}
        assert resolve_asset_key(bucket, _NAME_NFC) == _NAME_NFD
        assert resolve_asset_key(bucket, _NAME_NFD) == _NAME_NFD

    def test_missing_name_returns_none(self):
        assert resolve_asset_key({"李白": {}}, "杜甫") is None

    def test_malformed_bucket_returns_none(self):
        assert resolve_asset_key(None, "李白") is None
        assert resolve_asset_key("not-a-dict", "李白") is None

    def test_duplicate_forms_last_wins(self):
        # Match normalize_asset_bucket merge direction: later writes win.
        bucket = {_NAME_NFC: {"description": "first"}, _NAME_NFD: {"description": "last"}}
        assert resolve_asset_key(bucket, _NAME_NFC) == _NAME_NFD

    def test_duplicate_forms_last_wins_after_json_roundtrip(self, tmp_path):
        """Write order determines the winner, unchanged after disk round-trip; JSON serialization does not reorder keys."""
        import json

        bucket = {_NAME_NFC: {"description": "first"}, _NAME_NFD: {"description": "last"}}
        path = tmp_path / "project.json"
        path.write_text(json.dumps({"characters": bucket}, ensure_ascii=False), encoding="utf-8")

        reloaded = json.loads(path.read_text(encoding="utf-8"))["characters"]
        key = resolve_asset_key(reloaded, _NAME_NFC)
        assert key == _NAME_NFD
        assert reloaded[key]["description"] == "last"

    @pytest.mark.parametrize(
        "bad",
        [
            "李白/诗人",
            "a\\b",
            "..",
            "a/../b",
            "x\0y",
            "",
            "   ",
            None,
            123,
        ],
    )
    def test_illegal_names_rejected(self, bad):
        with pytest.raises(ValueError, match=r"含非法字符|不能为空或仅含空白字符|必须是字符串"):
            validate_asset_name(bad)

    @pytest.mark.parametrize(
        "bad",
        [
            "a:b",
            "a*b",
            "a?b",
            'a"b',
            "a<b",
            "a>b",
            "a|b",
            "a\nb",
            "a\rb",
            "a\tb",
            "a\x1fb",
            "a\x7fb",
            "尾随点.",
            "CON",
            "con",
            "Nul",
            "COM1",
            "lpt9",
            "CON.backup",
        ],
    )
    def test_windows_unsafe_names_rejected(self, bad):
        """Names enter filenames; Windows reserved/control characters, trailing dots, and reserved device names
        can pass validation yet fail disk writing. Projects must migrate across platforms, so reject these consistently everywhere."""
        with pytest.raises(ValueError, match=r"含非法字符|是 Windows 保留设备名|不能以点结尾"):
            validate_asset_name(bad)

    def test_mention_delimiter_rejected(self):
        """Body references use ``@[名称]`` ending at the first ``]``; names containing ``]`` would truncate written references."""
        with pytest.raises(ValueError, match="含非法字符"):
            validate_asset_name("甲]乙")
        assert validate_asset_name("甲[乙") == "甲[乙"

    def test_non_string_reports_type_error(self):
        with pytest.raises(ValueError, match="必须是字符串"):
            validate_asset_name(None)
        with pytest.raises(ValueError, match="必须是字符串"):
            validate_asset_name(123)

    def test_reserved_device_names_not_overmatched(self):
        # Match reserved device names exactly in the first dot segment; allow CON1 / CONAN / COM10.
        assert validate_asset_name("CON1") == "CON1"
        assert validate_asset_name("CONAN") == "CONAN"
        assert validate_asset_name("COM10") == "COM10"


@pytest.fixture
def demo_pm(tmp_path):
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo")
    return manager


class TestProjectManagerCreationEntryPoints:
    def test_add_character_rejects_slash(self, demo_pm):
        with pytest.raises(ValueError, match=r"含非法字符"):
            demo_pm.add_character("demo", "李白/诗人", "desc")
        assert "李白/诗人" not in demo_pm.load_project("demo")["characters"]

    def test_add_project_character_rejects_slash(self, demo_pm):
        with pytest.raises(ValueError, match=r"含非法字符"):
            demo_pm.add_project_character("demo", "李白/诗人", "desc")

    def test_add_batch_rejects_slash(self, demo_pm):
        with pytest.raises(ValueError, match=r"含非法字符"):
            demo_pm.add_scenes_batch("demo", {"庙/宇": {"description": "d"}})
        assert "庙/宇" not in demo_pm.load_project("demo").get("scenes", {})

    def test_add_batch_rejects_normalized_collision(self, demo_pm):
        """Keys equal after strip must not silently overwrite; fail the whole batch loudly without writing, matching upsert_assets."""
        with pytest.raises(ValueError, match="冲突"):
            demo_pm.add_scenes_batch("demo", {"庙宇": {"description": "a"}, "  庙宇  ": {"description": "b"}})
        assert "庙宇" not in demo_pm.load_project("demo").get("scenes", {})

    def test_upsert_assets_rejects_slash(self, demo_pm):
        with pytest.raises(ValueError, match=r"props: 资产名称 .+ 含非法字符"):
            demo_pm.upsert_assets("demo", "props", {"玉/佩": {"description": "d"}})
        assert "玉/佩" not in demo_pm.load_project("demo").get("props", {})

    def test_add_asset_strips_name(self, demo_pm):
        assert demo_pm.add_character("demo", "  李白  ", "desc") is True
        chars = demo_pm.load_project("demo")["characters"]
        assert "李白" in chars
        assert "  李白  " not in chars

    def test_legal_names_still_work(self, demo_pm):
        assert demo_pm.add_character("demo", "李白", "desc") is True
        result = demo_pm.upsert_assets("demo", "scenes", {"庙宇": {"description": "d"}})
        assert "庙宇" in result["added"]
        assert demo_pm.add_props_batch("demo", {"玉佩": {"description": "d"}}) == 1


def _seed_nfd_character(demo_pm: ProjectManager) -> None:
    """Bypass registration to write NFD keys directly, simulating existing data; no migration is needed, as reads/writes resolve by coordinate system."""

    def _mutate(project: dict) -> None:
        project.setdefault("characters", {})[_NAME_NFD] = {"description": "legacy", "voice_style": ""}

    demo_pm.update_project("demo", _mutate)


class TestNfcConvergence:
    def test_add_character_nfd_input_lands_nfc(self, demo_pm):
        assert demo_pm.add_character("demo", _NAME_NFD, "desc") is True
        chars = demo_pm.load_project("demo")["characters"]
        assert _NAME_NFC in chars
        assert _NAME_NFD not in chars

    def test_add_character_nfc_input_skips_nfd_registered(self, demo_pm):
        _seed_nfd_character(demo_pm)
        assert demo_pm.add_character("demo", _NAME_NFC, "desc") is False
        chars = demo_pm.load_project("demo")["characters"]
        assert list(chars) == [_NAME_NFD]

    def test_add_batch_rejects_nfc_nfd_collision(self, demo_pm):
        with pytest.raises(ValueError, match="冲突"):
            demo_pm.add_scenes_batch("demo", {_NAME_NFC: {"description": "a"}, _NAME_NFD: {"description": "b"}})
        assert demo_pm.load_project("demo").get("scenes", {}) == {}

    def test_add_batch_nfc_input_skips_nfd_registered(self, demo_pm):
        _seed_nfd_character(demo_pm)
        assert demo_pm.add_characters_batch("demo", {_NAME_NFC: {"description": "new"}}) == 0
        chars = demo_pm.load_project("demo")["characters"]
        assert list(chars) == [_NAME_NFD]
        assert chars[_NAME_NFD]["description"] == "legacy"

    def test_upsert_nfc_updates_nfd_registered_entry_in_place(self, demo_pm):
        _seed_nfd_character(demo_pm)
        result = demo_pm.upsert_assets("demo", "characters", {_NAME_NFC: {"description": "updated"}})
        assert result["merged"] == [_NAME_NFC]
        chars = demo_pm.load_project("demo")["characters"]
        assert list(chars) == [_NAME_NFD]
        assert chars[_NAME_NFD]["description"] == "updated"

    def test_update_reference_audio_resolves_nfd_registered_key(self, demo_pm):
        _seed_nfd_character(demo_pm)
        demo_pm.update_character_reference_audio("demo", _NAME_NFC, "characters/refs_audio/x.wav")
        chars = demo_pm.load_project("demo")["characters"]
        assert list(chars) == [_NAME_NFD]
        assert chars[_NAME_NFD]["reference_audio"] == "characters/refs_audio/x.wav"

    def test_collect_reference_images_matches_across_forms(self, demo_pm):
        """Script names and bucket keys may differ in form; registration writes NFC without normalizing original script text."""
        demo_pm.add_character("demo", _NAME_NFD, "desc")
        sheet = "characters/sheet.png"
        sheet_path = demo_pm.get_project_path("demo") / sheet
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        sheet_path.write_bytes(b"png")

        def _mutate(project: dict) -> None:
            project["characters"][_NAME_NFC]["character_sheet"] = sheet

        demo_pm.update_project("demo", _mutate)

        refs = demo_pm.collect_reference_images("demo", {"characters_in_scene": [_NAME_NFD]})
        assert refs == [sheet_path]
