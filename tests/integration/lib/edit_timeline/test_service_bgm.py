"""剪辑时间线的 BGM 片段：在真实临时项目上经公开服务命令验证增删改、摆放规则、读取形态与乐观并发。"""

from __future__ import annotations

from typing import Any

import pytest

from lib.bgm.service import BgmLibraryService
from lib.edit_timeline import EditTimelineError, EditTimelineService, RevisionAuthor
from lib.edit_timeline.operations import TimelineOperationAdapter
from lib.project.project_manager import ProjectManager
from tests.factories import wav_bytes

AGENT = RevisionAuthor(kind="arcreel_agent")


@pytest.fixture
async def bgm_id(pm: ProjectManager) -> str:
    """一首 2 秒的 BGM。"""
    track = await BgmLibraryService(pm).upload("demo", filename="雨夜.wav", content=wav_bytes(2.0, tone_hz=330))
    return track.id


async def _create(service: EditTimelineService) -> str:
    created = await service.create_from_script("demo", episode=1, name="初剪", author=AGENT)
    return created.timeline.id


async def _edit(service: EditTimelineService, timeline_id: str, base_revision: int, *operations: dict[str, Any]):
    return await service.edit(
        "demo",
        timeline_id,
        base_revision=base_revision,
        summary="调整 BGM",
        operations=[TimelineOperationAdapter.validate_python(operation) for operation in operations],
        author=AGENT,
    )


def _rows(bgm: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    return [
        (
            clip.id,
            clip.name,
            clip.start,
            clip.end,
            clip.source_in,
            clip.source_out,
            clip.volume,
            clip.fade_in,
            clip.fade_out,
        )
        for clip in bgm
    ]


@pytest.mark.usefixtures("three_clips")
async def test_inserted_bgm_takes_defaults_and_is_cut_at_the_timeline_end(
    service: EditTimelineService, bgm_id: str
) -> None:
    timeline_id = await _create(service)

    result = await _edit(
        service,
        timeline_id,
        1,
        {"op": "insert_bgm", "bgm_id": bgm_id, "start": 0, "source_out": 1.5},
        {"op": "insert_bgm", "bgm_id": bgm_id, "start": 1.5, "volume": 0.4, "fade_in": 0.5},
    )

    assert result.revision == 2
    assert [clip.id for clip in result.bgm] == ["b1", "b2"]
    readout = await service.read("demo", timeline_id)
    assert readout.duration == 3.0
    # b1 只有 1.5 秒，默认各 1 秒的淡入淡出按比例缩短；b2 从 1.5 秒起放整首 2 秒，越过 3 秒的末尾，
    # 截到末尾，截断处淡出 1 秒
    assert _rows(readout.bgm) == [
        ("b1", "雨夜", 0.0, 1.5, 0.0, 1.5, 0.25, 0.75, 0.75),
        ("b2", "雨夜", 1.5, 3.0, 0.0, 2.0, 0.4, 0.5, 1.0),
    ]
    assert [issue for issue in readout.issues if issue.code.value == "bgm_missing"] == []


@pytest.mark.usefixtures("three_clips")
async def test_set_and_delete_bgm_change_only_the_named_clip(service: EditTimelineService, bgm_id: str) -> None:
    timeline_id = await _create(service)
    await _edit(
        service,
        timeline_id,
        1,
        {"op": "insert_bgm", "bgm_id": bgm_id, "start": 0, "source_out": 1},
        {"op": "insert_bgm", "bgm_id": bgm_id, "start": 1.5, "source_out": 1},
    )

    await _edit(
        service,
        timeline_id,
        2,
        {
            "op": "set_bgm",
            "clip": "b1",
            "start": 0.2,
            "source_in": 0.5,
            "source_out": 1.5,
            "volume": 0.6,
            "fade_out": 0,
        },
        {"op": "delete_bgm", "clip": "b2"},
    )

    readout = await service.read("demo", timeline_id)
    assert _rows(readout.bgm) == [("b1", "雨夜", 0.2, 1.2, 0.5, 1.5, 0.6, 1.0, 0.0)]


@pytest.mark.usefixtures("three_clips")
@pytest.mark.parametrize(
    ("operation", "field"),
    [
        ({"start": 0.5}, "start"),  # 与 b1（0–1 秒）重叠
        ({"start": 3.0}, "start"),  # 起点在时间线末尾之后
        ({"start": 1.5, "source_in": 1.5, "source_out": 3}, "source_in/source_out"),  # 出点超过 BGM 时长
        ({"start": 1.5, "volume": 1.5}, "volume"),
    ],
)
async def test_invalid_bgm_placement_rejects_the_whole_batch(
    service: EditTimelineService, bgm_id: str, operation: dict[str, Any], field: str
) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "insert_bgm", "bgm_id": bgm_id, "start": 0, "source_out": 1})

    with pytest.raises(EditTimelineError) as raised:
        await _edit(
            service,
            timeline_id,
            2,
            {"op": "set_volume", "clip": "c1", "volume": 0.5},
            {"op": "insert_bgm", "bgm_id": bgm_id, **operation},
        )

    assert raised.value.code == "operation_invalid"
    assert raised.value.params["operation_index"] == 1
    assert raised.value.params["field"] == field
    assert (await service.read("demo", timeline_id)).revision == 2


@pytest.mark.usefixtures("three_clips")
async def test_unknown_bgm_is_rejected_with_where_to_find_valid_ids(service: EditTimelineService) -> None:
    timeline_id = await _create(service)

    with pytest.raises(EditTimelineError) as raised:
        await _edit(service, timeline_id, 1, {"op": "insert_bgm", "bgm_id": "bgm-0000abcd", "start": 0})

    assert raised.value.code == "operation_invalid"
    assert raised.value.params["field"] == "bgm_id"
    assert "list_bgm" in raised.value.params["allowed"]


@pytest.mark.usefixtures("three_clips")
async def test_editing_a_bgm_clip_someone_else_just_changed_conflicts_on_that_clip(
    service: EditTimelineService, bgm_id: str
) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "insert_bgm", "bgm_id": bgm_id, "start": 0, "source_out": 1})
    await _edit(service, timeline_id, 2, {"op": "set_bgm", "clip": "b1", "volume": 0.1})

    with pytest.raises(EditTimelineError) as raised:
        await _edit(service, timeline_id, 2, {"op": "set_bgm", "clip": "b1", "fade_in": 0})

    assert raised.value.code == "revision_conflict"
    assert raised.value.params["conflicting_clip_ids"] == ["b1"]

    # 只改视频片段的旧基线批次不受 BGM 改动影响
    result = await _edit(service, timeline_id, 2, {"op": "set_volume", "clip": "c2", "volume": 0.5})
    assert result.revision == 4


@pytest.mark.usefixtures("three_clips")
async def test_bgm_whose_entry_disappears_reads_as_a_blocking_issue(
    pm: ProjectManager, service: EditTimelineService, bgm_id: str
) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "insert_bgm", "bgm_id": bgm_id, "start": 0, "source_out": 1})
    (pm.get_project_path("demo") / "bgm" / f"{bgm_id}.wav").unlink()

    readout = await service.read("demo", timeline_id)

    issues = [issue for issue in readout.issues if issue.code.value == "bgm_missing"]
    assert [(issue.severity.value, issue.clip_ids, issue.params) for issue in issues] == [
        ("blocking", ("b1",), {"bgm_id": bgm_id})
    ]
    assert readout.bgm[0].name is None
