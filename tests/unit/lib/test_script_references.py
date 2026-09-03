"""剧本载荷里引用名的落点遍历与「被引用」判定。"""

from __future__ import annotations

from typing import Any

import pytest

from lib.script_references import annotate_derivative_references, payload_reference_names


def _drama_script() -> dict[str, Any]:
    return {
        "content_mode": "drama",
        "scenes": [
            {
                "scene_id": "E1S01",
                "characters_in_scene": ["阿岚", "阿岚/战斗装"],
                "scenes": ["茶楼"],
                "props": ["玉佩"],
                "utterances": [
                    {"kind": "dialogue", "speaker": "阿岚", "text": "台词"},
                    {"kind": "voiceover", "speaker": None, "text": "旁白"},
                ],
            }
        ],
    }


class TestPayloadReferenceNames:
    def test_collects_reference_arrays_speakers_and_body_mentions(self):
        payload = _drama_script()
        payload["video_units"] = [{"unit_id": "E1U1", "text": "@[阿岚/兽化] 推门。@[阿岚/兽化]{我来了}"}]

        assert payload_reference_names(payload) == {"阿岚", "阿岚/战斗装", "茶楼", "玉佩", "阿岚/兽化"}

    def test_speaker_slot_mentions_count_as_references(self):
        """说话人位不进参考图，但它写下的名字确实写在剧本里——改名要改它，被引用要数它。"""
        payload = {"video_units": [{"unit_id": "E1U1", "text": "@[阿岚/战斗装]{我来了}"}]}

        assert payload_reference_names(payload) == {"阿岚/战斗装"}

    def test_null_speaker_and_non_string_items_are_skipped(self):
        payload = {"scenes": [{"characters_in_scene": ["阿岚", None, 7], "utterances": [{"speaker": None}]}]}

        assert payload_reference_names(payload) == {"阿岚"}

    def test_comparison_key_coordinates_are_applied(self):
        payload = {"scenes": [{"characters_in_scene": [" 阿岚 "]}]}

        assert payload_reference_names(payload) == {"阿岚"}

    @pytest.mark.parametrize("payload", [None, [], "text", 7], ids=["空", "列表", "字符串", "数字"])
    def test_malformed_payload_yields_nothing(self, payload: object):
        assert payload_reference_names(payload) == set()


PROJECT: dict[str, Any] = {
    "characters": {
        "阿岚": {"description": "少女", "derivatives": {"战斗装": {"description": "重甲"}, "便装": {}}},
        "青禾": {"description": "配角"},
    },
    "scenes": {"茶楼": {"description": "旧楼"}},
}


class TestAnnotateDerivativeReferences:
    def test_marks_referenced_and_unreferenced_derivatives(self):
        annotated = annotate_derivative_references(PROJECT, [_drama_script()])

        derivatives = annotated["characters"]["阿岚"]["derivatives"]
        assert derivatives["战斗装"]["referenced"] is True
        assert derivatives["便装"]["referenced"] is False

    def test_input_payload_is_left_untouched(self):
        """读时计算：写盘路径与读取路径共用载荷时，就地改写会把它带进 project.json。"""
        annotate_derivative_references(PROJECT, [_drama_script()])

        assert "referenced" not in PROJECT["characters"]["阿岚"]["derivatives"]["战斗装"]

    def test_characters_without_derivatives_and_other_buckets_are_preserved(self):
        annotated = annotate_derivative_references(PROJECT, [])

        assert annotated["characters"]["青禾"] == {"description": "配角"}
        assert annotated["scenes"] == PROJECT["scenes"]

    def test_reference_written_in_another_encoding_form_still_counts(self):
        payload = {"video_units": [{"unit_id": "E1U1", "text": "@[ 阿岚/战斗装 ]"}]}

        annotated = annotate_derivative_references(PROJECT, [payload])

        assert annotated["characters"]["阿岚"]["derivatives"]["战斗装"]["referenced"] is True

    @pytest.mark.parametrize("table", [None, [], {"战斗装": "not-a-dict"}], ids=["缺失", "列表", "值非对象"])
    def test_malformed_derivative_tables_pass_through(self, table: object):
        project = {"characters": {"阿岚": {"derivatives": table}}}

        annotated = annotate_derivative_references(project, [])

        assert annotated["characters"]["阿岚"]["derivatives"] == table

    def test_malformed_project_payload_yields_an_empty_mapping(self):
        assert annotate_derivative_references(None, []) == {}
