"""Timeline batch-edit public commands verify operations, per-item errors, and optimistic concurrency on real projects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from lib.edit_timeline import EditTimelineError, EditTimelineService, RevisionAuthor
from lib.edit_timeline.operations import TimelineOperationAdapter

type InstallMedia = Callable[[str, float], None]

AGENT = RevisionAuthor(kind="arcreel_agent")
CREATOR = RevisionAuthor(kind="creator", user_id="u1")


def _ops(*operations: dict[str, Any]) -> list[Any]:
    return [TimelineOperationAdapter.validate_python(operation) for operation in operations]


async def _create(service: EditTimelineService) -> str:
    created = await service.create_from_script("demo", episode=1, name="初剪", author=CREATOR)
    return created.timeline.id


@pytest.mark.usefixtures("three_clips")
async def test_batch_applies_as_one_revision_and_reports_only_affected_clips(service: EditTimelineService) -> None:
    timeline_id = await _create(service)

    result = await service.edit(
        "demo",
        timeline_id,
        base_revision=1,
        summary="压低开场原声并把无人声镜头提前",
        operations=_ops(
            {"op": "set_volume", "clip": "c1", "volume": 0.5},
            {"op": "move", "clip": "c3", "after": None},
            {"op": "set_reason", "clip": "c3", "reason": "先交代环境"},
        ),
        author=AGENT,
    )

    assert (result.revision, result.base_revision, result.concurrent_revisions) == (2, 1, ())
    assert [(clip.id, clip.start) for clip in result.clips] == [("c3", 0.0), ("c1", 0.5)]
    assert (result.clips[1].source_volume, result.clips[0].reason) == (0.5, "先交代环境")
    assert result.duration == 3.0

    readout = await service.read("demo", timeline_id)
    assert readout.revision == 2
    assert [clip.id for clip in readout.clips] == ["c3", "c1", "c2"]
    [summary] = await service.list_timelines("demo")
    assert (summary.update_summary, summary.updated_by) == ("压低开场原声并把无人声镜头提前", AGENT)


async def _edit(service: EditTimelineService, timeline_id: str, base_revision: int, *operations: dict[str, Any]):
    return await service.edit(
        "demo",
        timeline_id,
        base_revision=base_revision,
        summary="调整",
        operations=_ops(*operations),
        author=AGENT,
    )


@pytest.mark.usefixtures("three_clips")
@pytest.mark.parametrize(
    ("operation", "clip_id", "field", "allowed"),
    [
        ({"op": "set_volume", "clip": "c2", "volume": 1.5}, "c2", "source_volume", "0–1"),
        ({"op": "set_hold", "clip": "c2", "hold": 12}, "c2", "hold", "0–10.0"),
        ({"op": "set_hold", "clip": "c2", "hold": 1e308}, "c2", "hold", "0–10.0"),
        (
            {"op": "set_trim", "clip": "c2", "trim": {"source_in": 0.2, "source_out": 1.8}},
            "c2",
            "trim",
            "0 ≤ source_in < source_out ≤ 1.5，且至少保留 0.1 秒",
        ),
        (
            {"op": "set_trim", "clip": "c2", "trim": {"source_in": 0, "source_out": 1e308}},
            "c2",
            "trim",
            "0 ≤ source_in < source_out ≤ 1.5，且至少保留 0.1 秒",
        ),
        (
            {"op": "set_transition", "clip": "c3", "transition": {"type": "dissolve", "duration": 0.5}},
            "c3",
            "transition",
            "不是最后一个的片段",
        ),
        (
            {"op": "set_transition", "clip": "c1", "transition": {"type": "dissolve", "duration": 3}},
            "c1",
            "transition.duration",
            "0.1–2.0",
        ),
        (
            {"op": "set_transition", "clip": "c1", "transition": {"type": "dissolve", "duration": 1e308}},
            "c1",
            "transition.duration",
            "0.1–2.0",
        ),
        ({"op": "move", "clip": "c9", "after": None}, "c9", "clip", "当前修订里的片段 ID"),
        ({"op": "insert", "unit_id": "E9U9", "after": "c1"}, None, "unit_id", "集（id=1）脚本中的视频单元 ID"),
    ],
)
async def test_one_invalid_operation_rejects_the_whole_batch_and_locates_it(
    service: EditTimelineService, operation: dict[str, Any], clip_id: str | None, field: str, allowed: str
) -> None:
    timeline_id = await _create(service)

    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(service, timeline_id, 1, {"op": "set_volume", "clip": "c1", "volume": 0.2}, operation)

    error = excinfo.value
    assert error.code == "operation_invalid"
    assert (error.params["operation_index"], error.params["clip_id"]) == (1, clip_id)
    assert (error.params["field"], error.params["allowed"]) == (field, allowed)
    assert "第 2 条操作" in str(error)
    readout = await service.read("demo", timeline_id)
    assert (readout.revision, readout.clips[0].source_volume) == (1, 1.0)


@pytest.mark.usefixtures("three_clips")
async def test_stale_base_still_applies_when_its_clips_were_untouched(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "set_volume", "clip": "c3", "volume": 0.4})

    result = await _edit(service, timeline_id, 1, {"op": "set_hold", "clip": "c1", "hold": 0.5})

    assert (result.revision, result.base_revision) == (3, 1)
    assert [(item.number, item.author) for item in result.concurrent_revisions] == [(2, AGENT)]
    assert "已有他人写入修订 2" in result.message
    readout = await service.read("demo", timeline_id)
    # Preserve concurrent edits and apply this batch against the latest revision.
    assert [(clip.source_volume, clip.hold) for clip in readout.clips] == [(1.0, 0.5), (0.3, 0.0), (0.4, 0.0)]


@pytest.mark.usefixtures("three_clips")
@pytest.mark.parametrize(
    ("concurrent", "mine", "conflicting"),
    [
        # Another user edited the same clip this batch targets.
        ({"op": "set_volume", "clip": "c2", "volume": 0.4}, {"op": "set_hold", "clip": "c2", "hold": 1}, ["c2"]),
        # Another user deleted this batch’s anchor clip.
        ({"op": "delete", "clip": "c2"}, {"op": "insert", "unit_id": "E1U3", "after": "c2"}, ["c2"]),
        # Another user moved this batch’s target clip.
        ({"op": "move", "clip": "c3", "after": None}, {"op": "set_reason", "clip": "c3", "reason": "收尾"}, ["c3"]),
        # Batch incidentally clears c1 transition, just modified by another user.
        (
            {"op": "set_transition", "clip": "c1", "transition": {"type": "fade_black", "duration": 0.4}},
            {"op": "insert", "unit_id": "E1U3", "after": "c1"},
            ["c1"],
        ),
        # Another user inserted after c2; this batch’s c2→c3 transition cut no longer exists.
        (
            {"op": "insert", "unit_id": "E1U1", "after": "c2"},
            {"op": "set_transition", "clip": "c2", "transition": {"type": "dissolve", "duration": 0.2}},
            ["c2"],
        ),
    ],
)
async def test_stale_base_is_rejected_with_the_conflicting_clips(
    service: EditTimelineService, concurrent: dict[str, Any], mine: dict[str, Any], conflicting: list[str]
) -> None:
    timeline_id = await _create(service)
    await _edit(
        service,
        timeline_id,
        1,
        {"op": "set_transition", "clip": "c1", "transition": {"type": "dissolve", "duration": 0.2}},
    )
    await _edit(service, timeline_id, 2, concurrent)

    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(service, timeline_id, 2, mine)

    assert excinfo.value.code == "revision_conflict"
    assert excinfo.value.params["latest_revision"] == 3
    assert excinfo.value.params["conflicting_clip_ids"] == conflicting
    assert (await service.read("demo", timeline_id)).revision == 3


@pytest.mark.usefixtures("three_clips")
async def test_unknown_base_revision_is_rejected(service: EditTimelineService) -> None:
    timeline_id = await _create(service)

    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(service, timeline_id, 2, {"op": "set_volume", "clip": "c1", "volume": 0.5})

    assert excinfo.value.code == "revision_not_found"


async def test_trim_is_voided_after_the_current_video_version_changes(
    service: EditTimelineService, install_video: InstallMedia
) -> None:
    install_video("E1U1", 2.0)
    install_video("E1U2", 1.0)
    install_video("E1U3", 1.0)
    timeline_id = await _create(service)

    trimmed = await _edit(
        service, timeline_id, 1, {"op": "set_trim", "clip": "c1", "trim": {"source_in": 0.25, "source_out": 1.5}}
    )
    [clip] = trimmed.clips
    assert (clip.duration, clip.trim and (clip.trim.source_in, clip.trim.source_out, clip.trim.basis_version)) == (
        1.25,
        (0.25, 1.5, 1),
    )
    assert trimmed.issues == ()

    install_video("E1U1", 3.0)
    readout = await service.read("demo", timeline_id)

    assert [(issue.code, issue.severity, issue.clip_ids) for issue in readout.issues] == [
        ("trim_ignored", "info", ("c1",))
    ]
    assert readout.issues[0].params == {"basis_version": 1, "current_version": 2}
    # Invalidated trims temporarily use the full video.
    assert (readout.clips[0].duration, readout.duration) == (3.0, 5.0)


async def test_clips_report_the_full_length_of_their_current_video(
    service: EditTimelineService, install_video: InstallMedia
) -> None:
    install_video("E1U1", 2.0)
    install_video("E1U3", 1.0)
    timeline_id = await _create(service)

    trimmed = await _edit(
        service, timeline_id, 1, {"op": "set_trim", "clip": "c1", "trim": {"source_in": 0.25, "source_out": 1.5}}
    )
    readout = await service.read("demo", timeline_id)

    assert [(clip.duration, clip.source_duration) for clip in trimmed.clips] == [(1.25, 2.0)]
    # Clips without available videos occupy arranged duration, with unknown full-source duration.
    assert [(clip.id, clip.source_duration) for clip in readout.clips] == [("c1", 2.0), ("c2", None), ("c3", 1.0)]


@pytest.mark.usefixtures("three_clips")
async def test_total_duration_adds_trims_and_holds_and_ignores_transitions(service: EditTimelineService) -> None:
    timeline_id = await _create(service)

    result = await _edit(
        service,
        timeline_id,
        1,
        {"op": "set_trim", "clip": "c2", "trim": {"source_in": 0.5, "source_out": 1.25}},
        {"op": "set_hold", "clip": "c3", "hold": 2.5},
        {"op": "set_transition", "clip": "c1", "transition": {"type": "dissolve", "duration": 0.5}},
        {"op": "set_transition", "clip": "c2", "transition": {"type": "push_left", "duration": 0.3}},
    )

    # 1.0 + 0.75 + (0.5 + 2.5)
    assert result.duration == 4.75
    assert [(clip.id, clip.start, clip.duration) for clip in result.clips] == [
        ("c1", 0.0, 1.0),
        ("c2", 1.0, 0.75),
        ("c3", 1.75, 3.0),
    ]
    assert [(issue.code, issue.severity, issue.clip_ids, issue.params) for issue in result.issues] == [
        ("hold_too_long", "warning", ("c3",), {"hold": 2.5, "limit": 2.0})
    ]


@pytest.mark.usefixtures("three_clips")
async def test_transitions_must_fit_within_the_clips_they_straddle(service: EditTimelineService) -> None:
    timeline_id = await _create(service)

    # c3 lasts 0.5 seconds; incoming/outgoing transitions each use half, allowing at most 1.0-second incoming transition.
    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(
            service,
            timeline_id,
            1,
            {"op": "set_transition", "clip": "c2", "transition": {"type": "dissolve", "duration": 1.2}},
            {"op": "set_reason", "clip": "c1", "reason": "开场"},
        )

    assert excinfo.value.code == "operation_invalid"
    assert (excinfo.value.params["operation_index"], excinfo.value.params["clip_id"]) == (0, "c3")
    assert excinfo.value.params["allowed"] == "两侧转场时长之和 ≤ 1.0"


async def test_inserted_clips_reuse_units_with_voice_defaults_and_keep_one_narration_carrier(
    service: EditTimelineService, install_video: InstallMedia, install_narration: InstallMedia
) -> None:
    for unit_id in ("E1U1", "E1U2", "E1U3"):
        install_video(unit_id, 1.0)
    install_narration("E1U2", 0.8)
    timeline_id = await _create(service)

    result = await _edit(
        service,
        timeline_id,
        1,
        {"op": "insert", "unit_id": "E1U2", "after": "c3", "trim": {"source_in": 0.5, "source_out": 1.0}},
        {"op": "insert", "unit_id": "E1U1", "after": None, "source_volume": 0.6, "reason": "冷开场"},
        {"op": "delete", "clip": "c2"},
    )

    assert result.deleted_clip_ids == ("c2",)
    assert [(clip.id, clip.unit_id, clip.source_volume, clip.carries_narration) for clip in result.clips] == [
        ("c5", "E1U1", 0.6, False),
        ("c4", "E1U2", 0.3, True),
    ]
    readout = await service.read("demo", timeline_id)
    assert [clip.id for clip in readout.clips] == ["c5", "c1", "c3", "c4"]
    # Deleted narration-host clips reattach narration to the unit’s remaining clip, starting there.
    narration = readout.clips[3].narration
    assert narration is not None
    assert (narration.start, narration.end) == (3.0, 3.8)

    # Never reuse IDs: deleted c2 cannot be allocated again.
    again = await _edit(service, timeline_id, 2, {"op": "insert", "unit_id": "E1U3", "after": "c4"})
    assert [clip.id for clip in again.clips] == ["c6"]


@pytest.mark.usefixtures("three_clips")
async def test_cuts_whose_neighbours_change_fall_back_to_hard_cuts(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(
        service,
        timeline_id,
        1,
        {"op": "set_transition", "clip": "c1", "transition": {"type": "dissolve", "duration": 0.4}},
        {"op": "set_transition", "clip": "c2", "transition": {"type": "wipe_up", "duration": 0.4}},
    )

    result = await _edit(service, timeline_id, 2, {"op": "move", "clip": "c2", "after": "c3"})

    # Both c1→c2 and c2→c3 cuts disappear, reverting to hard cuts; return all batch-affected clips.
    assert [(clip.id, clip.transition_to_next) for clip in result.clips] == [("c1", None), ("c2", None)]
    readout = await service.read("demo", timeline_id)
    assert [clip.id for clip in readout.clips] == ["c1", "c3", "c2"]
    assert all(clip.transition_to_next is None for clip in readout.clips)


@pytest.mark.usefixtures("three_clips")
async def test_stale_base_detects_changes_even_when_later_restored(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "set_volume", "clip": "c1", "volume": 0.5})
    await _edit(service, timeline_id, 2, {"op": "set_volume", "clip": "c1", "volume": 1.0})
    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(service, timeline_id, 1, {"op": "set_hold", "clip": "c1", "hold": 0.5})
    assert excinfo.value.params["latest_revision"] == 3
    assert excinfo.value.params["conflicting_clip_ids"] == ["c1"]
    assert (await service.read("demo", timeline_id)).revision == 3


@pytest.mark.usefixtures("three_clips")
@pytest.mark.parametrize("moved", ["c1", "c2"])
async def test_adjacent_swap_conflicts_only_with_the_clip_actually_moved(
    service: EditTimelineService, moved: str
) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "move", "clip": moved, "after": "c2" if moved == "c1" else None})
    untouched = "c2" if moved == "c1" else "c1"
    result = await _edit(service, timeline_id, 1, {"op": "set_hold", "clip": untouched, "hold": 0.5})
    assert result.revision == 3
    assert [revision.number for revision in result.concurrent_revisions] == [2]
    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(service, timeline_id, 1, {"op": "set_reason", "clip": moved, "reason": "移过位置"})
    assert excinfo.value.params["conflicting_clip_ids"] == [moved]
    assert excinfo.value.params["latest_revision"] == 3


@pytest.mark.usefixtures("three_clips")
async def test_stale_move_cannot_erase_a_concurrently_added_transition(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(
        service,
        timeline_id,
        1,
        {"op": "set_transition", "clip": "c1", "transition": {"type": "dissolve", "duration": 0.2}},
    )
    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(service, timeline_id, 1, {"op": "move", "clip": "c2", "after": "c3"})
    assert excinfo.value.params["conflicting_clip_ids"] == ["c1"]
    current = await service.read("demo", timeline_id)
    assert current.revision == 2
    assert current.clips[0].transition_to_next is not None


@pytest.mark.usefixtures("three_clips")
@pytest.mark.parametrize("moved", ["c1", "c2"])
async def test_adjacent_swap_rejects_an_edit_of_the_actual_move_target(
    service: EditTimelineService, moved: str
) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "move", "clip": moved, "after": "c2" if moved == "c1" else None})
    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(service, timeline_id, 1, {"op": "set_hold", "clip": moved, "hold": 0.5})
    assert excinfo.value.code == "revision_conflict"
    assert excinfo.value.params["conflicting_clip_ids"] == [moved]
    assert (await service.read("demo", timeline_id)).revision == 2


@pytest.mark.usefixtures("three_clips")
async def test_stale_delete_cannot_clear_a_new_clips_transition(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(
        service,
        timeline_id,
        1,
        {"op": "insert", "unit_id": "E1U1", "after": "c1", "transition_to_next": {"type": "dissolve", "duration": 0.2}},
    )
    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(service, timeline_id, 1, {"op": "delete", "clip": "c2"})
    assert excinfo.value.params["latest_revision"] == 2
    assert excinfo.value.params["conflicting_clip_ids"] == ["c4"]
    current = await service.read("demo", timeline_id)
    assert current.revision == 2
    assert current.clips[1].transition_to_next is not None


@pytest.mark.usefixtures("three_clips")
@pytest.mark.parametrize(
    "concurrent",
    [
        {"op": "insert", "unit_id": "E1U3", "after": "c2"},
        {"op": "delete", "clip": "c2"},
        {"op": "move", "clip": "c3", "after": "c1"},
    ],
)
async def test_stale_unrelated_edit_survives_multiple_structural_revisions(
    service: EditTimelineService, concurrent: dict[str, Any]
) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, concurrent)
    await _edit(service, timeline_id, 2, {"op": "set_reason", "clip": "c3", "reason": "环境镜头"})
    before = await service.read("demo", timeline_id)
    result = await _edit(service, timeline_id, 1, {"op": "set_hold", "clip": "c1", "hold": 0.5})
    assert result.revision == 4
    assert [revision.number for revision in result.concurrent_revisions] == [2, 3]
    assert "2, 3" in result.message
    current = await service.read("demo", timeline_id)
    assert [clip.id for clip in current.clips] == [clip.id for clip in before.clips]
    assert current.duration == before.duration + 0.5
    assert next(clip for clip in current.clips if clip.id == "c3").reason == "环境镜头"


@pytest.mark.usefixtures("three_clips")
async def test_transition_resets_follow_each_operation_and_can_be_set_afterwards(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(
        service,
        timeline_id,
        1,
        {"op": "set_transition", "clip": "c1", "transition": {"type": "dissolve", "duration": 0.2}},
        {"op": "set_transition", "clip": "c2", "transition": {"type": "dissolve", "duration": 0.2}},
    )
    result = await _edit(
        service,
        timeline_id,
        2,
        {"op": "move", "clip": "c2", "after": "c3"},
        {"op": "set_transition", "clip": "c1", "transition": {"type": "fade_black", "duration": 0.3}},
        {"op": "insert", "unit_id": "E1U1", "after": "c1"},
        {"op": "set_transition", "clip": "c1", "transition": {"type": "wipe_up", "duration": 0.4}},
        {"op": "delete", "clip": "c3"},
    )
    current = await service.read("demo", timeline_id)
    assert [clip.id for clip in current.clips] == ["c1", "c4", "c2"]
    assert current.clips[0].transition_to_next is not None
    assert current.clips[0].transition_to_next.type == "wipe_up"
    assert all(clip.transition_to_next is None for clip in current.clips[1:])
    assert result.duration == 3.5
    subsequent = await _edit(
        service,
        timeline_id,
        3,
        {"op": "set_transition", "clip": "c4", "transition": {"type": "dissolve", "duration": 0.2}},
    )
    assert subsequent.clips[0].transition_to_next is not None
    assert subsequent.duration == 3.5


@pytest.mark.usefixtures("three_clips")
@pytest.mark.parametrize(
    "operation",
    [
        {"op": "move", "clip": "c1", "after": None},
        {"op": "set_volume", "clip": "c1", "volume": 1.0},
        {"op": "set_trim", "clip": "c1", "trim": None},
    ],
)
async def test_idempotent_operations_do_not_create_false_conflicts(
    service: EditTimelineService, operation: dict[str, Any]
) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, operation)
    result = await _edit(service, timeline_id, 1, {"op": "set_reason", "clip": "c1", "reason": "开场"})
    assert result.revision == 3
    assert result.clips[0].reason == "开场"
    assert [revision.number for revision in result.concurrent_revisions] == [2]


@pytest.mark.usefixtures("three_clips")
async def test_changes_restored_within_a_single_batch_still_conflict(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(
        service,
        timeline_id,
        1,
        {"op": "set_volume", "clip": "c1", "volume": 0.5},
        {"op": "set_volume", "clip": "c1", "volume": 1.0},
    )
    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(service, timeline_id, 1, {"op": "set_hold", "clip": "c1", "hold": 0.5})
    assert excinfo.value.params["conflicting_clip_ids"] == ["c1"]
    assert (await service.read("demo", timeline_id)).revision == 2


@pytest.mark.usefixtures("three_clips")
async def test_trim_out_point_one_millisecond_past_the_end_snaps_to_the_video_end(
    service: EditTimelineService,
) -> None:
    timeline_id = await _create(service)
    before = await service.read("demo", timeline_id)
    whole = next(clip for clip in before.clips if clip.id == "c2").duration

    result = await _edit(
        service,
        timeline_id,
        1,
        {"op": "set_trim", "clip": "c2", "trim": {"source_in": 0.2, "source_out": round(whole + 0.001, 3)}},
    )

    [clip] = [clip for clip in result.clips if clip.id == "c2"]
    assert clip.trim is not None
    assert (clip.trim.source_in, clip.trim.source_out) == (0.2, whole)
    assert clip.duration == round(whole - 0.2, 3)


@pytest.mark.usefixtures("three_clips")
async def test_a_moved_cut_is_reported_as_a_conflict_before_the_new_neighbour_is_validated(
    service: EditTimelineService,
) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "insert", "unit_id": "E1U3", "after": "c1"})

    # c1→c2 supports 1.2-second transitions; concurrent c4 lasts 0.5, but the observed cut vanished, so report conflict first.
    with pytest.raises(EditTimelineError) as excinfo:
        await _edit(
            service,
            timeline_id,
            1,
            {"op": "set_transition", "clip": "c1", "transition": {"type": "dissolve", "duration": 1.2}},
        )

    assert excinfo.value.code == "revision_conflict"
    assert excinfo.value.params["conflicting_clip_ids"] == ["c1"]


@pytest.mark.usefixtures("three_clips")
async def test_stale_transition_is_checked_against_the_latest_neighbour_lengths(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(
        service, timeline_id, 1, {"op": "set_trim", "clip": "c2", "trim": {"source_in": 0.5, "source_out": 0.75}}
    )
    await _edit(
        service, timeline_id, 2, {"op": "set_trim", "clip": "c2", "trim": {"source_in": 0.0, "source_out": 1.5}}
    )

    # Baseline c2 lasts 0.25, insufficient for 0.8 transition; latest revision restored full c2, making this batch valid.
    result = await _edit(
        service,
        timeline_id,
        2,
        {"op": "set_transition", "clip": "c1", "transition": {"type": "dissolve", "duration": 0.8}},
    )
    assert result.revision == 4
    current = await service.read("demo", timeline_id)
    assert current.clips[0].transition_to_next is not None
