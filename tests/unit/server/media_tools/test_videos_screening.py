"""分镜图生视频的剧本条目筛查：成不了目标的条目按记名拒收，不被静默滤掉，也不打断整批。"""

from __future__ import annotations

from typing import Any

import pytest

from lib.generation.generation_result import GenerationProblemCode
from server.media_tools.videos import screen_storyboard_items


def _segment(segment_id: object, **fields: Any) -> dict[str, Any]:
    return {"segment_id": segment_id, "video_prompt": "镜头平移", **fields}


@pytest.mark.parametrize(
    ("items", "requested_ids", "clean_ids", "refused_ids"),
    [
        pytest.param(
            [_segment("E1S01"), _segment("E1S01")],
            None,
            ["E1S01"],
            ["E1S01#1"],
            id="duplicate-id-keeps-the-first-and-names-the-copy-by-position",
        ),
        pytest.param([_segment(["E1S01"])], None, [], ["items[0]"], id="non-scalar-id"),
        pytest.param([42, _segment("E1S01")], None, ["E1S01"], ["items[0]"], id="non-object-entry"),
        pytest.param([_segment("E1S01"), _segment("")], None, ["E1S01"], ["items[1]"], id="id-less-entry"),
        pytest.param(
            [42, _segment("items[0]")],
            None,
            ["items[0]"],
            ["items[0]*"],
            id="diagnostic-name-never-shadows-a-real-id",
        ),
        pytest.param(
            [_segment("E1S01", scene_id="SC1"), _segment("E1S02", scene_id="SC1")],
            {"SC1"},
            [],
            ["SC1"],
            id="named-alias-points-at-two-items",
        ),
        pytest.param(
            [_segment("E1S01", scene_id="A"), _segment("E1S01", scene_id="B")],
            {"B"},
            [],
            ["B"],
            id="two-aliases-over-one-canonical-id",
        ),
        pytest.param(
            [_segment(0, scene_id="E1S01")],
            {"E1S01"},
            [],
            ["E1S01"],
            id="non-scalar-canonical-id-is-not-masked-by-an-alias",
        ),
        pytest.param(
            [_segment("E1S01"), _segment("E1S01")],
            {"E1S01"},
            [],
            ["E1S01"],
            id="named-id-with-a-duplicate-is-refused-under-the-requested-name",
        ),
        pytest.param(
            [_segment("E1S01", scene_id=["E1S01"])],
            {"E1S01"},
            ["E1S01"],
            [],
            id="non-scalar-alias-does-not-break-addressing-by-canonical-id",
        ),
        pytest.param(
            [_segment("E1S01"), _segment(["E1S02"])],
            {"E1S01"},
            ["E1S01"],
            [],
            id="dirt-outside-the-named-ids-does-not-veto-a-named-request",
        ),
    ],
)
def test_screening_names_each_unaddressable_entry(
    items: list[Any],
    requested_ids: set[str] | None,
    clean_ids: list[str],
    refused_ids: list[str],
) -> None:
    clean, refused = screen_storyboard_items(items, "segment_id", requested_ids=requested_ids)

    assert [item["segment_id"] for item in clean] == clean_ids
    assert [ticket.unit_id for ticket in refused] == refused_ids
    assert all(
        [problem.code for problem in ticket.problems] == [GenerationProblemCode.UNIT_REQUEST_INVALID]
        for ticket in refused
    )
