"""写盘统一入口「不更坏」结构校验守卫测试。

只断言外部行为：构造 before/after 剧本，断言写盘是否 raise ScriptStructureValidationError，
以及资产回写豁免、validate 默认值，不 patch 私有方法。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from lib.script_structure_validator import ScriptStructureValidationError


def _segment(segment_id: str = "E1S01", duration: int = 4) -> dict:
    return {
        "segment_id": segment_id,
        "duration_seconds": duration,
        "novel_text": "原文",
        "characters_in_segment": ["角色A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _valid_script(segments: list[dict] | None = None) -> dict:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": segments if segments is not None else [_segment()],
    }


def _invalid_script() -> dict:
    # 缺 summary/novel，image_prompt/video_prompt 形状错 —— 结构非法
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "segments": [{"segment_id": "E1S01", "duration_seconds": 4, "image_prompt": "x", "video_prompt": "y"}],
    }


def _pm(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    return pm


class TestNoWorseSemantics:
    def test_valid_to_invalid_is_rejected(self, tmp_path: Path):
        """前合法 ∧ 后非法 → 拒绝（本次编辑引入新结构错误）。"""
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script(), "episode_1.json")

        with pytest.raises(ScriptStructureValidationError):
            with pm.locked_script("demo", "episode_1.json") as script:
                # 把合法 segment 的 duration 改成越界值
                script["segments"][0]["duration_seconds"] = 999

    def test_invalid_to_invalid_is_allowed(self, tmp_path: Path):
        """前非法 → 放行（不为历史遗留背锅），即使后仍非法。"""
        pm = _pm(tmp_path)
        pm.save_script("demo", _invalid_script(), "episode_1.json", validate=False)

        # 在本就非法的旧剧本上做一次合法编辑（改 title），不应被拦
        with pm.locked_script("demo", "episode_1.json") as script:
            script["title"] = "新标题"

        assert pm.load_script("demo", "episode_1.json")["title"] == "新标题"

    def test_valid_to_valid_is_allowed(self, tmp_path: Path):
        """前后都合法 → 放行。"""
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script(), "episode_1.json")

        with pm.locked_script("demo", "episode_1.json") as script:
            script["segments"][0]["duration_seconds"] = 10

        assert pm.load_script("demo", "episode_1.json")["segments"][0]["duration_seconds"] == 10

    def test_fresh_save_invalid_is_rejected(self, tmp_path: Path):
        """全新保存（无改前）+ 非法 → 严格拒绝。"""
        pm = _pm(tmp_path)
        with pytest.raises(ScriptStructureValidationError):
            pm.save_script("demo", _invalid_script(), "episode_1.json")

        # 拒绝后文件不应落盘
        scripts_dir = pm.get_project_path("demo") / "scripts"
        assert not (scripts_dir / "episode_1.json").exists()

    def test_fresh_save_valid_is_allowed(self, tmp_path: Path):
        """全新保存（无改前）+ 合法 → 放行。"""
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script(), "episode_1.json")
        assert pm.load_script("demo", "episode_1.json")["title"] == "标题"


class TestValidateDefaultsOn:
    def test_locked_script_validates_by_default(self, tmp_path: Path):
        """不显式传 validate 时默认开启校验（fail-safe）。"""
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script(), "episode_1.json")

        with pytest.raises(ScriptStructureValidationError):
            with pm.locked_script("demo", "episode_1.json") as script:  # 不传 validate
                script["segments"][0]["video_prompt"] = "坏形状"

    def test_validate_false_bypasses_guard(self, tmp_path: Path):
        """显式 validate=False 时即便引入非法结构也放行。"""
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script(), "episode_1.json")

        with pm.locked_script("demo", "episode_1.json", validate=False) as script:
            script["segments"][0]["video_prompt"] = "坏形状"

        assert pm.load_script("demo", "episode_1.json")["segments"][0]["video_prompt"] == "坏形状"


def _unit(unit_id: str = "E1U1") -> dict:
    shots = [{"duration": 3, "text": "镜头1"}, {"duration": 4, "text": "镜头2"}]
    return {
        "unit_id": unit_id,
        "shots": shots,
        "references": [],
        "duration_seconds": sum(s["duration"] for s in shots),
    }


def _reference_script(units: list[dict] | None = None) -> dict:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": units if units is not None else [_unit("E1U1"), _unit("E1U2")],
    }


class TestMetadataRecompute:
    def test_reference_video_metadata_counts_units(self, tmp_path: Path):
        """reference 模式经咽喉后 total_scenes 应等于 video_units 数、时长为各 unit 之和，
        而非落入 segments 兜底分支错算为 0。"""
        pm = _pm(tmp_path)
        pm.save_script("demo", _reference_script(), "episode_1.json")

        saved = pm.load_script("demo", "episode_1.json")
        assert saved["metadata"]["total_scenes"] == 2
        assert saved["metadata"]["estimated_duration_seconds"] == 14

    def test_narration_metadata_unchanged(self, tmp_path: Path):
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script([_segment("E1S01", 4), _segment("E1S02", 6)]), "episode_1.json")

        saved = pm.load_script("demo", "episode_1.json")
        assert saved["metadata"]["total_scenes"] == 2
        assert saved["metadata"]["estimated_duration_seconds"] == 10


class TestAssetWritebackExemption:
    def test_update_scene_asset_succeeds_on_invalid_script(self, tmp_path: Path):
        """资产回写（validate=False）在剧本本就非法时仍能成功写入。"""
        pm = _pm(tmp_path)
        pm.save_script("demo", _invalid_script(), "episode_1.json", validate=False)

        pm.update_scene_asset("demo", "episode_1.json", "E1S01", "storyboard_image", "storyboards/E1S01.png")

        saved = pm.load_script("demo", "episode_1.json")
        assert saved["segments"][0]["generated_assets"]["storyboard_image"] == "storyboards/E1S01.png"

    def test_batch_update_scene_assets_succeeds_on_invalid_script(self, tmp_path: Path):
        pm = _pm(tmp_path)
        pm.save_script("demo", _invalid_script(), "episode_1.json", validate=False)

        pm.batch_update_scene_assets("demo", "episode_1.json", [("E1S01", "video_clip", "videos/E1S01.mp4")])

        saved = pm.load_script("demo", "episode_1.json")
        assert saved["segments"][0]["generated_assets"]["video_clip"] == "videos/E1S01.mp4"

    def test_writeback_survives_corrupted_list_key(self, tmp_path: Path):
        """资产回写热路径：剧本含 `segments: null` 这类历史脏数据时，metadata 重算需 fallback
        而非阻塞写回——`resolve_items` 自身 fail-loud 是给编辑路径用的，写盘咽喉应翻译为容错。
        否则带脏列表键的项目里 update_scene_asset 等会全部失败。"""
        pm = _pm(tmp_path)
        # 直接落盘构造一个 segments=null 的脏剧本（绕开 save_script，模拟历史遗留）
        script_dir = tmp_path / "projects" / "demo" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "episode_1.json").write_text(
            '{"episode": 1, "title": "x", "content_mode": "narration", "segments": null, '
            '"novel": {"title": "n", "chapter": "c"}, "summary": ""}',
            encoding="utf-8",
        )
        with pm.locked_script("demo", "episode_1.json", validate=False) as script:
            script["generated_assets_demo"] = "anything"  # 任意非结构性变更
        saved = pm.load_script("demo", "episode_1.json")
        # 写回成功 + metadata 计算 fallback 到 0（脏数据下不汇总）
        assert saved.get("generated_assets_demo") == "anything"
        assert saved["metadata"]["total_scenes"] == 0
