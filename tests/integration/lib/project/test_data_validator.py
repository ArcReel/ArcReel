import json
from pathlib import Path

import pytest

from lib.project.data_validator import (
    DRAMA_SPEECH_OVERFLOW_TOLERANCE,
    DataValidator,
    validate_episode,
    validate_project,
)
from lib.speech.speech_rate import estimate_spoken_seconds


def _write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _project_payload(content_mode: str = "narration") -> dict:
    return {
        "title": "Demo",
        "content_mode": content_mode,
        "generation_mode": "storyboard",
        "style": "Anime",
        "characters": {
            "姜月茴": {"description": "女主"},
        },
        "scenes": {
            "古宅": {"description": "废弃古宅，阴暗潮湿"},
        },
        "props": {
            "玉佩": {"description": "关键道具"},
        },
    }


class TestDataValidator:
    def test_validate_project_success(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload())

        validator = DataValidator(projects_dir=str(tmp_path / "projects"))
        result = validator.validate_project("demo")

        assert result.valid
        assert result.errors == []
        assert "验证通过" in str(result)

    @pytest.mark.parametrize("style", [None, ""], ids=["absent", "empty"])
    def test_validate_project_accepts_project_without_style(self, tmp_path, style):
        """Unselected styles, such as custom style images or no choice yet, are valid; style is optional."""
        payload = _project_payload()
        if style is None:
            del payload["style"]
        else:
            payload["style"] = style
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert result.valid
        assert result.errors == []

    @pytest.mark.parametrize("style", [None, 123, False, [], {}], ids=["null", "int", "bool", "list", "dict"])
    def test_validate_project_rejects_non_string_style(self, tmp_path, style):
        """Present style values must be strings; explicit null/other types are type errors, not valid unselected styles."""
        payload = _project_payload()
        payload["style"] = style
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        assert any("字段类型错误：style 应为字符串" in error for error in result.errors)

    @pytest.mark.parametrize("style", [None, "", " \n"], ids=["absent", "empty", "whitespace"])
    def test_validate_project_rejects_selected_template_without_snapshot(self, tmp_path, style):
        """Selected style templates require expanded snapshots, or settings would show selection while generation lacks style."""
        payload = _project_payload()
        payload["style_template_id"] = "live_cinematic"
        if style is None:
            del payload["style"]
        else:
            payload["style"] = style
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        assert any("缺少必填字段：style" in error for error in result.errors)

    @pytest.mark.parametrize("template_id", [0, ["live_cinematic"]], ids=["falsy", "truthy"])
    def test_validate_project_rejects_non_string_template_id(self, tmp_path, template_id):
        """Nonstring template IDs are type errors, excluded from selected-template-needs-snapshot checks."""
        payload = _project_payload()
        payload["style_template_id"] = template_id
        payload["style"] = ""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        assert any("字段类型错误：style_template_id 应为字符串" in error for error in result.errors)
        assert not any("缺少必填字段：style" in error for error in result.errors)

    def test_validate_project_treats_empty_template_id_as_none(self, tmp_path):
        """Creation expands templates by truthiness; empty IDs equal null and require no snapshot."""
        payload = _project_payload()
        payload["style_template_id"] = ""
        payload["style"] = ""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert result.valid

    def test_validate_project_accepts_selected_template_with_snapshot(self, tmp_path):
        payload = _project_payload()
        payload["style_template_id"] = "live_cinematic"
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert result.valid

    def test_validate_project_reports_missing_and_invalid_fields(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        # Only absent title errors; empty strings are valid under the new frontend i18n fallback policy.
        _write_json(
            project_dir / "project.json",
            {
                "content_mode": "invalid",
                "style": "",
                "characters": {"A": []},
                "scenes": {
                    "X": {"description": ""},
                },
                "props": {
                    "Y": {"description": ""},
                },
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        # Absent title means missing required field, distinct from field-type errors.
        assert any("缺少必填字段：title" in error for error in result.errors)
        assert any("content_mode" in error for error in result.errors)
        assert any("角色 'A' 数据格式错误" in error for error in result.errors)
        # Empty scene/prop descriptions do not error.
        assert not any("场景 'X'" in error for error in result.errors)
        assert not any("道具 'Y'" in error for error in result.errors)

    def test_validate_project_rejects_non_string_title(self, tmp_path):
        # Existing nonstring title, such as int/null/list, needs explicit type-error copy distinct from absence,
        # preventing callers from assuming they omitted the field.
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload()
        payload["title"] = 123
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        assert any("字段类型错误：title 应为字符串" in error for error in result.errors)
        assert not any("缺少必填字段：title" in error for error in result.errors)

    def test_validate_project_allows_empty_title(self, tmp_path):
        # Empty titles are valid; frontend uses the unnamed-project i18n fallback.
        # lib no longer requires nonempty titles, avoiding forced slug fallbacks in ProjectManager writes.
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload()
        payload["title"] = ""
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert result.valid
        assert not any("title" in error for error in result.errors)

    def test_validate_project_rejects_non_string_voice_updated_at(self, tmp_path):
        # voice_updated_at is outside extra_string_fields, a system-only timestamp not exposed through generic PATCH,
        # but still needs type checks: external edits/imports could silently cause NaN in frontend timestamp comparisons.
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload()
        payload["characters"]["姜月茴"]["voice_updated_at"] = 12345
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        assert any("voice_updated_at 必须是字符串" in error for error in result.errors)

    def test_validate_project_rejects_unparseable_voice_updated_at(self, tmp_path):
        # Strings that are invalid ISO8601 evade type checks, but frontend
        # `new Date(iso).getTime()` yields NaN, making comparisons always false and silently disabling banners.
        # This is subtler than type errors; separately validate value parseability.
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload()
        payload["characters"]["姜月茴"]["voice_updated_at"] = "not-a-date"
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        assert any("voice_updated_at 不是合法的 ISO8601 时间戳" in error for error in result.errors)

    def test_validate_project_rejects_unparseable_voice_notice_dismissed_at(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload()
        payload["characters"]["姜月茴"]["voice_notice_dismissed_at"] = "not-a-date"
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        assert any("voice_notice_dismissed_at 不是合法的 ISO8601 时间戳" in error for error in result.errors)

    def test_validate_project_accepts_z_and_offset_voice_timestamps(self, tmp_path):
        # Accept both repository-written formats: seconds with Z from restoration, and microseconds with +00:00 offset.
        # （datetime.now(UTC).isoformat()）。
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload()
        payload["characters"]["姜月茴"]["voice_updated_at"] = "2026-01-02T00:00:00.500000+00:00"
        payload["characters"]["姜月茴"]["voice_notice_dismissed_at"] = "2026-01-02T00:00:00Z"
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

        assert result.valid

    def test_validate_episode_narration_success_with_warnings(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "novel_text": "原文",
                        "characters_in_segment": ["姜月茴"],
                        "scenes": ["古宅"],
                        "props": ["玉佩"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert result.valid
        assert any("缺少 duration_seconds" in w for w in result.warnings)

    def test_validate_episode_accepts_an_empty_title(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {"episode": 1, "title": "", "content_mode": "narration", "segments": []},
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not any("title" in error for error in result.errors)

    def test_validate_episode_rejects_missing_narration_audio_file(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "novel_text": "原文",
                        "characters_in_segment": ["姜月茴"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                        "generated_assets": {"narration_audio": "audio/segment_E1S01.wav"},
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        # Missing referenced audio files cause central validation errors, without silent allowlist acceptance.
        assert not result.valid
        assert any("narration_audio" in error for error in result.errors)

    def test_validate_episode_accepts_existing_narration_audio(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        audio_file = project_dir / "audio" / "segment_E1S01.wav"
        audio_file.parent.mkdir(parents=True, exist_ok=True)
        audio_file.write_bytes(b"RIFFfakewav")
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "novel_text": "原文",
                        "characters_in_segment": ["姜月茴"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                        "generated_assets": {"narration_audio": "audio/segment_E1S01.wav"},
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        # Existing files pass the whole chain without narration_audio errors.
        assert result.valid
        assert not any("narration_audio" in error for error in result.errors)

    def test_validate_episode_accepts_split_segment_ids_and_missing_scenes_props_warning(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S03_1",
                        "novel_text": "原文",
                        "characters_in_segment": ["姜月茴"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert result.valid
        # scenes/props are optional; warn when missing.
        assert any("缺少 scenes" in warning for warning in result.warnings)
        assert any("缺少 props" in warning for warning in result.warnings)

    def test_validate_episode_reports_invalid_references_and_fields(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": "bad",
                "title": "",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "bad-id",
                        "duration_seconds": 5,
                        "novel_text": "",
                        "characters_in_segment": ["未知角色"],
                        "scenes": ["未知场景"],
                        "props": ["未知道具"],
                        "image_prompt": "",
                        "video_prompt": "",
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not result.valid
        assert any("episode (整数)" in error for error in result.errors)
        assert any("segment_id 格式错误" in error for error in result.errors)
        # 5 is a valid positive integer, with no duration_seconds error.
        assert not any("duration_seconds 值无效" in error for error in result.errors)
        assert any("不存在于 project.json 的角色" in error for error in result.errors)
        assert any("不存在于 project.json 的场景" in error for error in result.errors)
        assert any("不存在于 project.json 的道具" in error for error in result.errors)

    def test_validate_episode_accepts_nfc_nfd_mismatch_on_narration_segment_refs(self, tmp_path):
        """Normalize all segment character/scene/prop references to NFC for comparison; after registration writes NFC,
        legacy scripts retaining NFD spellings remain valid instead of appearing to reference unregistered assets."""
        import unicodedata

        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        assert name_nfc != name_nfd

        project = _project_payload("narration")
        project["characters"] = {name_nfc: {"description": "女主"}}
        project["scenes"] = {name_nfc: {"description": "场景"}}
        project["props"] = {name_nfc: {"description": "道具"}}

        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", project)
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": 4,
                        "novel_text": "原文",
                        "characters_in_segment": [name_nfd],
                        "scenes": [name_nfd],
                        "props": [name_nfd],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")
        assert result.valid, result.errors

    def test_validate_episode_accepts_nfc_nfd_mismatch_on_drama_scene_refs(self, tmp_path):
        """Drama's three reference types normalize identically to narration segments."""
        import unicodedata

        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")

        project = _project_payload("drama")
        project["characters"] = {name_nfc: {"description": "女主"}}
        project["scenes"] = {name_nfc: {"description": "场景"}}
        project["props"] = {name_nfc: {"description": "道具"}}

        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", project)
        _write_json(
            project_dir / "scripts" / "episode_2.json",
            {
                "episode": 2,
                "title": "第二集",
                "content_mode": "drama",
                "scenes": [
                    {
                        "scene_id": "E2S01",
                        "duration_seconds": 8,
                        "characters_in_scene": [name_nfd],
                        "scenes": [name_nfd],
                        "props": [name_nfd],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = validate_episode("demo", "episode_2.json", projects_dir=str(tmp_path / "projects"))
        assert result.valid, result.errors

    @pytest.mark.parametrize("bad_duration", [0, -1, "5", 4.5, True])
    def test_validate_episode_rejects_non_positive_integer_duration(self, tmp_path, bad_duration):
        """Reject duration_seconds that is not a positive integer: 0 / negative / string / float / bool."""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "x",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": bad_duration,
                        "novel_text": "x",
                        "image_prompt": "x",
                        "video_prompt": "x",
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not result.valid, f"bad={bad_duration}"
        assert any("duration_seconds 值无效" in e for e in result.errors), f"bad={bad_duration}; errors={result.errors}"

    def test_validate_episode_drama_mode(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("drama"))
        _write_json(
            project_dir / "scripts" / "episode_2.json",
            {
                "episode": 2,
                "title": "第二集",
                "content_mode": "drama",
                "scenes": [
                    {
                        "scene_id": "E2S01",
                        "duration_seconds": 8,
                        "characters_in_scene": ["姜月茴"],
                        "scenes": ["古宅"],
                        "props": ["玉佩"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = validate_episode("demo", "episode_2.json", projects_dir=str(tmp_path / "projects"))
        assert result.valid

    def _drama_episode_with_scene(self, tmp_path, scene_extra: dict, project_extra: dict | None = None):
        # Minimal drama episode; merge scene_extra into scenes for targeted utterance checks.
        project_dir = tmp_path / "projects" / "demo"
        project = _project_payload("drama")
        project.update(project_extra or {})
        _write_json(project_dir / "project.json", project)
        scene = {
            "scene_id": "E2S01",
            "duration_seconds": 8,
            "characters_in_scene": ["姜月茴"],
            "scenes": ["古宅"],
            "props": ["玉佩"],
            "image_prompt": "img",
            "video_prompt": "vid",
        }
        scene.update(scene_extra)
        _write_json(
            project_dir / "scripts" / "episode_2.json",
            {"episode": 2, "title": "第二集", "content_mode": "drama", "scenes": [scene]},
        )
        return validate_episode("demo", "episode_2.json", projects_dir=str(tmp_path / "projects"))

    def test_validate_episode_drama_accepts_valid_utterances(self, tmp_path):
        # Valid utterances: dialogue with speaker, voiceover without speaker → pass.
        result = self._drama_episode_with_scene(
            tmp_path,
            {
                "utterances": [
                    {"kind": "dialogue", "speaker": "姜月茴", "text": "你来了。"},
                    {"kind": "voiceover", "speaker": None, "text": "那是命运的开端。"},
                ]
            },
        )
        assert result.valid

    def test_validate_episode_drama_rejects_non_list_utterances(self, tmp_path):
        # Nonarray utterances → structural error.
        result = self._drama_episode_with_scene(tmp_path, {"utterances": "这不是数组"})
        assert not result.valid
        assert any("utterances" in error for error in result.errors)

    def test_validate_episode_drama_rejects_dialogue_without_speaker(self, tmp_path):
        # kind ⇄ speaker: dialogue without nonempty speaker → validation failure.
        result = self._drama_episode_with_scene(
            tmp_path, {"utterances": [{"kind": "dialogue", "speaker": "", "text": "无主台词"}]}
        )
        assert not result.valid
        assert any("speaker" in error for error in result.errors)

    def test_validate_episode_drama_rejects_voiceover_with_speaker(self, tmp_path):
        # kind ⇄ speaker: voiceover with speaker → validation failure.
        result = self._drama_episode_with_scene(
            tmp_path, {"utterances": [{"kind": "voiceover", "speaker": "旁白人", "text": "解说"}]}
        )
        assert not result.valid
        assert any("speaker" in error for error in result.errors)

    def test_validate_episode_drama_rejects_non_string_speaker(self, tmp_path):
        # Speaker values other than strings/null, such as numbers, fail like Pydantic speaker: str | None,
        # instead of passing structure checks and later failing Pydantic.
        result = self._drama_episode_with_scene(
            tmp_path, {"utterances": [{"kind": "voiceover", "speaker": 123, "text": "解说"}]}
        )
        assert not result.valid
        assert any("speaker" in error for error in result.errors)

    def test_validate_episode_drama_accepts_voiceover_blank_speaker(self, tmp_path):
        # Empty/whitespace voiceover speakers mean no speaker, matching Pydantic _normalize_speaker → allow;
        # do not be stricter than authoritative Pydantic models.
        result = self._drama_episode_with_scene(
            tmp_path, {"utterances": [{"kind": "voiceover", "speaker": "  ", "text": "解说"}]}
        )
        assert result.valid

    def test_validate_episode_drama_legacy_voiceover_tolerated(self, tmp_path):
        # Legacy drama without utterances but with old voiceover uses read migration; validation allows export.
        result = self._drama_episode_with_scene(tmp_path, {"voiceover": ["旧画外音"]})
        assert result.valid

    def test_validate_episode_drama_warns_when_speech_overflows_scene(self, tmp_path):
        # One-sided upper bound: estimated dialogue exceeds scene duration × tolerance → warn only, without blocking save.
        long_line = "台词" * 60  # 120 Han reading units greatly exceed the 8-second scene tolerance.
        assert estimate_spoken_seconds(long_line, None) > 8 * (1 + DRAMA_SPEECH_OVERFLOW_TOLERANCE)
        result = self._drama_episode_with_scene(
            tmp_path,
            {
                "duration_seconds": 8,
                "utterances": [{"kind": "dialogue", "speaker": "姜月茴", "text": long_line}],
            },
        )
        assert result.valid, result.errors
        assert any("说话时长" in w for w in result.warnings)

    def test_validate_episode_drama_speech_overflow_uses_project_speech_rate(self, tmp_path):
        # Project speech-rate overrides apply: faster settings suppress the same dialogue's speech-volume warning.
        line = "台词" * 60
        scene = {
            "duration_seconds": 8,
            "utterances": [{"kind": "dialogue", "speaker": "姜月茴", "text": line}],
        }
        slow = self._drama_episode_with_scene(tmp_path / "slow", scene)
        assert any("说话时长" in w for w in slow.warnings)
        fast = self._drama_episode_with_scene(tmp_path / "fast", scene, {"speech_rate_units_per_second": 20})
        assert not any("说话时长" in w for w in fast.warnings)

    def test_validate_project_rejects_out_of_range_speech_rate(self, tmp_path):
        # Present speech-rate overrides must lie within hard bounds; out-of-range/nonnumeric means error.
        # 10**400 is legal project.json integer syntax beyond double precision; report out-of-range without interrupting validation.
        for index, bad in enumerate((0, -1, 20.5, "5", 10**400)):
            case_root = tmp_path / f"case-{index}"
            project_dir = case_root / "projects" / "demo"
            payload = _project_payload("drama")
            payload["speech_rate_units_per_second"] = bad
            _write_json(project_dir / "project.json", payload)
            result = validate_project("demo", projects_dir=str(case_root / "projects"))
            assert not result.valid
            assert any("speech_rate_units_per_second" in e for e in result.errors)

    def test_validate_project_accepts_in_range_speech_rate(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload("drama")
        payload["speech_rate_units_per_second"] = 6.5
        _write_json(project_dir / "project.json", payload)
        result = validate_project("demo", projects_dir=str(tmp_path / "projects"))
        assert result.valid, result.errors

    def test_validate_project_rejects_out_of_range_episode_target_duration(self, tmp_path):
        # Present values require integer seconds within hard bounds; out-of-range/float/nonnumeric means error.
        for index, bad in enumerate((5, 900, 120.0, "120")):
            case_root = tmp_path / f"etd-{index}"
            project_dir = case_root / "projects" / "demo"
            payload = _project_payload("drama")
            payload["episode_target_duration"] = bad
            _write_json(project_dir / "project.json", payload)
            result = validate_project("demo", projects_dir=str(case_root / "projects"))
            assert not result.valid
            assert any("episode_target_duration" in e for e in result.errors)

    def test_validate_project_accepts_in_range_episode_target_duration(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload("drama")
        payload["episode_target_duration"] = 120
        _write_json(project_dir / "project.json", payload)
        result = validate_project("demo", projects_dir=str(tmp_path / "projects"))
        assert result.valid, result.errors

    def test_validate_episode_drama_speech_overflow_counts_voiceover(self, tmp_path):
        # Speech volume includes dialogue plus voiceover, matching subtitle derivation.
        long_vo = "旁白" * 60
        result = self._drama_episode_with_scene(
            tmp_path,
            {
                "duration_seconds": 8,
                "utterances": [{"kind": "voiceover", "speaker": None, "text": long_vo}],
            },
        )
        assert result.valid, result.errors
        assert any("说话时长" in w for w in result.warnings)

    def test_validate_episode_drama_no_warning_when_speech_fits(self, tmp_path):
        # Within scene duration × tolerance, emit no speech-volume warning.
        result = self._drama_episode_with_scene(
            tmp_path,
            {
                "duration_seconds": 8,
                "utterances": [
                    {"kind": "dialogue", "speaker": "姜月茴", "text": "你来了。"},
                    {"kind": "voiceover", "speaker": None, "text": "夜色渐深。"},
                ],
            },
        )
        assert result.valid, result.errors
        assert not any("说话时长" in w for w in result.warnings)

    def test_validate_episode_drama_no_warning_when_speech_far_under_duration(self, tmp_path):
        # One-sided bound: much less speech than storyboard duration does not warn; visuals drive duration and silence is valid.
        result = self._drama_episode_with_scene(
            tmp_path,
            {
                "duration_seconds": 30,
                "utterances": [{"kind": "dialogue", "speaker": "姜月茴", "text": "嗯。"}],
            },
        )
        assert result.valid, result.errors
        assert not any("说话时长" in w for w in result.warnings)

    def test_validate_episode_drama_no_speech_warning_without_utterances(self, tmp_path):
        # No utterances in legacy/unfilled data means no speech-volume warnings or crashes.
        result = self._drama_episode_with_scene(tmp_path, {"duration_seconds": 8})
        assert result.valid, result.errors
        assert not any("说话时长" in w for w in result.warnings)

    def test_validate_episode_drama_accepts_source_text(self, tmp_path):
        # String source_text verbatim anchors → pass.
        result = self._drama_episode_with_scene(tmp_path, {"source_text": "推门而入，信纸还在桌上。"})
        assert result.valid

    def test_validate_episode_drama_accepts_missing_source_text(self, tmp_path):
        # Missing source_text from legacy/best-effort output → allow, default empty string.
        result = self._drama_episode_with_scene(tmp_path, {})
        assert result.valid

    @pytest.mark.parametrize("field", ["source_text", "scene_description"])
    def test_validate_episode_drama_rejects_non_string_text_field(self, tmp_path, field):
        # Nonstring values, such as numbers, fail, mirroring Pydantic str constraints.
        result = self._drama_episode_with_scene(tmp_path, {field: 123})
        assert not result.valid
        assert any(field in error for error in result.errors)

    def test_validate_episode_drama_accepts_scene_description(self, tmp_path):
        # String visual-adaptation descriptions passed through conversion are valid, including formal-storyboard extra=forbid.
        result = self._drama_episode_with_scene(tmp_path, {"scene_description": "雨夜天台，阿离背身而立"})
        assert result.valid

    def test_validate_episode_drama_rejects_null_source_text(self, tmp_path):
        # Explicit source_text=null fails, distinct from missing keys that default empty;
        # match shared source_text: str models rejecting null under extra=forbid, avoiding validator/model disagreement.
        result = self._drama_episode_with_scene(tmp_path, {"source_text": None})
        assert not result.valid
        assert any("source_text" in error for error in result.errors)

    def test_validate_helpers_on_missing_files(self, tmp_path):
        result = validate_project("missing", projects_dir=str(tmp_path / "projects"))
        assert not result.valid
        assert any("无法加载 project.json" in error for error in result.errors)

    # New tests.

    def test_project_json_validates_scenes_and_props(self, tmp_path):
        """New schema: scenes + props dictionaries both pass validation."""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(
            project_dir / "project.json",
            {
                "title": "Test",
                "content_mode": "narration",
                "generation_mode": "storyboard",
                "style": "Anime",
                "characters": {},
                "scenes": {
                    "书房": {"description": "昏暗的古代书房"},
                    "庭院": {"description": "月下庭院"},
                },
                "props": {
                    "长剑": {"description": "寒光闪闪的长剑"},
                },
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")
        assert result.valid
        assert result.errors == []

    def test_project_json_rejects_legacy_clues(self, tmp_path):
        """Top-level clues reports a deprecation error."""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(
            project_dir / "project.json",
            {
                "title": "Test",
                "content_mode": "narration",
                "style": "Anime",
                "characters": {},
                "clues": {"玉佩": {"type": "prop", "description": "xxx", "importance": "major"}},
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")
        assert not result.valid
        assert any("已废弃字段 clues" in error for error in result.errors)

    def test_validate_episode_drama_invalid_scene_prop_refs(self, tmp_path):
        """Drama references to undefined scenes/props error."""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("drama"))
        _write_json(
            project_dir / "scripts" / "episode_3.json",
            {
                "episode": 3,
                "title": "第三集",
                "content_mode": "drama",
                "scenes": [
                    {
                        "scene_id": "E3S01",
                        "duration_seconds": 8,
                        "characters_in_scene": ["姜月茴"],
                        "scenes": ["未知场景"],
                        "props": ["未知道具"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_3.json")
        assert not result.valid
        assert any("不存在于 project.json 的场景" in error for error in result.errors)
        assert any("不存在于 project.json 的道具" in error for error in result.errors)

    def test_validate_episode_malformed_segment_ref_reports_error_not_typeerror(self, tmp_path):
        """Unhashable dict-like elements in segments[*].scenes, on ``_validate_segment_refs`` comparison paths,
        must fail validation instead of crashing set operations. External episode JSON corruption requires reported errors,
        not exceptions escaping validate_episode."""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": 5,
                        "novel_text": "text",
                        "characters_in_segment": ["姜月茴"],
                        "scenes": [{"malformed": True}],
                        "props": [],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")
        assert not result.valid
        assert any("不存在于 project.json 的场景" in error for error in result.errors)

    def test_legacy_scene_type_field_does_not_block_export(self, tmp_path):
        """Legacy scene_type values such as 对话/动作/过渡 must not block exports.

        scene_type is retired and no longer validated.
        """
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("drama"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "x",
                "content_mode": "drama",
                "scenes": [
                    {
                        "scene_id": f"E1S{i:02d}",
                        "scene_type": legacy_value,
                        "duration_seconds": 8,
                        "characters_in_scene": ["姜月茴"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                    for i, legacy_value in enumerate(["对话", "动作", "过渡", "剧情", "空镜", "随便写"], start=1)
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")
        assert result.valid, f"导出预检查不应被 scene_type 阻断,errors={result.errors}"


class TestDerivativeReferences:
    """``characters_in_*`` accepts ``角色/衍生`` using the same valid-reference set as body mentions (docs/adr/0072)."""

    @staticmethod
    def _episode(character_reference: str) -> dict:
        return {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 8,
                    "novel_text": "原文",
                    "characters_in_segment": [character_reference],
                    "scenes": ["古宅"],
                    "props": ["玉佩"],
                    "image_prompt": "img",
                    "video_prompt": "vid",
                }
            ],
        }

    def _validate(self, tmp_path, character_reference: str):
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload()
        payload["characters"]["姜月茴"]["derivatives"] = {"劲装": {"description": "换上劲装", "character_sheet": ""}}
        _write_json(project_dir / "project.json", payload)
        _write_json(project_dir / "scripts" / "episode_1.json", self._episode(character_reference))

        return DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

    def test_registered_derivative_accepted(self, tmp_path):
        assert self._validate(tmp_path, "姜月茴/劲装").valid

    def test_unregistered_derivative_rejected(self, tmp_path):
        result = self._validate(tmp_path, "姜月茴/夜行衣")

        assert not result.valid
        assert any("不存在于 project.json 的角色" in error for error in result.errors)


class TestEpisodeLedgerFields:
    """Episode-ledger fields are all optional without position records; present fields follow lib.episode.episode_ledger model shapes."""

    def _validate(self, tmp_path, episode_entry=None, whole_source_files="__absent__"):
        payload = _project_payload()
        if episode_entry is not None:
            payload["episodes"] = [episode_entry]
        if whole_source_files != "__absent__":
            payload["whole_source_files"] = whole_source_files
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        return DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

    def _entry(self, **ledger_fields):
        return {"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json", **ledger_fields}

    def test_legacy_entry_without_ledger_fields_is_valid(self, tmp_path):
        result = self._validate(tmp_path, self._entry())
        assert result.valid, result.errors

    def test_bool_episode_num_rejected(self, tmp_path):
        """True/False subclass int but are invalid episode IDs, colliding with 1/0 keys; reject explicitly."""
        result = self._validate(tmp_path, self._entry(episode=True))
        assert any("episode" in e for e in result.errors)

    def test_full_ledger_entry_is_valid(self, tmp_path):
        result = self._validate(
            tmp_path,
            self._entry(
                source_range={"source_file": "source/novel.txt", "start": 0, "end": 100},
                hook="悬念钩子",
                outline={"story_beats": ["开端", "冲突"], "next_episode_teaser": "下集更精彩"},
                ledger_status="planned",
            ),
            whole_source_files=[{"source_file": "source/novel.txt"}],
        )
        assert result.valid, result.errors

    def test_empty_title_allowed_on_episode_entry(self, tmp_path):
        # Orphan registration creates empty titles; script-sync writers also write "" when scripts lack title.
        entry = self._entry()
        entry["title"] = ""
        result = self._validate(tmp_path, entry)
        assert result.valid, result.errors

    def test_missing_title_still_reported(self, tmp_path):
        entry = self._entry()
        del entry["title"]
        result = self._validate(tmp_path, entry)
        assert any("title" in e for e in result.errors)

    def test_unknown_ledger_status_tolerated(self, tmp_path):
        """Tolerate values outside current statuses as absent status; legacy projects may retain retired states."""
        result = self._validate(tmp_path, self._entry(ledger_status="done"))
        assert result.valid, result.errors

    def test_non_string_ledger_status_rejected(self, tmp_path):
        result = self._validate(tmp_path, self._entry(ledger_status=3))
        assert any("ledger_status" in e for e in result.errors)

    def test_malformed_source_range_rejected(self, tmp_path):
        result = self._validate(
            tmp_path,
            self._entry(source_range={"source_file": "source/novel.txt", "start": 100, "end": 1}),
        )
        assert any("source_range" in e for e in result.errors)

    def test_escaping_source_file_rejected(self, tmp_path):
        # Consumers read sources by source_file paths; reject out-of-bounds .. values in validation.
        result = self._validate(
            tmp_path,
            self._entry(source_range={"source_file": "../outside.txt", "start": 0, "end": 1}),
        )
        assert any("source_range" in e for e in result.errors)

    @pytest.mark.parametrize(
        "item",
        [
            {"source_file": "/etc/passwd"},
            {"source_file": "source/nested/a.txt"},
            {"source_file": "source/episode_1.txt"},
            {"source_file": "source/cover.png"},
            "source/novel.txt",
        ],
    )
    def test_invalid_whole_source_file_rejected(self, tmp_path, item):
        result = self._validate(tmp_path, whole_source_files=[item])
        assert any("whole_source_files[0]" in e for e in result.errors)

    def test_whole_source_files_must_be_an_array(self, tmp_path):
        result = self._validate(tmp_path, whole_source_files={"source_file": "source/novel.txt"})
        assert any("whole_source_files" in e for e in result.errors)

    def test_unknown_source_origin_rejected(self, tmp_path):
        result = self._validate(tmp_path, self._entry(source_origin="pasted"))
        assert any("source_origin" in e for e in result.errors)

    @pytest.mark.parametrize("origin", ["own", "none"])
    def test_source_range_requires_a_cut_episode(self, tmp_path, origin):
        result = self._validate(
            tmp_path,
            self._entry(source_origin=origin, source_range={"source_file": "source/novel.txt", "start": 0, "end": 1}),
        )
        assert any("source_range" in e for e in result.errors)

    def test_unhashable_source_origin_is_reported_not_raised(self, tmp_path):
        result = self._validate(
            tmp_path,
            self._entry(source_origin=["own"], source_range={"source_file": "source/novel.txt", "start": 0, "end": 1}),
        )
        assert any("source_origin" in e for e in result.errors)

    def test_legacy_status_with_source_range_tolerated(self, tmp_path):
        """Legacy statuses no longer conflict with valid source_range; positions depend only on source_range itself."""
        result = self._validate(
            tmp_path,
            self._entry(
                ledger_status="已废弃的状态",
                source_range={"source_file": "source/novel.txt", "start": 0, "end": 1},
            ),
        )
        assert result.valid, result.errors

    def test_non_string_hook_rejected(self, tmp_path):
        result = self._validate(tmp_path, self._entry(hook=123))
        assert any("hook" in e for e in result.errors)

    def test_malformed_outline_rejected(self, tmp_path):
        result = self._validate(tmp_path, self._entry(outline={"story_beats": "不是列表"}))
        assert any("outline" in e for e in result.errors)

    def test_empty_whole_source_files_is_valid(self, tmp_path):
        result = self._validate(tmp_path, whole_source_files=[])
        assert result.valid, result.errors

    def test_tree_validation_allows_missing_script_for_ledgered_entry(self, tmp_path):
        """Ledger script_file is a forward-looking contract; ungenerated scripts do not count as tree-validation errors."""
        payload = _project_payload()
        payload["episodes"] = [
            {
                "episode": 1,
                "title": "",
                "script_file": "scripts/episode_1.json",
                "ledger_status": "planned",
                "source_range": {"source_file": "source/novel.txt", "start": 0, "end": 5},
            }
        ]
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert not any("script_file" in e for e in result.errors), result.errors

    def test_tree_validation_allows_missing_script_for_entry_without_ledger_status(self, tmp_path):
        """v2→v3 no longer backfills ledger_status, so upgraded entries may never have it;
        valid shapes with parseable episode IDs are normal ledger entries, even before script generation."""
        payload = _project_payload()
        payload["episodes"] = [{"episode": 1, "title": "x", "script_file": "scripts/episode_1.json"}]
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert not any("script_file" in e for e in result.errors), result.errors

    def test_tree_validation_missing_script_still_blocks_malformed_entry(self, tmp_path):
        """Unparseable episode IDs are not valid ledger entries; their script_file must actually exist."""
        payload = _project_payload()
        payload["episodes"] = [{"title": "x", "script_file": "scripts/episode_1.json"}]
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert any("episodes[0].script_file" in e for e in result.errors)

    @pytest.mark.parametrize("bad_episode_num", [0, -1])
    def test_tree_validation_missing_script_still_blocks_non_positive_episode_num(self, tmp_path, bad_episode_num):
        """parse_episode_num can parse 0/negative IDs, but they are invalid; script_file must still exist."""
        payload = _project_payload()
        payload["episodes"] = [{"episode": bad_episode_num, "title": "x", "script_file": "scripts/episode_1.json"}]
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert any("episodes[0].script_file" in e for e in result.errors)

    def test_tree_validation_traversal_still_rejected_for_ledgered_entry(self, tmp_path):
        """missing_ok exempts missing files only; out-of-bounds ledger paths still reject."""
        payload = _project_payload()
        payload["episodes"] = [
            {
                "episode": 1,
                "title": "",
                "script_file": "../outside.json",
                "ledger_status": "planned",
            }
        ]
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert any("越界" in e for e in result.errors)

    def test_tree_validation_reference_audio_missing_field_is_valid(self, tmp_path):
        """reference_audio is optional; absent/empty character fields must not error."""
        payload = _project_payload()
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert not any("reference_audio" in e for e in result.errors)

    def test_tree_validation_reference_audio_accepts_existing_file(self, tmp_path):
        payload = _project_payload()
        payload["characters"]["姜月茴"]["reference_audio"] = "characters/refs_audio/姜月茴.wav"
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        audio_path = tmp_path / "projects" / "demo" / "characters" / "refs_audio" / "姜月茴.wav"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake-wav-bytes")

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert not any("reference_audio" in e for e in result.errors)

    def test_tree_validation_reference_audio_missing_file_rejected(self, tmp_path):
        payload = _project_payload()
        payload["characters"]["姜月茴"]["reference_audio"] = "characters/refs_audio/姜月茴.wav"
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert any("reference_audio" in e for e in result.errors)

    def test_tree_validation_reference_audio_rejects_path_traversal(self, tmp_path):
        payload = _project_payload()
        payload["characters"]["姜月茴"]["reference_audio"] = "../outside.wav"
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert any("reference_audio" in e and "越界" in e for e in result.errors)


def _ad_project_payload(**overrides) -> dict:
    payload = {
        "title": "速干杯带货",
        "content_mode": "ad",
        "generation_mode": "storyboard",
        "style": "Realistic",
        "target_duration": 60,
        "brief": "突出 3 秒速干卖点",
        "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
        "characters": {"主播": {"description": "出镜模特"}},
        "scenes": {"客厅": {"description": "现代客厅"}},
        "props": {"速干杯": {"description": "主推商品"}},
    }
    payload.update(overrides)
    return payload


class TestAdProjectValidation:
    """Ad/short-film project.json validation: target_duration/brief and always-single-episode constraints."""

    def _validate(self, tmp_path, payload: dict):
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        return DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

    def test_valid_ad_project_passes(self, tmp_path):
        result = self._validate(tmp_path, _ad_project_payload())
        assert result.valid, result.errors

    def test_ad_accepts_arbitrary_positive_target_duration(self, tmp_path):
        # UI offers four tiers; data accepts any positive integer seconds.
        result = self._validate(tmp_path, _ad_project_payload(target_duration=47))
        assert result.valid, result.errors

    def test_ad_missing_target_duration_rejected(self, tmp_path):
        payload = _ad_project_payload()
        del payload["target_duration"]
        result = self._validate(tmp_path, payload)
        assert not result.valid
        assert any("target_duration" in e for e in result.errors)

    def test_ad_non_positive_target_duration_rejected(self, tmp_path):
        for bad in (0, -5, "60", True):
            result = self._validate(tmp_path, _ad_project_payload(target_duration=bad))
            assert not result.valid, f"target_duration={bad!r} 应被拒绝"
            assert any("target_duration" in e for e in result.errors)

    def test_ad_non_string_brief_rejected(self, tmp_path):
        result = self._validate(tmp_path, _ad_project_payload(brief=123))
        assert not result.valid
        assert any("brief" in e for e in result.errors)

    def test_ad_with_default_duration_rejected(self, tmp_path):
        result = self._validate(tmp_path, _ad_project_payload(default_duration=8))
        assert not result.valid
        assert any("default_duration" in e for e in result.errors)

    def test_ad_with_episode_target_duration_rejected(self, tmp_path):
        result = self._validate(tmp_path, _ad_project_payload(episode_target_duration=120))
        assert not result.valid
        assert any("episode_target_duration" in e for e in result.errors)

    def test_ad_episodes_must_be_single_episode_one(self, tmp_path):
        multi = _ad_project_payload(
            episodes=[
                {"episode": 1, "title": "", "script_file": "scripts/episode_1.json"},
                {"episode": 2, "title": "", "script_file": "scripts/episode_2.json"},
            ]
        )
        result = self._validate(tmp_path, multi)
        assert not result.valid
        assert any("episodes" in e for e in result.errors)

        wrong_num = _ad_project_payload(episodes=[{"episode": 2, "title": "", "script_file": "scripts/episode_2.json"}])
        result = self._validate(tmp_path, wrong_num)
        assert not result.valid

        empty = _ad_project_payload(episodes=[])
        result = self._validate(tmp_path, empty)
        assert not result.valid

    def test_target_duration_and_brief_rejected_outside_ad(self, tmp_path):
        payload = _project_payload("narration")
        payload["target_duration"] = 60
        result = self._validate(tmp_path, payload)
        assert not result.valid
        assert any("target_duration" in e for e in result.errors)

        payload = _project_payload("drama")
        payload["brief"] = "x"
        result = self._validate(tmp_path, payload)
        assert not result.valid
        assert any("brief" in e for e in result.errors)

    def test_narration_and_drama_payloads_unaffected(self, tmp_path):
        for mode in ("narration", "drama"):
            result = self._validate(tmp_path, _project_payload(mode))
            assert result.valid, result.errors

    def test_ad_grid_storyboard_rejected(self, tmp_path):
        result = self._validate(tmp_path, _ad_project_payload(grid_storyboard=True))
        assert not result.valid
        assert any("grid_storyboard" in e for e in result.errors)

    def test_ad_grid_storyboard_false_passes(self, tmp_path):
        result = self._validate(tmp_path, _ad_project_payload(grid_storyboard=False))
        assert result.valid, result.errors


class TestGenerationModeValidation:
    """Required binary generation_mode and grid_storyboard type validation."""

    def _validate(self, tmp_path, payload: dict):
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        return DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

    @pytest.mark.parametrize("mode", ["storyboard", "reference_video"])
    def test_binary_route_values_pass(self, tmp_path, mode):
        payload = _project_payload()
        payload["generation_mode"] = mode
        result = self._validate(tmp_path, payload)
        assert result.valid, result.errors

    def test_missing_generation_mode_rejected(self, tmp_path):
        payload = _project_payload()
        del payload["generation_mode"]
        result = self._validate(tmp_path, payload)
        assert not result.valid
        assert any("缺少必填字段：generation_mode" in e for e in result.errors)

    @pytest.mark.parametrize("mode", ["grid", "single", "bogus"])
    def test_non_binary_route_rejected(self, tmp_path, mode):
        payload = _project_payload()
        payload["generation_mode"] = mode
        result = self._validate(tmp_path, payload)
        assert not result.valid
        assert any("generation_mode 值无效" in e for e in result.errors)

    @pytest.mark.parametrize("mode", [{"value": "storyboard"}, ["storyboard"], 5])
    def test_non_scalar_route_reported_as_error(self, tmp_path, mode):
        """Invalid nonscalars yield validation errors, not exceptions; archive imports return 400 instead of 500."""
        payload = _project_payload()
        payload["generation_mode"] = mode
        result = self._validate(tmp_path, payload)
        assert not result.valid
        assert any("generation_mode 值无效" in e for e in result.errors)

    def test_grid_storyboard_true_passes_on_storyboard_route(self, tmp_path):
        payload = _project_payload()
        payload["grid_storyboard"] = True
        result = self._validate(tmp_path, payload)
        assert result.valid, result.errors

    def test_grid_storyboard_non_bool_rejected(self, tmp_path):
        payload = _project_payload()
        payload["grid_storyboard"] = "yes"
        result = self._validate(tmp_path, payload)
        assert not result.valid
        assert any("grid_storyboard" in e for e in result.errors)


class TestAdEpisodeValidation:
    """Ad/short-film flat shots[] structure and reference-integrity validation."""

    def _ad_shot(self, **overrides) -> dict:
        shot = {
            "shot_id": "E1S01",
            "section": "hook",
            "duration_seconds": 3,
            "voiceover_text": "三秒速干，告别水渍",
            "characters_in_shot": ["主播"],
            "scenes": ["客厅"],
            "props": [],
            "products_in_shot": [],
            "image_prompt": "img",
            "video_prompt": "vid",
        }
        shot.update(overrides)
        return shot

    def _validate(self, tmp_path, shots: list[dict], project: dict | None = None):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", project or _ad_project_payload())
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {"episode": 1, "title": "速干杯带货", "content_mode": "ad", "shots": shots},
        )
        return DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

    def test_valid_ad_script_passes(self, tmp_path):
        result = self._validate(tmp_path, [self._ad_shot()])
        assert result.valid, result.errors

    def test_empty_shots_are_a_legal_empty_script(self, tmp_path):
        result = self._validate(tmp_path, [])
        assert result.valid, result.errors

    def test_bad_shot_id_rejected(self, tmp_path):
        result = self._validate(tmp_path, [self._ad_shot(shot_id="S01")])
        assert not result.valid
        assert any("shot_id" in e for e in result.errors)

    def test_missing_voiceover_text_rejected(self, tmp_path):
        shot = self._ad_shot()
        del shot["voiceover_text"]
        result = self._validate(tmp_path, [shot])
        assert not result.valid
        assert any("voiceover_text" in e for e in result.errors)

    def test_non_string_voiceover_text_rejected(self, tmp_path):
        result = self._validate(tmp_path, [self._ad_shot(voiceover_text=42)])
        assert not result.valid
        assert any("voiceover_text" in e for e in result.errors)

    def test_unknown_character_reference_rejected(self, tmp_path):
        result = self._validate(tmp_path, [self._ad_shot(characters_in_shot=["不存在的人"])])
        assert not result.valid
        assert any("characters_in_shot" in e for e in result.errors)

    def test_unknown_product_reference_rejected(self, tmp_path):
        result = self._validate(tmp_path, [self._ad_shot(products_in_shot=["不存在的商品"])])
        assert not result.valid
        assert any("products_in_shot" in e for e in result.errors)

    def test_shot_product_reference_accepts_nfc_nfd_mismatch_on_storyboard_path(self, tmp_path):
        """products_in_shot normalizes like storyboard-generation inputs resolved through reference catalogs;
        valid NFC/NFD-different names must pass, or validation would be stricter than actual assembly
        and block products generation can resolve."""
        import unicodedata

        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        assert name_nfc != name_nfd

        project = _ad_project_payload(products={name_nfd: {"description": "主推商品"}})
        result = self._validate(tmp_path, [self._ad_shot(products_in_shot=[name_nfc])], project=project)
        assert result.valid, result.errors

    def test_shot_reference_accepts_nfc_nfd_mismatch_on_storyboard_path(self, tmp_path):
        """Storyboard references also compare normalized NFC; reference assembly through
        lib.artifacts.generation_input/catalogs indexes normalized values, so raw validation
        would reject legitimate resolvable names."""
        import unicodedata

        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        assert name_nfc != name_nfd

        project = _ad_project_payload(characters={name_nfd: {"description": "出镜模特"}})
        result = self._validate(tmp_path, [self._ad_shot(characters_in_shot=[name_nfc])], project=project)
        assert result.valid, result.errors

    def test_missing_duration_warns_with_default(self, tmp_path):
        shot = self._ad_shot()
        del shot["duration_seconds"]
        result = self._validate(tmp_path, [shot])
        assert result.valid, result.errors
        assert any("duration_seconds" in w for w in result.warnings)

    def test_storyboard_path_accepts_duration_above_reference_cap(self, tmp_path):
        """Storyboard membership is enforced in generation schemas through supported_durations enums;
        this validator checks positive integers only, without rejecting 16 seconds by reference-mode bounds."""
        result = self._validate(tmp_path, [self._ad_shot(duration_seconds=16)])
        assert result.valid, result.errors

    def test_total_duration_drift_warns_but_passes(self, tmp_path):
        """Total duration differing from target_duration beyond tolerance warns only, without blocking."""
        project = _ad_project_payload(target_duration=60)
        result = self._validate(tmp_path, [self._ad_shot(duration_seconds=3)], project=project)
        assert result.valid, result.errors
        assert any("target_duration" in w for w in result.warnings)

    def test_total_duration_close_to_target_no_warning(self, tmp_path):
        project = _ad_project_payload(target_duration=12)
        shots = [
            self._ad_shot(shot_id="E1S01", duration_seconds=3),
            self._ad_shot(shot_id="E1S02", duration_seconds=4),
            self._ad_shot(shot_id="E1S03", duration_seconds=4),
        ]
        result = self._validate(tmp_path, shots, project=project)
        assert result.valid, result.errors
        assert not any("target_duration" in w for w in result.warnings)


class TestAdEpisodeValidationEdgeCases:
    """Corrupt-data tolerance for ad storyboard scripts."""

    def test_non_string_shot_id_reported_not_crash(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _ad_project_payload())
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "T",
                "content_mode": "ad",
                "shots": [{"shot_id": 101, "voiceover_text": "x", "image_prompt": "i", "video_prompt": "v"}],
            },
        )
        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")
        assert not result.valid
        assert any("shot_id" in e for e in result.errors)

    def test_products_bucket_not_dict_reported_not_crash(self, tmp_path):
        project = _ad_project_payload()
        project["products"] = ["速干杯"]
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", project)
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "T",
                "content_mode": "ad",
                "shots": [
                    {
                        "shot_id": "E1S01",
                        "voiceover_text": "x",
                        "products_in_shot": ["速干杯"],
                        "image_prompt": "i",
                        "video_prompt": "v",
                    }
                ],
            },
        )
        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")
        assert not result.valid
        assert any("products_in_shot" in e for e in result.errors)

    def test_ad_missing_episodes_key_rejected(self, tmp_path):
        payload = _ad_project_payload()
        del payload["episodes"]
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")
        assert not result.valid
        assert any("恒为第 1 集单条" in e for e in result.errors)


class TestAdReferenceVideoUnitsValidation:
    """Ad reference-to-video shares self-contained video_units validation with other content types."""

    @staticmethod
    def _unit(**overrides) -> dict:
        unit = {
            "unit_id": "E1U1",
            "text": "@[主播] 展示 @[速干杯]",
            "duration_seconds": 7,
            "generated_assets": {"status": "pending"},
        }
        unit.update(overrides)
        return unit

    def _validate(self, tmp_path, units) -> object:
        project_dir = tmp_path / "projects" / "demo"
        project = _ad_project_payload(
            generation_mode="reference_video",
            products={"速干杯": {"description": "主推商品"}},
        )
        _write_json(project_dir / "project.json", project)
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {"episode": 1, "title": "速干杯带货", "content_mode": "ad", "video_units": units},
        )
        return DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

    def test_valid_self_contained_unit_passes(self, tmp_path):
        result = self._validate(tmp_path, [self._unit()])
        assert result.valid, result.errors

    def test_replan_shell_allows_empty_text_and_zero_duration(self, tmp_path):
        result = self._validate(tmp_path, [self._unit(text="", duration_seconds=0, needs_replan=True)])
        assert result.valid, result.errors

    def test_normal_unit_rejects_empty_text_and_zero_duration(self, tmp_path):
        result = self._validate(tmp_path, [self._unit(text="   ", duration_seconds=0, needs_replan=False)])
        assert not result.valid
        assert any("text" in error for error in result.errors)
        assert any("duration_seconds" in error for error in result.errors)

    def test_text_must_be_a_string(self, tmp_path):
        result = self._validate(tmp_path, [self._unit(text=["镜头1"])])
        assert not result.valid
        assert any("text" in error for error in result.errors)

    def test_needs_replan_must_be_boolean(self, tmp_path):
        result = self._validate(tmp_path, [self._unit(needs_replan="yes")])
        assert not result.valid
        assert any("needs_replan" in error for error in result.errors)

    def test_unregistered_mention_is_not_a_structural_error(self, tmp_path):
        """References derive at execution; unregistered body `@[名称]` only warns during rendering, without structural error."""
        result = self._validate(tmp_path, [self._unit(text="@[不存在的资产] 出现在画面里")])
        assert result.valid, result.errors

    def test_unparseable_video_generated_at_rejected(self, tmp_path):
        unit = self._unit(generated_assets={"status": "completed", "video_generated_at": "not-a-date"})
        result = self._validate(tmp_path, [unit])
        assert not result.valid
        assert any("video_generated_at 不是合法的 ISO8601 时间戳" in error for error in result.errors)

    def test_malformed_entry_rejected(self, tmp_path):
        result = self._validate(tmp_path, ["not-a-dict", {"unit_id": "E1U2"}])
        assert not result.valid
        assert any("video_units[0]" in error for error in result.errors)
        assert any("text" in error for error in result.errors)


class TestSourceKindValidation:
    """Source types persist per source file; absent defaults to novel, invalid values reject; also guard generic-speaker regressions."""

    def _validate(self, tmp_path, project):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", project)
        return DataValidator(projects_dir=str(tmp_path / "projects")).validate_project("demo")

    def _payload(self, file_kind: object = None, episode_kind: object = None) -> dict:
        payload = _project_payload("drama")
        payload["whole_source_files"] = [
            {"source_file": "source/novel.txt", **({} if file_kind is None else {"source_kind": file_kind})}
        ]
        payload["episodes"] = [
            {
                "episode": 1,
                "title": "第一集",
                "script_file": "scripts/episode_1.json",
                "source_origin": "own",
                **({} if episode_kind is None else {"source_kind": episode_kind}),
            }
        ]
        return payload

    def test_missing_source_kind_is_valid(self, tmp_path):
        result = self._validate(tmp_path, self._payload())
        assert result.valid, result.errors

    @pytest.mark.parametrize("kind", ["novel", "screenplay"])
    def test_valid_source_kind_passes(self, tmp_path, kind):
        result = self._validate(tmp_path, self._payload(kind, kind))
        assert result.valid, result.errors

    @pytest.mark.parametrize(("file_kind", "episode_kind"), [("screen_play", None), (None, "screen_play")])
    def test_invalid_source_kind_rejected(self, tmp_path, file_kind, episode_kind):
        result = self._validate(tmp_path, self._payload(file_kind, episode_kind))
        assert not result.valid
        assert any("source_kind" in e for e in result.errors)

    def test_generic_speaker_in_drama_dialogue_passes_validation(self, tmp_path):
        """Generic unregistered speakers in dialogue, outside characters_in_scene, pass validation.

        Only characters_in_scene membership requires registration, not dialogue.speaker;
        screenplay extras such as speaker=老人甲 must pass without forced registration.
        """
        project_dir = tmp_path / "projects" / "demo"
        payload = self._payload("screenplay")
        _write_json(project_dir / "project.json", payload)
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "drama",
                "scenes": [
                    {
                        "scene_id": "E1S01",
                        "duration_seconds": 8,
                        "characters_in_scene": ["姜月茴"],
                        "scenes": ["古宅"],
                        "props": ["玉佩"],
                        "image_prompt": "img",
                        "video_prompt": {
                            "action": "转身",
                            "camera_motion": "Static",
                            "ambiance_audio": "风声",
                            # Generic extras outside characters remain valid speakers.
                            "dialogue": [{"speaker": "老人甲", "line": "天黑了，快回家。"}],
                        },
                        "voiceover": ["多年以后，她仍记得那个夜晚。"],
                    }
                ],
            },
        )
        result = validate_episode("demo", "episode_1.json", projects_dir=str(tmp_path / "projects"))
        assert result.valid, result.errors


def _episode_for_kind(kind: str, items: object) -> tuple[dict, dict]:
    """Construct (project, episode) by skeleton kind, setting the episode item-array key to supplied, possibly invalid, items."""
    array_key, content_mode, gen_mode = {
        "segments": ("segments", "narration", None),
        "scenes": ("scenes", "drama", None),
        "shots": ("shots", "ad", None),
        "video_units": ("video_units", "narration", "reference_video"),
    }[kind]
    project = _ad_project_payload() if content_mode == "ad" else _project_payload(content_mode)
    episode: dict = {"episode": 1, "title": "第一集", "content_mode": content_mode, array_key: items}
    if gen_mode:
        project["generation_mode"] = gen_mode
        episode["generation_mode"] = gen_mode
    return project, episode


class TestSkeletonEntryTypeGuards:
    """All four skeleton loops report errors/valid=False for nondict items or nonlist skeleton fields, without raising."""

    def _validate(self, tmp_path, project: dict, episode: dict):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", project)
        _write_json(project_dir / "scripts" / "episode_1.json", episode)
        return DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

    @pytest.mark.parametrize(
        ("kind", "array_key"),
        [
            ("segments", "segments"),
            ("scenes", "scenes"),
            ("shots", "shots"),
            ("video_units", "video_units"),
        ],
    )
    def test_non_dict_entry_reported_not_crash(self, tmp_path, kind, array_key):
        # Nondict string items report item-type errors and continue with valid=False, without raising.
        project, episode = _episode_for_kind(kind, ["不是对象", {}])
        result = self._validate(tmp_path, project, episode)
        assert not result.valid
        assert any(f"{array_key}[0]" in error for error in result.errors), result.errors

    @pytest.mark.parametrize(
        ("kind", "array_key"),
        [
            ("segments", "segments"),
            ("scenes", "scenes"),
            ("shots", "shots"),
            ("video_units", "video_units"),
        ],
    )
    def test_non_list_skeleton_field_reported_not_crash(self, tmp_path, kind, array_key):
        # A dict instead of the array field reports must-be-array, valid=False, without raising.
        project, episode = _episode_for_kind(kind, {"E1S01": {}})
        result = self._validate(tmp_path, project, episode)
        assert not result.valid
        assert any(array_key in error and "数组" in error for error in result.errors), result.errors


class TestRouteSkeletonMismatchValidation:
    """Mismatched legacy scripts with historical per-episode generation overrides report structural conclusions/resplitting guidance, not per-field absence."""

    def test_unit_script_under_storyboard_route_is_rejected(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload())
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {"episode": 1, "title": "第一集", "content_mode": "narration", "video_units": []},
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not result.valid
        assert any("骨架" in error and "重新拆分" in error for error in result.errors), result.errors
        # Report only generation-mode mismatch, without downstream missing-segments noise.
        assert len(result.errors) == 1, result.errors

    def test_storyboard_script_under_reference_route_is_rejected(self, tmp_path):
        payload = _project_payload()
        payload["generation_mode"] = "reference_video"
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", payload)
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": []},
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not result.valid
        assert any("generate_script_plan" in error for error in result.errors), result.errors

    def test_reference_route_script_with_residual_segments_is_not_a_mismatch(self, tmp_path):
        """Leftover storyboard arrays in reference-to-video are not mismatches; present video_units validates as units without blocking import."""
        payload = _project_payload()
        payload["generation_mode"] = "reference_video"
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", payload)
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "video_units": [],
                "segments": [{"segment_id": "E1S1"}],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not any("骨架" in error for error in result.errors), result.errors

    def test_residual_route_stamp_is_ignored(self, tmp_path):
        """Legacy generation_mode stamps are unknown fields, neither affecting classification nor failing validation."""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload())
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "segments": [],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        # Still validate segments (empty arrays have separate errors), without treating leftover stamps as mismatches.
        assert not any("骨架" in error for error in result.errors), result.errors


class TestInvalidContentModeEpisodeValidation:
    """Present invalid legacy/corrupt content_mode makes resolve_declared_kind fail loudly with ValueError,
    but episode validation must report corruption as structured errors without propagating exceptions."""

    def test_validate_episode_reports_structured_error_not_crash(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("bogus_legacy"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {"episode": 1, "title": "第一集", "segments": []},
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not result.valid
        assert any("content_mode" in error for error in result.errors), result.errors

    def test_validate_project_tree_reports_structured_error_not_crash(self, tmp_path):
        payload = _project_payload("bogus_legacy")
        payload["episodes"] = [{"episode": 1, "title": "x", "script_file": "scripts/episode_1.json"}]
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", payload)
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {"episode": 1, "title": "第一集", "segments": []},
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_project_tree(project_dir)

        assert not result.valid
        assert any("content_mode" in error for error in result.errors), result.errors

    def test_episode_level_invalid_content_mode_also_reported(self, tmp_path):
        # Valid project content_mode but invalid overriding episode content_mode
        # likewise yields structured errors, not exceptions.
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {"episode": 1, "title": "第一集", "content_mode": "bogus_legacy", "segments": []},
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not result.valid
        assert any("content_mode" in error for error in result.errors), result.errors

    def test_missing_episode_content_mode_still_falls_back_to_project_value(self, tmp_path):
        # Regression: entirely absent episode content_mode still falls back to project / "narration", without the new
        # error branch introduced here.
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload("narration"))
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "novel_text": "原文",
                        "characters_in_segment": ["姜月茴"],
                        "scenes": ["古宅"],
                        "props": ["玉佩"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                        "duration_seconds": 3,
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert result.valid, result.errors


# Skeleton kind → triggering (content_mode, generation_mode), inverse to resolve_declared_kind.
_KIND_TO_MODES = {
    "segments": ("narration", None),
    "scenes": ("drama", None),
    "shots": ("ad", None),
    "video_units": ("narration", "reference_video"),
}
# Skeleton kind → expected validator method.
_KIND_TO_VALIDATOR = {
    "segments": "_validate_segments",
    "scenes": "_validate_scenes",
    "shots": "_validate_shots",
    "video_units": "_validate_reference_video_script",
}


class TestDataValidatorSkeletonExhaustiveness:
    """Exhaustiveness: _validate_episode_payload skeleton-to-validator dispatch covers every SKELETONS key.

    Adding a fifth skeleton plus canonical mapping makes dispatch else fail loudly, and this test fails per case.
    """

    @pytest.mark.parametrize("kind", list(_KIND_TO_MODES))
    def test_episode_dispatch_covers_every_skeleton_kind(self, kind, tmp_path, monkeypatch):
        from lib.script.script_skeleton import SKELETONS

        # Traverse all SKELETONS keys; fifth skeletons absent from the map below raise KeyError.
        assert set(_KIND_TO_MODES) == set(SKELETONS)
        assert set(_KIND_TO_VALIDATOR) == set(SKELETONS)

        content_mode, gen_mode = _KIND_TO_MODES[kind]
        called: list[str] = []
        spied = (*_KIND_TO_VALIDATOR.values(), "_warn_ad_target_duration_drift")
        for name in spied:
            monkeypatch.setattr(DataValidator, name, lambda *a, _n=name, **k: called.append(_n))

        project = {"content_mode": content_mode, "products": {}}
        # Scripts carry no generation-mode stamps; arrange arrays by kind or generation-mode gates reject mismatches first.
        episode = {"episode": 1, "title": "第一集", "content_mode": content_mode, kind: []}
        if gen_mode:
            project["generation_mode"] = gen_mode

        validator = DataValidator(projects_dir=str(tmp_path / "projects"))
        validator._validate_episode_payload(tmp_path, project, episode, [], [])

        assert _KIND_TO_VALIDATOR[kind] in called

    def test_narration_data_in_scenes_key_is_validated_as_scenes(self, tmp_path, monkeypatch):
        """After gates admit legacy narration data under scenes, validate its actual skeleton.

        Dispatching by declared segments reads a nonexistent array, leaving all scenes unvalidated.
        """
        called: list[str] = []
        for name in _KIND_TO_VALIDATOR.values():
            monkeypatch.setattr(DataValidator, name, lambda *a, _n=name, **k: called.append(_n))

        project = {"content_mode": "narration", "products": {}}
        episode = {"episode": 1, "title": "第一集", "content_mode": "narration", "scenes": []}

        validator = DataValidator(projects_dir=str(tmp_path / "projects"))
        validator._validate_episode_payload(tmp_path, project, episode, [], [])

        assert called == ["_validate_scenes"]


class TestCharacterDerivativesStructure:
    """Derivative-table structure: downstream consumes both fields as text; reject nonstrings before persistence."""

    @staticmethod
    def _keys(derivatives: object) -> list[str]:
        project = _project_payload()
        project["characters"]["姜月茴"]["derivatives"] = derivatives
        result = DataValidator("/tmp").validate_project_payload(project)
        return [message.key for message in result.error_messages]

    def test_well_formed_table_passes(self):
        assert self._keys({"战斗装": {"description": "换上黑色重甲", "character_sheet": ""}}) == []

    def test_empty_and_missing_tables_pass(self):
        assert self._keys({}) == []
        assert DataValidator("/tmp").validate_project_payload(_project_payload()).error_messages == []

    def test_non_object_table_rejected(self):
        assert self._keys("dirty") == ["val_asset_field_must_be_object"]

    def test_non_object_table_reaches_the_asset_definition_subset(self):
        """Derivative structural errors require ``val_asset_`` prefixes, or asset-subset validation would accept malformed shapes."""
        project = _project_payload()
        project["characters"]["姜月茴"]["derivatives"] = "dirty"
        result = DataValidator("/tmp").validate_asset_definitions(project)
        assert not result.valid
        assert [message.key for message in result.error_messages] == ["val_asset_field_must_be_object"]

    def test_non_object_derivative_rejected(self):
        assert self._keys({"战斗装": "dirty"}) == ["val_asset_format_object"]

    @pytest.mark.parametrize("derivative", [{"description": 3}, {"character_sheet": ["a.png"]}])
    def test_non_string_derivative_fields_rejected(self, derivative):
        assert self._keys({"战斗装": derivative}) == ["val_asset_field_must_be_string"]

    def test_derivative_error_names_the_reference_form(self):
        project = _project_payload()
        project["characters"]["姜月茴"]["derivatives"] = {"战斗装": {"description": 3}}
        errors = DataValidator("/tmp").validate_project_payload(project).errors
        assert "姜月茴/战斗装" in errors[0]

    def test_types_without_the_capability_ignore_the_field(self):
        project = _project_payload()
        project["scenes"]["古宅"]["derivatives"] = "dirty"
        assert DataValidator("/tmp").validate_project_payload(project).error_messages == []


class TestAssetDescription:
    """Asset descriptions are generation inputs only; present values require strings, while empty/absent is valid."""

    _BUCKETS = ("characters", "scenes", "props", "products")

    @staticmethod
    def _payload_with(bucket: str, entry: dict) -> dict:
        project = _project_payload()
        project["products"] = {"保温杯": {"description": "不锈钢保温杯"}}
        project[bucket] = {"无描述资产": entry}
        return project

    @pytest.mark.parametrize("bucket", _BUCKETS)
    @pytest.mark.parametrize("entry", [{"description": ""}, {}], ids=["empty", "absent"])
    def test_empty_or_absent_description_passes(self, bucket, entry):
        project = self._payload_with(bucket, entry)

        result = DataValidator("/tmp").validate_asset_definitions(project)

        assert result.valid
        assert result.error_messages == []

    @pytest.mark.parametrize("bucket", _BUCKETS)
    @pytest.mark.parametrize("description", [None, 3, {"text": "x"}, ["x"]])
    def test_non_string_description_rejected(self, bucket, description):
        project = self._payload_with(bucket, {"description": description})

        result = DataValidator("/tmp").validate_asset_definitions(project)

        assert not result.valid
        assert [message.key for message in result.error_messages] == ["val_asset_field_must_be_string"]
        assert "无描述资产" in result.errors[0]
        assert "description" in result.errors[0]


class TestPendingPromptValidation:
    def test_pending_prompts_are_not_reported_as_missing(self, tmp_path):
        """Mechanically persisted image_prompt / video_prompt=None means pending generation, without validation errors."""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", _project_payload())
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": 5,
                        "novel_text": "他推开门。",
                        "characters_in_segment": [],
                        "scenes": [],
                        "props": [],
                        "image_prompt": None,
                        "video_prompt": None,
                    }
                ],
            },
        )

        result = DataValidator(projects_dir=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not any("image_prompt" in error or "video_prompt" in error for error in result.errors)
