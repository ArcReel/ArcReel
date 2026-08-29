"""v10→v11 迁移：按角色是否已设参考音频补写声音绑定方式；版本守卫与幂等。"""

import json
from pathlib import Path

import pytest

from lib.project_migrations.v10_to_v11_character_voice_binding import migrate_project_dict, migrate_v10_to_v11


def _write(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def _load(d: Path) -> dict:
    return json.loads((d / "project.json").read_text(encoding="utf-8"))


class TestMigrateProjectDict:
    def test_project_with_reference_audio_keeps_reference_audio_binding(self):
        """存量项目里已设过参考音频的走的就是直传路径，补写 prompt 会静默换掉声音口径。"""
        after = migrate_project_dict(
            {
                "characters": {
                    "阿岚": {"description": "少女", "reference_audio": "characters/refs_audio/lan.wav"},
                    "老陈": {"description": "老人"},
                }
            }
        )
        assert after["character_voice_binding"] == "reference_audio"

    def test_project_without_reference_audio_gets_prompt_binding(self):
        after = migrate_project_dict({"characters": {"阿岚": {"description": "少女"}}})
        assert after["character_voice_binding"] == "prompt"

    def test_project_without_characters_gets_prompt_binding(self):
        assert migrate_project_dict({"title": "T"})["character_voice_binding"] == "prompt"

    @pytest.mark.parametrize(
        "characters",
        [
            "dirty",
            {"阿岚": "dirty"},
            {"阿岚": {"reference_audio": ""}},
            {"阿岚": {"reference_audio": None}},
            {"阿岚": {"reference_audio": ["a.wav"]}},
        ],
    )
    def test_unreadable_characters_fall_back_to_prompt(self, characters):
        """手编脏值一律当作没设过参考音频：读不出的字段本就挂不上音频。"""
        assert migrate_project_dict({"characters": characters})["character_voice_binding"] == "prompt"

    def test_existing_valid_binding_preserved(self):
        after = migrate_project_dict(
            {
                "character_voice_binding": "prompt",
                "characters": {"阿岚": {"reference_audio": "characters/refs_audio/lan.wav"}},
            }
        )
        assert after["character_voice_binding"] == "prompt"

    def test_invalid_binding_recomputed(self):
        after = migrate_project_dict(
            {
                "character_voice_binding": "whatever",
                "characters": {"阿岚": {"reference_audio": "characters/refs_audio/lan.wav"}},
            }
        )
        assert after["character_voice_binding"] == "reference_audio"

    def test_unrelated_fields_preserved(self):
        after = migrate_project_dict({"title": "T", "video_backend": "ark/m"})
        assert after["title"] == "T"
        assert after["video_backend"] == "ark/m"

    def test_idempotent(self):
        once = migrate_project_dict({"characters": {"阿岚": {"reference_audio": "a.wav"}}})
        assert migrate_project_dict(once) == once


class TestMigrateV10ToV11File:
    def test_bumps_schema_version_and_migrates(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": 10, "characters": {"阿岚": {"reference_audio": "a.wav"}}})
        migrate_v10_to_v11(d)
        data = _load(d)
        assert data["schema_version"] == 11
        assert data["character_voice_binding"] == "reference_audio"

    def test_skips_already_current_project(self, tmp_path: Path):
        d = _write(
            tmp_path,
            {
                "schema_version": 11,
                "character_voice_binding": "prompt",
                "characters": {"阿岚": {"reference_audio": "a.wav"}},
            },
        )
        migrate_v10_to_v11(d)
        assert _load(d)["character_voice_binding"] == "prompt"

    def test_string_schema_version_normalized(self, tmp_path: Path):
        d = _write(tmp_path, {"schema_version": "10", "characters": {}})
        migrate_v10_to_v11(d)
        assert _load(d)["schema_version"] == 11

    def test_missing_project_file_is_noop(self, tmp_path: Path):
        d = tmp_path / "empty"
        d.mkdir()
        migrate_v10_to_v11(d)
        assert not (d / "project.json").exists()

    def test_non_object_project_file_rejected(self, tmp_path: Path):
        d = tmp_path / "demo"
        d.mkdir()
        (d / "project.json").write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError):
            migrate_v10_to_v11(d)
