"""AI 修复的范围判定与回复合并：条目模式只放回违约条目，整集模式只接受原草稿已有的字段。"""

from __future__ import annotations

import pytest

from lib.script.draft_quarantine import QUARANTINE_KIND_NARRATION_SCRIPT_PLAN
from server.draft_repair import RepairScope, merge_repair, repair_scope

_CONTENT = {"episode": 1, "segments": [{"segment_id": "E1S01"}, {"segment_id": "E1S02"}]}


def test_item_mode_puts_back_only_the_violating_items() -> None:
    scope = repair_scope(QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, _CONTENT, [{"item_index": 1}, {"item_index": 1}])
    assert scope == RepairScope("segments", (1,))

    merged = merge_repair(_CONTENT, scope, {"segments": [{"segment_id": "E1S02", "fixed": True}]})

    assert merged["segments"] == [{"segment_id": "E1S01"}, {"segment_id": "E1S02", "fixed": True}]
    assert _CONTENT["segments"][1] == {"segment_id": "E1S02"}


@pytest.mark.parametrize("violation", [{"item_index": None}, {"item_index": 5}, {"item_index": True}])
def test_unlocated_violation_switches_to_whole_draft_mode(violation: dict) -> None:
    scope = repair_scope(QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, _CONTENT, [{"item_index": 0}, violation])
    assert scope.indices is None


def test_whole_draft_mode_keeps_only_the_draft_fields() -> None:
    scope = RepairScope("segments", None)

    merged = merge_repair(_CONTENT, scope, {"segments": [{"segment_id": "E1S01"}], "notes": "已合并"})

    assert merged == {"episode": 1, "segments": [{"segment_id": "E1S01"}]}


@pytest.mark.parametrize(
    ("scope", "reply", "match"),
    [
        (RepairScope("segments", (1,)), {"segments": []}, "条目数"),
        (RepairScope("segments", (1,)), {"segments": ["not an object"]}, "条目不是 JSON 对象"),
        (RepairScope("segments", None), {"segments": []}, "缺少非空的 segments 数组"),
        (RepairScope("segments", None), ["not an object"], "模型回复不是 JSON 对象"),
    ],
    ids=["item_count_mismatch", "item_not_object", "empty_whole_draft", "reply_not_object"],
)
def test_malformed_reply_is_rejected(scope: RepairScope, reply: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        merge_repair(_CONTENT, scope, reply)
