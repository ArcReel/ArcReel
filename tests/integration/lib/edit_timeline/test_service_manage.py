"""剪辑时间线管理命令：复制、改名、修订历史、回滚与删除，在真实临时项目目录上经公开服务命令验证。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
)
from lib.edit_timeline import EditTimelineError, EditTimelineService, RevisionAuthor
from lib.edit_timeline.operations import TimelineOperationAdapter
from lib.final_cut.basis import FinalCutVariant, final_cut_artifact_path, final_cut_key
from lib.jianying_draft.basis import jianying_draft_artifact_path, jianying_draft_key
from lib.project.project_activity import ACTIVITY_FILENAME, recorded_project_activity
from lib.project.project_manager import ProjectManager

pytestmark = pytest.mark.usefixtures("three_clips")

AGENT = RevisionAuthor(kind="arcreel_agent")
CREATOR = RevisionAuthor(kind="creator", user_id="u1")


def _ops(*operations: dict[str, Any]) -> list[Any]:
    return [TimelineOperationAdapter.validate_python(operation) for operation in operations]


async def _create(service: EditTimelineService, name: str = "初剪") -> str:
    return (await service.create_from_script("demo", episode=1, name=name, author=CREATOR)).timeline.id


async def _edit(service: EditTimelineService, timeline_id: str, base_revision: int, *operations: dict[str, Any]) -> int:
    result = await service.edit(
        "demo",
        timeline_id,
        base_revision=base_revision,
        summary="调整",
        operations=_ops(*operations),
        author=AGENT,
    )
    return result.revision


async def test_copy_of_a_given_revision_matches_the_source_content(service: EditTimelineService) -> None:
    source_id = await _create(service)
    await _edit(
        service,
        source_id,
        1,
        {"op": "set_volume", "clip": "c2", "volume": 0.6},
        {"op": "set_hold", "clip": "c1", "hold": 0.5},
        {"op": "set_transition", "clip": "c1", "transition": {"type": "dissolve", "duration": 0.4}},
    )
    await _edit(service, source_id, 2, {"op": "delete", "clip": "c3"})
    before = await service.read("demo", source_id, revision=2)

    copied = await service.copy("demo", source_id, name="快节奏版", revision=2, author=AGENT)

    assert copied.timeline.id != source_id
    assert (copied.timeline.name, copied.timeline.episode, copied.revision, copied.latest_revision) == (
        "快节奏版",
        1,
        1,
        1,
    )
    assert copied.clips == before.clips
    assert copied.duration == before.duration
    assert [clip.source_volume for clip in copied.clips] == [1.0, 0.6, 1.0]
    history = await service.list_revisions("demo", copied.timeline.id)
    [only] = history.revisions
    assert (only.author, only.parent) == (AGENT, None)
    assert "初剪" in only.summary
    assert "修订 2" in only.summary
    # 源时间线不受影响。
    assert (await service.read("demo", source_id)).revision == 3


async def test_copy_defaults_to_the_latest_revision_and_keeps_clip_numbering(service: EditTimelineService) -> None:
    source_id = await _create(service)
    await _edit(service, source_id, 1, {"op": "delete", "clip": "c3"})

    copied = await service.copy("demo", source_id, name="副本", author=CREATOR)
    copy_id = copied.timeline.id
    inserted = await service.edit(
        "demo",
        copy_id,
        base_revision=1,
        summary="补回",
        operations=_ops({"op": "insert", "unit_id": "E1U3", "after": None}),
        author=AGENT,
    )

    assert [clip.id for clip in copied.clips] == ["c1", "c2"]
    assert [clip.id for clip in inserted.clips] == ["c4"]


async def test_copy_rejects_unknown_revision_timeline_and_duplicate_names(service: EditTimelineService) -> None:
    source_id = await _create(service)

    with pytest.raises(EditTimelineError) as unknown_revision:
        await service.copy("demo", source_id, name="副本", revision=7, author=AGENT)
    with pytest.raises(EditTimelineError) as unknown_timeline:
        await service.copy("demo", "tl-0000abcd", name="副本", author=AGENT)
    with pytest.raises(EditTimelineError) as duplicate:
        await service.copy("demo", source_id, name=" 初剪 ", author=AGENT)

    assert unknown_revision.value.code == "revision_not_found"
    assert unknown_revision.value.params["latest_revision"] == 1
    assert unknown_timeline.value.code == "timeline_not_found"
    assert duplicate.value.code == "timeline_name_conflict"
    assert len(await service.list_timelines("demo")) == 1


async def test_rename_changes_the_listing_without_adding_a_revision(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    other_id = await _create(service, "快节奏版")

    renamed = await service.rename("demo", timeline_id, name="  完整版 ")
    same_case_change = await service.rename("demo", other_id, name="快节奏版 V2")
    again = await service.rename("demo", timeline_id, name="完整版")

    assert (renamed.name, renamed.revision) == ("完整版", 1)
    assert same_case_change.name == "快节奏版 V2"
    assert again.updated_at == renamed.updated_at
    assert [item.name for item in await service.list_timelines("demo")] == ["完整版", "快节奏版 V2"]
    assert (await service.read("demo", timeline_id)).timeline.name == "完整版"
    assert len((await service.list_revisions("demo", timeline_id)).revisions) == 1


async def test_rename_rejects_duplicates_but_allows_changing_only_the_case(service: EditTimelineService) -> None:
    first_id = await _create(service, "Rough Cut")
    await _create(service, "Fast")

    with pytest.raises(EditTimelineError) as duplicate:
        await service.rename("demo", first_id, name="fast")
    with pytest.raises(EditTimelineError) as invalid:
        await service.rename("demo", first_id, name="   ")
    renamed = await service.rename("demo", first_id, name="ROUGH CUT")

    assert duplicate.value.code == "timeline_name_conflict"
    assert invalid.value.code == "timeline_name_invalid"
    assert renamed.name == "ROUGH CUT"


async def test_revision_history_lists_authors_summaries_and_changed_clips(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await service.edit(
        "demo",
        timeline_id,
        base_revision=1,
        summary="压低 c2 原声",
        operations=_ops({"op": "set_volume", "clip": "c2", "volume": 0.5}),
        author=AGENT,
    )

    history = await service.list_revisions("demo", timeline_id)

    assert (history.timeline.id, history.latest_revision) == (timeline_id, 2)
    first, second = history.revisions
    assert (first.number, first.parent, first.author, first.clip_count, first.restored_from) == (
        1,
        None,
        CREATOR,
        3,
        None,
    )
    assert (second.number, second.parent, second.author, second.summary) == (2, 1, AGENT, "压低 c2 原声")
    assert second.changed_clip_ids == ("c2",)


async def test_restore_appends_a_revision_with_the_target_content(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "set_volume", "clip": "c1", "volume": 0.2})
    await _edit(service, timeline_id, 2, {"op": "delete", "clip": "c3"}, {"op": "move", "clip": "c2", "after": None})
    original = await service.read("demo", timeline_id, revision=1)

    result = await service.restore("demo", timeline_id, revision=1, author=CREATOR)

    assert (result.revision, result.base_revision) == (4, 3)
    assert result.deleted_clip_ids == ()
    assert {clip.id for clip in result.clips} == {"c1", "c2", "c3"}
    assert "修订 1" in result.message
    restored = await service.read("demo", timeline_id)
    assert restored.revision == 4
    assert restored.clips == original.clips
    assert restored.duration == original.duration
    history = await service.list_revisions("demo", timeline_id)
    assert [revision.number for revision in history.revisions] == [1, 2, 3, 4]
    last = history.revisions[-1]
    assert (last.restored_from, last.parent, last.author) == (1, 3, CREATOR)
    assert set(last.changed_clip_ids or ()) == {"c1", "c2", "c3"}
    # 旧修订仍可读：回滚前的内容没有丢。
    assert [clip.id for clip in (await service.read("demo", timeline_id, revision=3)).clips] == ["c2", "c1"]


async def test_restore_reports_clips_removed_by_the_rollback(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "insert", "unit_id": "E1U1", "after": "c3"})

    result = await service.restore("demo", timeline_id, revision=1, author=AGENT)

    assert result.deleted_clip_ids == ("c4",)
    assert result.clips == ()
    assert [clip.id for clip in (await service.read("demo", timeline_id)).clips] == ["c1", "c2", "c3"]


async def test_restore_rejects_missing_revision_and_unchanged_content(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "set_volume", "clip": "c1", "volume": 0.2})
    await _edit(service, timeline_id, 2, {"op": "set_volume", "clip": "c1", "volume": 1.0})

    with pytest.raises(EditTimelineError) as missing:
        await service.restore("demo", timeline_id, revision=9, author=AGENT)
    with pytest.raises(EditTimelineError) as latest:
        await service.restore("demo", timeline_id, revision=3, author=AGENT)
    with pytest.raises(EditTimelineError) as same_content:
        await service.restore("demo", timeline_id, revision=1, author=AGENT)

    assert missing.value.code == "revision_not_found"
    assert latest.value.code == same_content.value.code == "revision_unchanged"
    assert (await service.read("demo", timeline_id)).revision == 3


async def test_edit_based_on_a_revision_before_a_rollback_conflicts_on_the_restored_clips(
    service: EditTimelineService,
) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "set_volume", "clip": "c1", "volume": 0.2})
    await service.restore("demo", timeline_id, revision=1, author=AGENT)  # r3：c1 音量被还原

    with pytest.raises(EditTimelineError) as conflict:
        await _edit(service, timeline_id, 2, {"op": "set_hold", "clip": "c1", "hold": 0.5})
    untouched = await _edit(service, timeline_id, 2, {"op": "set_hold", "clip": "c2", "hold": 0.5})

    assert conflict.value.code == "revision_conflict"
    assert conflict.value.params["conflicting_clip_ids"] == ["c1"]
    assert untouched == 4


async def test_a_reordering_rollback_marks_every_shared_clip_as_changed(service: EditTimelineService) -> None:
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "move", "clip": "c3", "after": None})
    await service.restore("demo", timeline_id, revision=1, author=AGENT)

    history = await service.list_revisions("demo", timeline_id)

    assert set(history.revisions[-1].changed_clip_ids or ()) == {"c1", "c2", "c3"}
    with pytest.raises(EditTimelineError) as conflict:
        await _edit(service, timeline_id, 2, {"op": "set_hold", "clip": "c2", "hold": 0.5})
    assert conflict.value.code == "revision_conflict"


def _register_render_artifacts(pm: ProjectManager, timeline_id: str) -> list[str]:
    """给一条剪辑时间线登记成片与剪映草稿（文件加登记），返回正式路径。"""
    project_dir = pm.get_project_path("demo")
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project_dir))
    final_cut = final_cut_artifact_path(1, timeline_id, FinalCutVariant())
    draft = jianying_draft_artifact_path(1, timeline_id, "without_narration")
    for path, key in (
        (final_cut, final_cut_key(1, timeline_id, FinalCutVariant())),
        (draft, jianying_draft_key(1, timeline_id, "without_narration")),
    ):
        (project_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (project_dir / path).write_bytes(b"rendered")
        (project_dir / path).with_name((project_dir / path).stem + ".render.json").write_text("{}", encoding="utf-8")
        manifest.register(key, artifact_path=path, basis=ArtifactBasis.build("test/render", kind_version=1, inputs={}))
    return [final_cut, draft]


async def test_delete_removes_the_timeline_with_its_render_claims_and_files(
    service: EditTimelineService, pm: ProjectManager
) -> None:
    doomed = await _create(service)
    kept = await _create(service, "保留版")
    doomed_paths = _register_render_artifacts(pm, doomed)
    kept_paths = _register_render_artifacts(pm, kept)
    project_dir = pm.get_project_path("demo")
    keys = {
        doomed: (final_cut_key(1, doomed, FinalCutVariant()), jianying_draft_key(1, doomed, "without_narration")),
        kept: (final_cut_key(1, kept, FinalCutVariant()), jianying_draft_key(1, kept, "without_narration")),
    }
    adapter = ProjectArtifactManifestAdapter(project_dir)
    assert all(adapter.get_entry(key) is not None for pair in keys.values() for key in pair)

    await service.delete("demo", doomed)

    assert [item.id for item in await service.list_timelines("demo")] == [kept]
    with pytest.raises(EditTimelineError) as gone:
        await service.read("demo", doomed)
    assert gone.value.code == "timeline_not_found"
    assert all(adapter.get_entry(key) is None for key in keys[doomed])
    assert all(adapter.get_entry(key) is not None for key in keys[kept])
    assert not any((project_dir / path).exists() for path in doomed_paths)
    assert not (project_dir / "renders" / "episode_1" / doomed).exists()
    assert all((project_dir / path).exists() for path in kept_paths)


async def test_delete_without_render_products_and_unknown_ids(service: EditTimelineService) -> None:
    timeline_id = await _create(service)

    await service.delete("demo", timeline_id)
    with pytest.raises(EditTimelineError) as again:
        await service.delete("demo", timeline_id)

    assert await service.list_timelines("demo") == ()
    assert again.value.code == "timeline_not_found"


async def test_deleted_ids_are_not_reused_by_new_timelines(service: EditTimelineService) -> None:
    first = await _create(service)
    await service.delete("demo", first)

    second = await _create(service)

    assert second != first


_STALE_ACTIVITY = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)

_TIMELINE_WRITES: dict[str, Callable[[EditTimelineService, str], Awaitable[object]]] = {
    "create": lambda service, _timeline_id: _create(service, name="新版"),
    "edit": lambda service, timeline_id: _edit(service, timeline_id, 2, {"op": "delete", "clip": "c3"}),
    "copy": lambda service, timeline_id: service.copy("demo", timeline_id, name="副本", author=CREATOR),
    "rename": lambda service, timeline_id: service.rename("demo", timeline_id, name="改名"),
    "restore": lambda service, timeline_id: service.restore("demo", timeline_id, revision=1, author=CREATOR),
    "delete": lambda service, timeline_id: service.delete("demo", timeline_id),
}


@pytest.mark.parametrize("write", _TIMELINE_WRITES.values(), ids=_TIMELINE_WRITES.keys())
async def test_every_timeline_write_moves_project_activity_forward(
    service: EditTimelineService,
    pm: ProjectManager,
    write: Callable[[EditTimelineService, str], Awaitable[object]],
) -> None:
    # 剪辑时间线是 JSON，修改时间不计入活动；删除后文件也不在了，只能靠写入出口记账。
    timeline_id = await _create(service)
    await _edit(service, timeline_id, 1, {"op": "set_volume", "clip": "c2", "volume": 0.6})
    project_path = pm.get_project_path("demo")
    (project_path / ACTIVITY_FILENAME).write_text(_STALE_ACTIVITY.isoformat(), encoding="utf-8")

    await write(service, timeline_id)

    recorded = recorded_project_activity(project_path)
    assert recorded is not None
    assert recorded > _STALE_ACTIVITY
