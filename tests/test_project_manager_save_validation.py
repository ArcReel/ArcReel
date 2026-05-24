"""写盘统一入口「不更坏」结构校验守卫测试。

只断言外部行为：构造 before/after 剧本，断言写盘是否 raise ScriptStructureValidationError，
以及资产回写豁免、validate 默认值，不 patch 私有方法。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from lib.script_editor import ScriptEditError
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
        """reference 模式经统一入口后 total_scenes 应等于 video_units 数、时长为各 unit 之和，
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

    def _seed_corrupted_null_segments(self, pm, tmp_path: Path) -> None:
        """直接落盘构造 segments=null 的脏剧本，绕过 save_script 模拟历史遗留。"""
        script_dir = tmp_path / "projects" / "demo" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "episode_1.json").write_text(
            '{"episode": 1, "title": "x", "content_mode": "narration", "segments": null, '
            '"novel": {"title": "n", "chapter": "c"}, "summary": ""}',
            encoding="utf-8",
        )

    def _seed_corrupted_null_video_units(self, tmp_path: Path) -> None:
        """构造 video_units=null 的 reference 模式脏剧本，且事先有合理的 metadata（模拟数据
        损坏前的状态），用于验证脏数据 fallback 时保留旧 metadata 而非重算成错值。"""
        script_dir = tmp_path / "projects" / "demo" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "episode_1.json").write_text(
            '{"episode": 1, "title": "x", "content_mode": "narration", '
            '"generation_mode": "reference_video", "video_units": null, '
            '"novel": {"title": "n", "chapter": "c"}, "summary": "", '
            '"metadata": {"created_at": "2024-01-01T00:00:00+00:00", "status": "draft", '
            '"updated_at": "2024-01-01T00:00:00+00:00", "total_scenes": 5, "estimated_duration_seconds": 40}}',
            encoding="utf-8",
        )

    def test_update_scene_asset_fails_loud_on_corrupted_list_key(self, tmp_path: Path):
        """`update_scene_asset` 在 `segments: null` 下必须 fail-loud（raise ScriptEditError）——
        静默 no-op 会让 worker 以为回写成功但实际数据丢失。worker 层应该 catch 这个错误并
        把任务标为失败，让运维看到「数据损坏」而不是「成功但空」。"""
        pm = _pm(tmp_path)
        self._seed_corrupted_null_segments(pm, tmp_path)
        with pytest.raises(ScriptEditError, match="必须是列表"):
            pm.update_scene_asset("demo", "episode_1.json", "E1S01", "storyboard_image", "x.png")

    def test_batch_update_scene_assets_fails_loud_on_corrupted_list_key(self, tmp_path: Path):
        """`batch_update_scene_assets` 在 `segments: null` 下必须 fail-loud——批量场景下静默
        no-op 危害更大：worker 写完 N 个 clip 全被丢、SSE 仍然广播「all updated」、UI 永远 pending。"""
        pm = _pm(tmp_path)
        self._seed_corrupted_null_segments(pm, tmp_path)
        with pytest.raises(ScriptEditError, match="必须是列表"):
            pm.batch_update_scene_assets("demo", "episode_1.json", [("E1S01", "video_clip", "videos/E1S01.mp4")])

    def test_writeback_preserves_old_metadata_on_corrupted_list_key(self, tmp_path: Path):
        """资产回写热路径在 reference 模式 `video_units: null` 下：metadata 重算应**跳过**而非
        hard-pin 到 segments shell。否则 fallback 会把 total_scenes=0 / 默认时长写回，
        把 reference 项目的 metadata 改写成 0-scene narration shell——脏数据「不更坏」的反面。
        正确行为：保留旧 metadata 不动（即便陈旧也好过写错的），仅 updated_at 刷新。"""
        pm = _pm(tmp_path)
        self._seed_corrupted_null_video_units(tmp_path)

        # 非结构性变更走 validate=False（资产回写热路径）
        with pm.locked_script("demo", "episode_1.json", validate=False) as script:
            script["generated_assets_demo"] = "anything"

        saved = pm.load_script("demo", "episode_1.json")
        # 旧 metadata 完全保留（除 updated_at 必然刷新外）
        assert saved["metadata"]["total_scenes"] == 5
        assert saved["metadata"]["estimated_duration_seconds"] == 40
        assert saved.get("generated_assets_demo") == "anything"

    def test_get_pending_scenes_warns_and_returns_empty_on_corrupted_list_key(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """读取路径（get_pending_scenes）在脏数据下不阻塞 UI 渲染：返回 [] + 发出可观测的
        warning 日志，让运维有信号去人工修复，不让降级变隐形。"""
        pm = _pm(tmp_path)
        self._seed_corrupted_null_segments(pm, tmp_path)
        with caplog.at_level(logging.WARNING, logger="lib.project_manager"):
            result = pm.get_pending_scenes("demo", "episode_1.json", "storyboard_image")
        assert result == []
        assert any("segments" in rec.message and "数据损坏" in rec.message for rec in caplog.records)

    def test_get_scenes_needing_storyboard_warns_and_returns_empty_on_corrupted_list_key(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """同上：get_scenes_needing_storyboard 在脏数据下也是 warn + []。"""
        pm = _pm(tmp_path)
        self._seed_corrupted_null_segments(pm, tmp_path)
        with caplog.at_level(logging.WARNING, logger="lib.project_manager"):
            result = pm.get_scenes_needing_storyboard("demo", "episode_1.json")
        assert result == []
        assert any("segments" in rec.message and "数据损坏" in rec.message for rec in caplog.records)

    def test_get_pending_scenes_handles_item_without_generated_assets(self, tmp_path: Path):
        """读取侧容错：列表内 item 缺 generated_assets 字段（结构脏数据但列表本身完好）时
        不应抛 KeyError，按"未生成"算进 pending——与 get_scenes_needing_storyboard 的容错对齐。"""
        pm = _pm(tmp_path)
        # 直接落盘：segments 是合法 list，但内部 item 缺 generated_assets
        script_dir = tmp_path / "projects" / "demo" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "episode_1.json").write_text(
            '{"episode": 1, "title": "x", "content_mode": "narration", '
            '"segments": [{"segment_id": "E1S01", "duration_seconds": 4}], '
            '"novel": {"title": "n", "chapter": "c"}, "summary": ""}',
            encoding="utf-8",
        )
        # 不应抛 KeyError；item 缺字段 → pending（含进结果）
        result = pm.get_pending_scenes("demo", "episode_1.json", "storyboard_image")
        assert len(result) == 1
        assert result[0]["segment_id"] == "E1S01"

    def test_reference_video_read_helpers_return_units(self, tmp_path: Path):
        """reference 模式下 get_pending_scenes / get_scenes_needing_storyboard 必须按 video_units
        返回待办——旧 `_script_items_shape` 在 reference 下落到 drama 兜底取 "scenes" 键,reference
        脚本无该键,静默返回 [],UI 显示"无待办"。改用 resolve_items 统一三模式判别后修复。"""
        pm = _pm(tmp_path)
        pm.save_script("demo", _reference_script(), "episode_1.json")

        pending = pm.get_pending_scenes("demo", "episode_1.json", "storyboard_image")
        assert [item["unit_id"] for item in pending] == ["E1U1", "E1U2"]

        needing = pm.get_scenes_needing_storyboard("demo", "episode_1.json")
        assert [item["unit_id"] for item in needing] == ["E1U1", "E1U2"]

    def test_reference_video_update_scene_asset_writes_unit(self, tmp_path: Path):
        """reference 模式下 update_scene_asset 必须按 unit_id 索引 video_units 回写资产——
        旧 helper 在 reference 下取 "scenes" 键找不到任何 unit_id,KeyError("场景不存在")
        掩盖了根因（路径选错而非 id 不存在）。"""
        pm = _pm(tmp_path)
        pm.save_script("demo", _reference_script(), "episode_1.json")

        pm.update_scene_asset("demo", "episode_1.json", "E1U1", "storyboard_image", "storyboards/E1U1.png")

        saved = pm.load_script("demo", "episode_1.json")
        assert saved["video_units"][0]["generated_assets"]["storyboard_image"] == "storyboards/E1U1.png"

    @pytest.mark.parametrize(
        "assets_json",
        ["null", '"corrupted"', "[]"],
        ids=["null", "string", "list"],
    )
    def test_get_pending_scenes_handles_non_dict_generated_assets(self, tmp_path: Path, assets_json: str):
        """读取侧容错：item.generated_assets 存在但是 null / 字符串 / 非 dict 时不抛 AttributeError，
        视为"未生成"算进 pending。`item.get("generated_assets", {}).get(...)` 只挡 key 缺失,
        None / 非 dict 仍会崩——必须用 isinstance check 与写入侧 update_scene_asset 的 mirror。"""
        pm = _pm(tmp_path)
        script_dir = tmp_path / "projects" / "demo" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "episode_1.json").write_text(
            '{"episode": 1, "title": "x", "content_mode": "narration", '
            f'"segments": [{{"segment_id": "E1S01", "duration_seconds": 4, "generated_assets": {assets_json}}}], '
            '"novel": {"title": "n", "chapter": "c"}, "summary": ""}',
            encoding="utf-8",
        )
        result = pm.get_pending_scenes("demo", "episode_1.json", "storyboard_image")
        assert len(result) == 1
        # 同样 get_scenes_needing_storyboard 也容错
        result2 = pm.get_scenes_needing_storyboard("demo", "episode_1.json")
        assert len(result2) == 1
