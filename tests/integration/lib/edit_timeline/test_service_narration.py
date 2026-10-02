"""剪辑时间线的旁白落点与旁白检查：在真实临时项目目录上经公开服务命令验证。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from lib.edit_timeline import EditTimelineError, EditTimelineService, RevisionAuthor
from lib.edit_timeline.operations import TimelineOperationAdapter
from lib.edit_timeline.readout import TimelineIssue
from lib.project.project_manager import ProjectManager

type InstallMedia = Callable[[str, float], None]

AGENT = RevisionAuthor(kind="arcreel_agent")
NARRATION_CODES = {"narration_missing", "narration_overrun", "narration_source_collision"}


def _use_delivery(pm: ProjectManager, delivery: str) -> None:
    pm.update_project("demo", lambda project: project.update({"narration_delivery": delivery}))


@pytest.fixture
def four_units(pm: ProjectManager, install_video: InstallMedia, install_narration: InstallMedia) -> None:
    """在 E1U1（台词）、E1U2（画外音）、E1U3（无人声）之后追加画外音单元 E1U4，视频各 1 秒。

    E1U2 的旁白 2.5 秒，从 1 秒起延伸到 c3、c4 上；E1U4 的旁白 1.5 秒，超出 4 秒的时间线末尾。
    """
    script = pm.load_script("demo", "episode_1.json")
    script["video_units"].append({"unit_id": "E1U4", "text": "{雨停了}", "duration_seconds": 4})
    pm.save_script("demo", script, "episode_1.json")
    for unit_id in ("E1U1", "E1U2", "E1U3", "E1U4"):
        install_video(unit_id, 1.0)
    install_narration("E1U2", 2.5)
    install_narration("E1U4", 1.5)


async def _create(service: EditTimelineService) -> str:
    created = await service.create_from_script("demo", episode=1, name="初剪", author=AGENT)
    return created.timeline.id


async def _edit(service: EditTimelineService, timeline_id: str, base_revision: int, *operations: dict[str, Any]):
    return await service.edit(
        "demo",
        timeline_id,
        base_revision=base_revision,
        summary="调整旁白",
        operations=[TimelineOperationAdapter.validate_python(operation) for operation in operations],
        author=AGENT,
    )


def _narration_issues(issues: tuple[TimelineIssue, ...]) -> list[tuple[str, str, str, tuple[str, ...], dict]]:
    return [
        (issue.code.value, issue.severity.value, issue.applies_to.value, issue.clip_ids, issue.params)
        for issue in issues
        if issue.code.value in NARRATION_CODES
    ]


@pytest.mark.usefixtures("four_units")
async def test_overlap_past_end_and_unlowered_source_are_reported(pm: ProjectManager, service: EditTimelineService):
    _use_delivery(pm, "use_tts")
    timeline_id = await _create(service)

    readout = await service.read("demo", timeline_id)

    assert [(clip.id, clip.narration) for clip in readout.clips if clip.narration is not None] == [
        ("c2", readout.clips[1].narration),
        ("c4", readout.clips[3].narration),
    ]
    assert _narration_issues(readout.issues) == [
        (
            "narration_overrun",
            "warning",
            "with_narration",
            ("c2", "c4"),
            {"cause": "next_narration", "next_unit_id": "E1U4", "overlap": 0.5},
        ),
        (
            "narration_source_collision",
            "warning",
            "with_narration",
            ("c2", "c3"),
            {"cause": "source_volume", "other_unit_id": "E1U3", "source_volume": 1.0, "overlap": 1.0},
        ),
        ("narration_overrun", "warning", "with_narration", ("c4",), {"cause": "timeline_end", "overflow": 0.5}),
    ]

    lowered = await _edit(service, timeline_id, 1, {"op": "set_volume", "clip": "c3", "volume": 0.3})

    assert [issue[0] for issue in _narration_issues(lowered.issues)] == ["narration_overrun", "narration_overrun"]


@pytest.mark.usefixtures("four_units")
async def test_narration_extending_onto_a_dialogue_clip_is_reported(pm: ProjectManager, service: EditTimelineService):
    _use_delivery(pm, "use_tts")
    timeline_id = await _create(service)

    result = await _edit(service, timeline_id, 1, {"op": "move", "clip": "c1", "after": "c2"})

    collisions = [issue for issue in _narration_issues(result.issues) if issue[0] == "narration_source_collision"]
    assert collisions[0] == (
        "narration_source_collision",
        "warning",
        "with_narration",
        ("c2", "c1"),
        {"cause": "dialogue", "other_unit_id": "E1U1", "source_volume": 1.0, "overlap": 1.0},
    )


@pytest.mark.usefixtures("four_units")
async def test_place_narration_moves_the_carrier_within_the_unit(pm: ProjectManager, service: EditTimelineService):
    _use_delivery(pm, "use_tts")
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "insert", "unit_id": "E1U2", "after": "c4"})

    result = await _edit(service, timeline_id, 2, {"op": "place_narration", "clip": "c5"})

    assert [(clip.id, clip.carries_narration) for clip in result.clips] == [("c2", False), ("c5", True)]
    readout = await service.read("demo", timeline_id)
    assert [(clip.id, clip.narration) for clip in readout.clips if clip.carries_narration and clip.narration] == [
        ("c4", readout.clips[3].narration),
        ("c5", readout.clips[4].narration),
    ]
    assert (readout.clips[4].narration.start, readout.clips[4].narration.end) == (4.0, 6.5)
    history = await service.list_revisions("demo", timeline_id)
    assert history.revisions[-1].changed_clip_ids == ("c2", "c5")
    # c2 不再承载旁白，它的重叠与相撞随之消失；c4 的旁白（3–4.5 秒）压到 c5 的旁白上，c5 的旁白超出 5 秒的末尾。
    assert [(issue[0], issue[3]) for issue in _narration_issues(readout.issues)] == [
        ("narration_overrun", ("c4", "c5")),
        ("narration_overrun", ("c5",)),
    ]


@pytest.mark.usefixtures("four_units")
async def test_place_narration_rejects_clips_without_narration(pm: ProjectManager, service: EditTimelineService):
    _use_delivery(pm, "use_tts")
    timeline_id = await _create(service)

    with pytest.raises(EditTimelineError) as raised:
        await _edit(
            service,
            timeline_id,
            1,
            {"op": "set_volume", "clip": "c2", "volume": 0.2},
            {"op": "place_narration", "clip": "c1"},
        )

    assert raised.value.code == "operation_invalid"
    assert raised.value.params["operation_index"] == 1
    assert (raised.value.params["clip_id"], raised.value.params["field"]) == ("c1", "clip")
    assert (await service.read("demo", timeline_id)).revision == 1


async def test_missing_narration_audio_blocks_only_the_narrated_version(
    pm: ProjectManager, service: EditTimelineService, install_video: InstallMedia
):
    _use_delivery(pm, "use_tts")
    for unit_id in ("E1U1", "E1U2", "E1U3"):
        install_video(unit_id, 1.0)
    timeline_id = await _create(service)

    readout = await service.read("demo", timeline_id)

    assert [
        (issue.code.value, issue.severity.value, issue.applies_to.value, issue.clip_ids, issue.unit_id)
        for issue in readout.issues
        if issue.code.value in NARRATION_CODES
    ] == [("narration_missing", "blocking", "with_narration", ("c2",), "E1U2")]


@pytest.mark.usefixtures("four_units")
async def test_post_production_projects_report_no_narration_issues(pm: ProjectManager, service: EditTimelineService):
    _use_delivery(pm, "post_production")
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "move", "clip": "c1", "after": "c2"})
    pm.update_project("demo", lambda project: project.update({"narration_delivery": "post_production"}))

    readout = await service.read("demo", timeline_id)

    assert _narration_issues(readout.issues) == []
    assert not any(issue.severity.value == "blocking" for issue in readout.issues)
