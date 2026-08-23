"""Tests for emit_generation_success_batch."""

import pytest

from server.services import generation_tasks
from tests.integration.server.services.generation_tasks_support import (
    _FakePM,
)


class TestGenerationTasks:
    def test_emit_success_batch_includes_fingerprints(self, monkeypatch, tmp_path):
        """生成成功事件应携带 asset_fingerprints"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        (project_path / "storyboards").mkdir()
        sb = project_path / "storyboards" / "scene_E1S01.png"
        sb.write_bytes(b"img")

        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="storyboard",
            project_name="demo",
            resource_id="E1S01",
            payload={"script_file": "ep01.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert "asset_fingerprints" in change
        assert "storyboards/scene_E1S01.png" in change["asset_fingerprints"]
        assert isinstance(change["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)

    @pytest.mark.parametrize(
        ("task_type", "expected_label_key", "expected_label"),
        [
            pytest.param("grid", "grid", "多宫格分镜「E1G01」", id="grid"),
            pytest.param("grid_split", "grid_split", "多宫格分镜「E1G01」切分", id="grid-split"),
            pytest.param("voice_sample", "voice_sample", "「E1G01」试听样本", id="voice-sample"),
            pytest.param("character", "asset_image_character", "角色「E1G01」资产图", id="character-sheet"),
            pytest.param("prop", "asset_image_prop", "道具「E1G01」资产图", id="prop-sheet"),
        ],
    )
    def test_emit_success_batch_carries_label_key_and_params(
        self, monkeypatch, tmp_path, task_type, expected_label_key, expected_label
    ):
        """完成事件携带稳定 label_key 与参数，界面据此按用户语言成文；label 只是默认语言兜底。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: _FakePM(project_path))

        generation_tasks.emit_generation_success_batch(
            task_type=task_type,
            project_name="demo",
            resource_id="E1G01",
            payload={},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["label_key"] == expected_label_key
        assert change["label_params"] == {"id": "E1G01"}
        assert change["label"] == expected_label

    @pytest.mark.parametrize(
        ("script", "expected_entity_type", "expected_label"),
        [
            pytest.param(
                {"content_mode": "drama", "scenes": [{"scene_id": "E1S01"}]},
                "drama_scene",
                "分镜「E1S01」",
                id="drama-scenes",
            ),
            pytest.param(
                {"content_mode": "ad", "shots": [{"shot_id": "E1S01"}]},
                "shot",
                "分镜「E1S01」",
                id="ad-shots",
            ),
            pytest.param(
                {"content_mode": "narration", "segments": [{"segment_id": "E1S01"}]},
                "segment",
                "分镜「E1S01」",
                id="narration-segments",
            ),
        ],
    )
    def test_emit_success_batch_storyboard_entity_type_follows_skeleton(
        self, monkeypatch, tmp_path, script, expected_entity_type, expected_label
    ):
        """storyboard/video 任务完成通知与分镜级事件同口径：实体类型按项目剧本骨架解析，
        三种分镜骨架的中文名词统一为「分镜」。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = script
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        for task_type, action in (("storyboard", "storyboard_ready"), ("video", "video_ready")):
            captured.clear()
            generation_tasks.emit_generation_success_batch(
                task_type=task_type,
                project_name="demo",
                resource_id="E1S01",
                payload={"script_file": "ep01.json"},
            )
            assert len(captured) == 1
            change = captured[0][0]
            assert change["entity_type"] == expected_entity_type
            assert change["action"] == action
            assert change["label"] == expected_label

    def test_emit_success_batch_reference_video_entity_type_aligns_with_frontend(self, monkeypatch, tmp_path):
        """参考生视频任务完成通知的 entity_type 需为前端联合类型认识的 "reference_unit"
        （而非仅本侧认识的 "reference_video_unit"），分组标题才能落「视频单元」而非「内容」
        兜底；条目文案统一使用「视频单元」，不随骨架名词改动。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = {"content_mode": "narration", "video_units": [{"unit_id": "U01"}]}
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="reference_video",
            project_name="demo",
            resource_id="U01",
            payload={"script_file": "ep01.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "reference_unit"
        assert change["action"] == "reference_video_ready"
        assert change["label_key"] == "skeleton_video_units"
        assert change["label"] == "视频单元「U01」"

    def test_emit_success_batch_reference_video_ad_entity_type_not_shot(self, monkeypatch, tmp_path):
        """ad 剧本骨架恒为 shots[]，reference_video 路径派生的 video_unit 索引与 shots
        同存于一份剧本 JSON——resolve_script_kind 的数据形状判别会因 shots 键仍在而落回
        content_mode==ad→shots，与该任务实际对应 video_unit 资源不符，故需固定解析，
        不随骨架判别漂到 "shot"。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = {
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01"}],
            "video_units": [{"unit_id": "U01", "shot_ids": ["E1S01"]}],
        }
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="reference_video",
            project_name="demo",
            resource_id="U01",
            payload={"script_file": "episode_1.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "reference_unit"
        assert change["action"] == "reference_video_ready"
        assert change["label_key"] == "skeleton_video_units"
        assert change["label"] == "视频单元「U01」"

    def test_emit_success_batch_reference_video_tts_entity_type_not_shot(self, monkeypatch, tmp_path):
        """TTS 任务与视频任务共用项目生成模式，ad 参考生视频的混合骨架不能把 unit 事件分到 shot。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.project.update(content_mode="ad", generation_mode="reference_video")
        fake_pm.script = {
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01"}],
            "video_units": [{"unit_id": "E1U01"}],
        }
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="tts",
            project_name="demo",
            resource_id="E1U01",
            payload={"script_file": "episode_1.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "reference_unit"
        assert change["action"] == "tts_ready"
        assert change["label_key"] == "narration_audio"
        assert change["label"] == "旁白配音「E1U01」"

    def test_emit_success_batch_falls_back_to_segments_when_script_load_fails(self, monkeypatch, tmp_path):
        """骨架判定拿不到剧本（脚本缺失/损坏）时兜底 segments/「分镜」，不让通知发送中断。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)

        def _raise_load_script(project_name, script_file):
            raise FileNotFoundError(script_file)

        fake_pm.load_script = _raise_load_script
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="storyboard",
            project_name="demo",
            resource_id="E1S01",
            payload={"script_file": "missing.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "segment"
        assert change["label"] == "分镜「E1S01」"

    def test_emit_success_batch_falls_back_to_segments_when_script_not_a_dict(self, monkeypatch, tmp_path):
        """剧本文件内容损坏成非 dict（如顶层数组）时兜底 segments/「分镜」，不让
        resolve_script_kind 内部的 .get() 调用抛 AttributeError 中断通知发送。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = ["not", "a", "dict"]
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="storyboard",
            project_name="demo",
            resource_id="E1S01",
            payload={"script_file": "corrupted.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "segment"
        assert change["label"] == "分镜「E1S01」"

    def test_emit_success_batch_attaches_script_file_and_episode(self, monkeypatch, tmp_path):
        """骨架驱动的完成事件须挂 script_file 与 episode（供 episode 作用域消费方使用）——
        锁死这条挂载，防将来改动 emit 时静默丢字段。"""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = {"content_mode": "narration", "episode": 3, "segments": [{"segment_id": "E3S01"}]}
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        for task_type in ("storyboard", "video", "reference_video"):
            captured.clear()
            generation_tasks.emit_generation_success_batch(
                task_type=task_type,
                project_name="demo",
                resource_id="E3S01",
                payload={"script_file": "ep03.json"},
            )
            assert len(captured) == 1
            change = captured[0][0]
            assert change["script_file"] == "ep03.json"
            assert change["episode"] == 3
