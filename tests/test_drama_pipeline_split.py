"""drama 两段式流水线切分（step1 内容 / step2 视觉）的模型、合并与校验测试。

覆盖：
- DramaScene.source_text 字段
- step1 内容模型 DramaSceneContent / DramaNormalizedScript（含 duration 枚举硬约束）
- step2 视觉模型 DramaSceneVisual / DramaVisualScript
- merge_drama_visual_into_scenes：按 scene_id 合并、唯一性 + 全覆盖校验
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lib.episode_ledger import episode_outline_context
from lib.script_models import (
    DramaEpisodeScript,
    DramaNormalizedScript,
    DramaScene,
    DramaSceneContent,
    DramaSceneVisual,
    DramaVisualMergeError,
    DramaVisualScript,
    build_drama_normalized_script_model,
    merge_drama_visual_into_scenes,
)


def _content_scene(scene_id: str = "E1S01", **overrides) -> dict:
    base = {
        "scene_id": scene_id,
        "duration_seconds": 8,
        "segment_break": False,
        "characters_in_scene": ["林清"],
        "scenes": [],
        "props": [],
        "scene_description": "林清坐在窗边木桌前，目光落在桌上一封拆开的信纸上。",
        "utterances": [
            {"kind": "dialogue", "speaker": "林清", "text": "师父，我回来了。"},
            {"kind": "voiceover", "speaker": None, "text": "雨夜，往事浮现。"},
        ],
        "source_text": "林清回到故居，推门而入，信纸还在桌上。",
    }
    base.update(overrides)
    return base


def _visual_scene(scene_id: str = "E1S01", **overrides) -> dict:
    base = {
        "scene_id": scene_id,
        "image_prompt": {
            "scene": "林清坐在窗边木桌前，半边脸笼在逆光阴影里。",
            "composition": {"shot_type": "Medium Shot", "lighting": "逆光蓝灰", "ambiance": "雨丝拍窗"},
        },
        "video_prompt": {
            "action": "林清缓缓抬起头，手指摩挲信纸边缘。",
            "camera_motion": "Static",
            "ambiance_audio": "雨声渐大",
        },
    }
    base.update(overrides)
    return base


class TestDramaSceneSourceText:
    def test_source_text_defaults_to_empty(self):
        scene = DramaScene.model_validate(
            {
                "scene_id": "E1S01",
                "characters_in_scene": ["林清"],
                "image_prompt": {
                    "scene": "x",
                    "composition": {"shot_type": "Medium Shot", "lighting": "a", "ambiance": "b"},
                },
                "video_prompt": {"action": "x", "camera_motion": "Static", "ambiance_audio": "y"},
            }
        )
        assert scene.source_text == ""

    def test_source_text_preserved_verbatim(self):
        scene = DramaScene.model_validate(
            {
                "scene_id": "E1S01",
                "characters_in_scene": ["林清"],
                "source_text": "推门而入，信纸还在桌上。",
                "utterances": [{"kind": "dialogue", "speaker": "林清", "text": "我回来了"}],
                "image_prompt": {
                    "scene": "x",
                    "composition": {"shot_type": "Medium Shot", "lighting": "a", "ambiance": "b"},
                },
                "video_prompt": {"action": "x", "camera_motion": "Static", "ambiance_audio": "y"},
            }
        )
        assert scene.source_text == "推门而入，信纸还在桌上。"


class TestDramaSceneContent:
    def test_valid_content_scene(self):
        content = DramaSceneContent.model_validate(_content_scene())
        assert content.scene_id == "E1S01"
        assert content.source_text.startswith("林清回到")
        assert len(content.utterances) == 2
        assert content.scene_description

    def test_content_scene_rejects_visual_fields(self):
        # 视觉字段不属于 step1 内容；extra=forbid 应拒绝
        with pytest.raises(ValidationError):
            DramaSceneContent.model_validate(_content_scene(image_prompt={"scene": "x"}))

    def test_content_scene_enforces_kind_speaker(self):
        with pytest.raises(ValidationError):
            DramaSceneContent.model_validate(
                _content_scene(utterances=[{"kind": "voiceover", "speaker": "林清", "text": "x"}])
            )

    def test_content_scene_rejects_empty_scene_id(self):
        # 空 scene_id 应在 schema 层被拒：merge 把空 scene_id 当错误，提前到校验层 fail-fast
        with pytest.raises(ValidationError):
            DramaSceneContent.model_validate(_content_scene(scene_id=""))

    def test_normalized_script_holds_content_scenes(self):
        script = DramaNormalizedScript.model_validate(
            {"title": "第一集", "scenes": [_content_scene("E1S01"), _content_scene("E1S02")]}
        )
        assert len(script.scenes) == 2


class TestDramaNormalizedDurationEnum:
    def test_duration_constrained_to_supported(self):
        model = build_drama_normalized_script_model([4, 6, 8])
        # 合法
        model.model_validate({"title": "t", "scenes": [_content_scene(duration_seconds=6)]})
        # 非成员（5）应被枚举硬约束拒绝
        with pytest.raises(ValidationError):
            model.model_validate({"title": "t", "scenes": [_content_scene(duration_seconds=5)]})


class TestDramaSceneVisual:
    def test_visual_scene_only_visual_fields(self):
        visual = DramaSceneVisual.model_validate(_visual_scene())
        assert visual.scene_id == "E1S01"

    def test_visual_scene_rejects_content_fields(self):
        with pytest.raises(ValidationError):
            DramaSceneVisual.model_validate(_visual_scene(utterances=[]))

    def test_visual_scene_rejects_empty_scene_id(self):
        # 对齐锚 scene_id 不得为空串，否则合并阶段才暴露
        with pytest.raises(ValidationError):
            DramaSceneVisual.model_validate(_visual_scene(scene_id=""))

    def test_visual_video_prompt_rejects_dialogue(self):
        # drama 视觉层用 DramaVideoPrompt（无 dialogue）
        with pytest.raises(ValidationError):
            DramaSceneVisual.model_validate(
                _visual_scene(
                    video_prompt={
                        "action": "x",
                        "camera_motion": "Static",
                        "ambiance_audio": "y",
                        "dialogue": [],
                    }
                )
            )

    def test_visual_script_holds_visual_scenes(self):
        script = DramaVisualScript.model_validate({"scenes": [_visual_scene("E1S01"), _visual_scene("E1S02")]})
        assert len(script.scenes) == 2


class TestMergeDramaVisualIntoScenes:
    def test_merge_by_scene_id_produces_full_drama_scenes(self):
        content = [_content_scene("E1S01"), _content_scene("E1S02")]
        # 视觉列表顺序与内容相反——合并按 scene_id 对齐、非列表顺序
        visual = [_visual_scene("E1S02"), _visual_scene("E1S01")]
        merged = merge_drama_visual_into_scenes(content, visual)
        assert [s["scene_id"] for s in merged] == ["E1S01", "E1S02"]  # 保持内容顺序
        # 每个合并结果都是合法 DramaScene，且携带 step1 的 utterances/source_text
        for scene in merged:
            DramaScene.model_validate(scene)
            assert scene["utterances"]
            assert scene["source_text"]
            assert "image_prompt" in scene and "video_prompt" in scene
            # scene_description 是 step1 视觉基底，不进最终 DramaScene
            assert "scene_description" not in scene

    def test_merge_passes_full_episode_validation(self):
        merged = merge_drama_visual_into_scenes([_content_scene("E1S01")], [_visual_scene("E1S01")])
        DramaEpisodeScript.model_validate({"title": "第一集", "scenes": merged})

    def test_merge_missing_visual_raises(self):
        with pytest.raises(DramaVisualMergeError):
            merge_drama_visual_into_scenes([_content_scene("E1S01"), _content_scene("E1S02")], [_visual_scene("E1S01")])

    def test_merge_non_dict_visual_item_raises(self):
        # step2 校验降级返回原始 scenes 时可能混入非 dict 条目：须 fail-loud 抛 DramaVisualMergeError，
        # 而非对其调用 .get() 触发 AttributeError、绕过合并错误路径
        with pytest.raises(DramaVisualMergeError):
            merge_drama_visual_into_scenes([_content_scene("E1S01")], ["bad", _visual_scene("E1S01")])

    def test_merge_non_dict_content_item_raises(self):
        with pytest.raises(DramaVisualMergeError):
            merge_drama_visual_into_scenes([42, _content_scene("E1S01")], [_visual_scene("E1S01")])

    def test_merge_orphan_visual_raises(self):
        with pytest.raises(DramaVisualMergeError):
            merge_drama_visual_into_scenes([_content_scene("E1S01")], [_visual_scene("E1S01"), _visual_scene("E1S99")])

    def test_merge_duplicate_visual_scene_id_raises(self):
        with pytest.raises(DramaVisualMergeError):
            merge_drama_visual_into_scenes([_content_scene("E1S01")], [_visual_scene("E1S01"), _visual_scene("E1S01")])

    def test_merge_duplicate_content_scene_id_raises(self):
        # step1 内容侧重复 scene_id：两个场景会共用同一视觉、下游产物文件名撞键，须 fail-loud
        with pytest.raises(DramaVisualMergeError):
            merge_drama_visual_into_scenes([_content_scene("E1S01"), _content_scene("E1S01")], [_visual_scene("E1S01")])

    def test_merge_visual_missing_visual_fields_raises(self):
        # step2 校验降级返回的半成品视觉条目可能只有 scene_id、缺 image_prompt / video_prompt：
        # 须在合并阶段 fail-loud，而非写入 None 绕过 DramaVisualMergeError、拖到 save_script 才以通用异常失败
        with pytest.raises(DramaVisualMergeError):
            merge_drama_visual_into_scenes([_content_scene("E1S01")], [{"scene_id": "E1S01"}])
        # 仅缺 video_prompt 也须 fail-loud
        with pytest.raises(DramaVisualMergeError):
            merge_drama_visual_into_scenes(
                [_content_scene("E1S01")],
                [{"scene_id": "E1S01", "image_prompt": {"scene": "x"}}],
            )


class TestEpisodeOutlineContext:
    """内容抽取前移后，分集大纲（故事节点 / 钩子）作为 step1 内容生成的规划输入。"""

    def _project(self) -> dict:
        return {
            "episodes": [
                {
                    "episode": 1,
                    "title": "初入江湖",
                    "hook": "少年坠崖",
                    "outline": {"story_beats": ["下山", "遇敌"], "next_episode_teaser": "神秘人相救"},
                },
                {"episode": 2, "title": "绝处逢生"},  # 旧式条目：无规划数据
            ]
        }

    def test_returns_current_and_next(self):
        cur, nxt = episode_outline_context(self._project(), 1)
        assert cur is not None and cur["hook"] == "少年坠崖"
        assert cur["story_beats"] == ["下山", "遇敌"]
        assert cur["next_episode_teaser"] == "神秘人相救"
        # 下一集是旧式条目（无 hook/beats/teaser）→ None
        assert nxt is None

    def test_missing_episode_returns_none(self):
        cur, nxt = episode_outline_context(self._project(), 9)
        assert cur is None and nxt is None

    def test_corrupt_story_beats_treated_as_missing(self):
        project = {"episodes": [{"episode": 1, "hook": "钩子", "outline": {"story_beats": "非列表"}}]}
        cur, _ = episode_outline_context(project, 1)
        assert cur is not None and cur["story_beats"] == []

    def test_non_string_story_beats_items_filtered(self):
        # list 内非字符串项（手编损坏）一并过滤，避免脏数据原样进 step1 prompt
        project = {"episodes": [{"episode": 1, "hook": "钩子", "outline": {"story_beats": ["下山", 42, None, "遇敌"]}}]}
        cur, _ = episode_outline_context(project, 1)
        assert cur is not None and cur["story_beats"] == ["下山", "遇敌"]
