import json
from pathlib import Path

import pytest

from lib.data_validator import DataValidator, validate_episode, validate_project


def _write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _project_payload(content_mode: str = "narration") -> dict:
    return {
        "title": "Demo",
        "content_mode": content_mode,
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

        validator = DataValidator(projects_root=str(tmp_path / "projects"))
        result = validator.validate_project("demo")

        assert result.valid
        assert result.errors == []
        assert "验证通过" in str(result)

    def test_validate_project_reports_missing_and_invalid_fields(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        # title 字段完全缺失才报错;空字符串在新策略下属于合法状态(前端 i18n 兜底)
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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        # title 完全缺失 → "缺少必填字段",区别于"字段类型错误"
        assert any("缺少必填字段: title" in error for error in result.errors)
        assert any("content_mode" in error for error in result.errors)
        assert any("角色 'A' 数据格式错误" in error for error in result.errors)
        # scenes/props 缺少 description 也应报错
        assert any("场景 'X'" in error for error in result.errors)
        assert any("道具 'Y'" in error for error in result.errors)

    def test_validate_project_rejects_non_string_title(self, tmp_path):
        # title 字段存在但类型不是 string(如 int / null / list)应给出区分于"缺失"的明确文案,
        # 避免调用方误以为字段没写。
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload()
        payload["title"] = 123
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")

        assert not result.valid
        assert any("字段类型错误: title 应为字符串" in error for error in result.errors)
        assert not any("缺少必填字段: title" in error for error in result.errors)

    def test_validate_project_allows_empty_title(self, tmp_path):
        # title 为空字符串属于合法状态:前端会以「未命名项目」i18n 兜底,
        # lib 层不再要求 title 非空,避免 ProjectManager 写路径被迫存 slug 作 fallback。
        project_dir = tmp_path / "projects" / "demo"
        payload = _project_payload()
        payload["title"] = ""
        _write_json(project_dir / "project.json", payload)

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")

        assert result.valid
        assert not any("title" in error for error in result.errors)

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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert result.valid
        assert any("缺少 duration_seconds" in w for w in result.warnings)

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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        # 引用的音频文件不存在 → 中央校验报错（不再被白名单静默放过）
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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        # 文件存在 → 整条校验链应通过，且不产生 narration_audio 相关错误
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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert result.valid
        # scenes/props 都是 optional，缺少时应有警告
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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

        assert not result.valid
        assert any("episode (整数)" in error for error in result.errors)
        assert any("segment_id 格式错误" in error for error in result.errors)
        # 5 是正整数 → 合法，不应再报 duration_seconds 错误
        assert not any("duration_seconds 值无效" in error for error in result.errors)
        assert any("不存在于 project.json 的角色" in error for error in result.errors)
        assert any("不存在于 project.json 的场景" in error for error in result.errors)
        assert any("不存在于 project.json 的道具" in error for error in result.errors)

    @pytest.mark.parametrize("bad_duration", [0, -1, "5", 4.5, True])
    def test_validate_episode_rejects_non_positive_integer_duration(self, tmp_path, bad_duration):
        """非正整数的 duration_seconds 仍应报错（0 / 负数 / 字符串 / 浮点 / bool）。"""
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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

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

        result = validate_episode("demo", "episode_2.json", projects_root=str(tmp_path / "projects"))
        assert result.valid

    def test_validate_helpers_on_missing_files(self, tmp_path):
        result = validate_project("missing", projects_root=str(tmp_path / "projects"))
        assert not result.valid
        assert any("无法加载 project.json" in error for error in result.errors)

    # ── 新增测试 ──────────────────────────────────────────────

    def test_project_json_validates_scenes_and_props(self, tmp_path):
        """新 schema：scenes + props 两个字典都通过校验"""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(
            project_dir / "project.json",
            {
                "title": "Test",
                "content_mode": "narration",
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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")
        assert result.valid
        assert result.errors == []

    def test_project_json_rejects_legacy_clues(self, tmp_path):
        """顶层 clues 字段应报废弃错误"""
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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")
        assert not result.valid
        assert any("已废弃字段 clues" in error for error in result.errors)

    def test_validate_scenes_dict_missing_description(self, tmp_path):
        """scenes 字典中某个场景缺少 description 应报错"""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(
            project_dir / "project.json",
            {
                "title": "Test",
                "content_mode": "narration",
                "style": "Anime",
                "characters": {},
                "scenes": {
                    "书房": {"description": ""},  # 空字符串视为缺失
                },
                "props": {},
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")
        assert not result.valid
        assert any("场景 '书房'" in error and "description" in error for error in result.errors)

    def test_validate_props_dict_missing_description(self, tmp_path):
        """props 字典中某个道具缺少 description 应报错"""
        project_dir = tmp_path / "projects" / "demo"
        _write_json(
            project_dir / "project.json",
            {
                "title": "Test",
                "content_mode": "narration",
                "style": "Anime",
                "characters": {},
                "scenes": {},
                "props": {
                    "玉佩": {},  # 完全缺少 description 键
                },
            },
        )

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")
        assert not result.valid
        assert any("道具 '玉佩'" in error and "description" in error for error in result.errors)

    def test_validate_episode_drama_invalid_scene_prop_refs(self, tmp_path):
        """drama 模式：引用未定义的 scenes/props 应报错"""
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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_3.json")
        assert not result.valid
        assert any("不存在于 project.json 的场景" in error for error in result.errors)
        assert any("不存在于 project.json 的道具" in error for error in result.errors)

    def test_legacy_scene_type_field_does_not_block_export(self, tmp_path):
        """存量项目里残留 scene_type='对话'/'动作'/'过渡' 等任意值不该阻断导出。

        scene_type 字段已废弃,validator 不再校验。
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

        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")
        assert result.valid, f"导出预检查不应被 scene_type 阻断,errors={result.errors}"


class TestEpisodeLedgerFields:
    """分集账本字段：全部可缺失（旧式条目），存在时按 lib.episode_ledger 模型校验形状。"""

    def _validate(self, tmp_path, episode_entry=None, planning_cursor="__absent__"):
        payload = _project_payload()
        if episode_entry is not None:
            payload["episodes"] = [episode_entry]
        if planning_cursor != "__absent__":
            payload["planning_cursor"] = planning_cursor
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        return DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")

    def _entry(self, **ledger_fields):
        return {"episode": 1, "title": "开端", "script_file": "scripts/episode_1.json", **ledger_fields}

    def test_legacy_entry_without_ledger_fields_is_valid(self, tmp_path):
        result = self._validate(tmp_path, self._entry())
        assert result.valid, result.errors

    def test_full_ledger_entry_is_valid(self, tmp_path):
        result = self._validate(
            tmp_path,
            self._entry(
                source_range={"source_file": "source/novel.txt", "start": 0, "end": 100},
                hook="悬念钩子",
                outline={"story_beats": ["开端", "冲突"], "next_episode_teaser": "下集更精彩"},
                ledger_status="planned",
            ),
            planning_cursor={"source_file": "source/novel.txt", "offset": 100},
        )
        assert result.valid, result.errors

    def test_empty_title_allowed_on_episode_entry(self, tmp_path):
        # 回填新建的孤儿条目 title 为空串；写入方（剧本同步）在剧本缺 title 时也写 ""
        entry = self._entry()
        entry["title"] = ""
        result = self._validate(tmp_path, entry)
        assert result.valid, result.errors

    def test_missing_title_still_reported(self, tmp_path):
        entry = self._entry()
        del entry["title"]
        result = self._validate(tmp_path, entry)
        assert any("title" in e for e in result.errors)

    def test_invalid_ledger_status_rejected(self, tmp_path):
        result = self._validate(tmp_path, self._entry(ledger_status="done"))
        assert any("ledger_status" in e for e in result.errors)

    def test_malformed_source_range_rejected(self, tmp_path):
        result = self._validate(
            tmp_path,
            self._entry(source_range={"source_file": "source/novel.txt", "start": 100, "end": 1}),
        )
        assert any("source_range" in e for e in result.errors)

    def test_escaping_source_file_rejected(self, tmp_path):
        # source_file 是消费方按路径读源文的依据，越界值（..）必须在校验层拒绝
        result = self._validate(
            tmp_path,
            self._entry(source_range={"source_file": "../outside.txt", "start": 0, "end": 1}),
        )
        assert any("source_range" in e for e in result.errors)

    def test_absolute_planning_cursor_source_file_rejected(self, tmp_path):
        result = self._validate(tmp_path, planning_cursor={"source_file": "/etc/passwd", "offset": 0})
        assert any("planning_cursor" in e for e in result.errors)

    def test_unanchored_with_source_range_rejected(self, tmp_path):
        result = self._validate(
            tmp_path,
            self._entry(
                ledger_status="unanchored",
                source_range={"source_file": "source/novel.txt", "start": 0, "end": 1},
            ),
        )
        assert any("unanchored" in e for e in result.errors)

    def test_non_string_hook_rejected(self, tmp_path):
        result = self._validate(tmp_path, self._entry(hook=123))
        assert any("hook" in e for e in result.errors)

    def test_malformed_outline_rejected(self, tmp_path):
        result = self._validate(tmp_path, self._entry(outline={"story_beats": "不是列表"}))
        assert any("outline" in e for e in result.errors)

    def test_malformed_planning_cursor_rejected(self, tmp_path):
        result = self._validate(tmp_path, planning_cursor={"offset": -1})
        assert any("planning_cursor" in e for e in result.errors)

    def test_null_planning_cursor_is_valid(self, tmp_path):
        result = self._validate(tmp_path, planning_cursor=None)
        assert result.valid, result.errors

    def test_tree_validation_allows_missing_script_for_ledgered_entry(self, tmp_path):
        """账本条目的 script_file 是前瞻性契约：剧本尚未生成不算 tree 校验错误。"""
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
        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert not any("script_file" in e for e in result.errors), result.errors

    def test_tree_validation_missing_script_still_blocks_legacy_entry(self, tmp_path):
        """旧式条目（无 ledger_status）维持原不变量：script_file 必须实际存在。"""
        payload = _project_payload()
        payload["episodes"] = [{"episode": 1, "title": "x", "script_file": "scripts/episode_1.json"}]
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert any("episodes[0].script_file" in e for e in result.errors)

    def test_tree_validation_traversal_still_rejected_for_ledgered_entry(self, tmp_path):
        """missing_ok 只豁免「文件不存在」，路径越界对账本条目照常拒绝。"""
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
        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project_tree(
            tmp_path / "projects" / "demo"
        )
        assert any("越界" in e for e in result.errors)


def _ad_project_payload(**overrides) -> dict:
    payload = {
        "title": "速干杯带货",
        "content_mode": "ad",
        "style": "Realistic",
        "target_duration": 60,
        "brief": "突出 3 秒速干卖点",
        "episodes": [{"episode": 1, "title": "", "script_file": "scripts/episode_1.json"}],
        "characters": {"主播": {"description": "出镜模特"}},
        "scenes": {"客厅": {"description": "现代客厅"}},
        "props": {"速干杯": {"description": "主推产品"}},
    }
    payload.update(overrides)
    return payload


class TestAdProjectValidation:
    """广告/短片项目的 project.json 校验：target_duration/brief 字段与恒单集约束。"""

    def _validate(self, tmp_path, payload: dict):
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        return DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")

    def test_valid_ad_project_passes(self, tmp_path):
        result = self._validate(tmp_path, _ad_project_payload())
        assert result.valid, result.errors

    def test_ad_accepts_arbitrary_positive_target_duration(self, tmp_path):
        # UI 只给四档，但数据层接受任意正整数秒
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


class TestAdEpisodeValidation:
    """广告/短片剧本（平铺 shots[]）的结构与引用完整性校验。"""

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
        return DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")

    def test_valid_ad_script_passes(self, tmp_path):
        result = self._validate(tmp_path, [self._ad_shot()])
        assert result.valid, result.errors

    def test_empty_shots_rejected(self, tmp_path):
        result = self._validate(tmp_path, [])
        assert not result.valid
        assert any("shots" in e for e in result.errors)

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
        result = self._validate(tmp_path, [self._ad_shot(products_in_shot=["不存在的产品"])])
        assert not result.valid
        assert any("products_in_shot" in e for e in result.errors)

    def test_missing_duration_warns_with_default(self, tmp_path):
        shot = self._ad_shot()
        del shot["duration_seconds"]
        result = self._validate(tmp_path, [shot])
        assert result.valid, result.errors
        assert any("duration_seconds" in w for w in result.warnings)

    def test_storyboard_path_accepts_duration_above_reference_cap(self, tmp_path):
        """storyboard 路径的成员校验在生成 schema 层（supported_durations 枚举）；
        校验器只把关正整数，16 秒不按 reference 区间拒。"""
        result = self._validate(tmp_path, [self._ad_shot(duration_seconds=16)])
        assert result.valid, result.errors

    def test_reference_path_rejects_duration_out_of_range(self, tmp_path):
        """ad + reference_video：镜头时长必须是 1-15 自由整数。"""
        project = _ad_project_payload(generation_mode="reference_video")
        result = self._validate(tmp_path, [self._ad_shot(duration_seconds=16)], project=project)
        assert not result.valid
        assert any("1-15" in e for e in result.errors)

    def test_reference_path_accepts_free_integers_in_range(self, tmp_path):
        project = _ad_project_payload(generation_mode="reference_video")
        result = self._validate(
            tmp_path,
            [self._ad_shot(duration_seconds=7), self._ad_shot(shot_id="E1S02", duration_seconds=15)],
            project=project,
        )
        assert result.valid, result.errors

    def test_total_duration_drift_warns_but_passes(self, tmp_path):
        """剧本总时长与 target_duration 偏差超阈值仅 warn，不阻塞。"""
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
    """ad 剧本骨架唯一与脏数据容错。"""

    def test_ad_reference_generation_mode_still_validates_shots(self, tmp_path):
        """ad 剧本不随生成路径换骨架：generation_mode=reference_video 仍按 shots 校验。"""
        project = _ad_project_payload(generation_mode="reference_video")
        project_dir = tmp_path / "projects" / "demo"
        _write_json(project_dir / "project.json", project)
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "速干杯带货",
                "content_mode": "ad",
                "shots": [
                    {
                        "shot_id": "E1S01",
                        "section": "hook",
                        "duration_seconds": 3,
                        "voiceover_text": "口播",
                        "characters_in_shot": [],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )
        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")
        assert result.valid, result.errors

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
        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")
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
        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_episode("demo", "episode_1.json")
        assert not result.valid
        assert any("products_in_shot" in e for e in result.errors)

    def test_ad_missing_episodes_key_rejected(self, tmp_path):
        payload = _ad_project_payload()
        del payload["episodes"]
        _write_json(tmp_path / "projects" / "demo" / "project.json", payload)
        result = DataValidator(projects_root=str(tmp_path / "projects")).validate_project("demo")
        assert not result.valid
        assert any("恒为第 1 集单条" in e for e in result.errors)
