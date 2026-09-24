import copy

import pytest

from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD
from lib.script.script_skeleton import (
    SKELETON_ANCHOR_TYPES,
    SKELETON_ENTITY_TYPES,
    SKELETON_ITEM_LABEL_KEYS,
    SKELETONS,
)
from server.services.project.project_state_projection import ProjectState, build_snapshot, diff_snapshots

_DERIVATIVE_SPECS = [spec for spec in ASSET_SPECS.values() if spec.supports_derivatives]

# 各骨架种类在数据形状上的取证写法：content_mode 与条目数组键共同决定 resolve_script_kind 的判别。
_KIND_CONTENT_MODES = {
    "segments": "narration",
    "scenes": "drama",
    "shots": "ad",
    "video_units": "narration",
}


def _project(**overrides) -> dict:
    project = {
        "title": "Demo",
        "content_mode": "narration",
        "style": "Anime",
        "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        "metadata": {"created_at": "t0", "updated_at": "t0"},
    }
    for spec in ASSET_SPECS.values():
        project[spec.bucket_key] = {}
    project.update(overrides)
    return project


def _pending_assets() -> dict:
    return {"storyboard_image": None, "video_clip": None, "video_uri": None, "status": "pending"}


def _script(kind: str, items: list[dict], **fields) -> dict:
    return {
        "episode": 1,
        "title": "第一集",
        "content_mode": _KIND_CONTENT_MODES[kind],
        kind: items,
        "metadata": {"total_scenes": len(items)},
        **fields,
    }


def _item(kind: str, item_id: str, **fields) -> dict:
    return {SKELETONS[kind].id_field: item_id, "generated_assets": _pending_assets(), **fields}


def _diff(before: ProjectState, after: ProjectState) -> list[dict]:
    return diff_snapshots(build_snapshot(before), build_snapshot(after))


def _triples(changes: list[dict]) -> list[tuple]:
    return [(c["entity_type"], c["action"], c["entity_id"]) for c in changes]


@pytest.mark.parametrize("spec", list(ASSET_SPECS.values()), ids=list(ASSET_SPECS))
def test_asset_lifecycle_follows_the_asset_type_registry(spec):
    empty = ProjectState(project=_project(), scripts={})
    added = ProjectState(project=_project(**{spec.bucket_key: {"甲": {"description": "d"}}}), scripts={})
    # 任意字段的变化都算更新，包括资产类型表未声明、由系统戳写的字段。
    edited = ProjectState(
        project=_project(**{spec.bucket_key: {"甲": {"description": "d", "system_stamped_at": "t1"}}}),
        scripts={},
    )

    focus = {"pane": spec.bucket_key, "anchor_type": spec.asset_type, "anchor_id": "甲"}
    label_key = f"named_entity_{spec.asset_type}"

    [created] = _diff(empty, added)
    assert (created["entity_type"], created["action"], created["entity_id"]) == (spec.asset_type, "created", "甲")
    assert (created["label_key"], created["label_params"], created["focus"]) == (label_key, {"id": "甲"}, focus)
    assert created["important"] is True

    [updated] = _diff(added, edited)
    assert (updated["action"], updated["focus"], updated["important"]) == ("updated", focus, True)

    [deleted] = _diff(edited, empty)
    assert (deleted["action"], deleted["focus"], deleted["important"]) == ("deleted", None, False)


@pytest.mark.parametrize("spec", _DERIVATIVE_SPECS, ids=[spec.asset_type for spec in _DERIVATIVE_SPECS])
def test_derivatives_are_separate_events_addressed_by_owner_slash_name(spec):
    def state(derivatives: dict) -> ProjectState:
        entry = {"description": "本体", DERIVATIVES_FIELD: derivatives}
        return ProjectState(project=_project(**{spec.bucket_key: {"甲": entry}}), scripts={})

    bare = state({})
    added = state({"战甲": {"description": "穿战甲"}})
    edited = state({"战甲": {"description": "穿战甲", spec.sheet_field: "sheet.png"}})

    focus = {"pane": spec.bucket_key, "anchor_type": spec.asset_type, "anchor_id": "甲"}
    label_key = f"named_entity_{spec.asset_type}_derivative"

    # 本体条目比对时排除衍生表：衍生的增删改不连带报本体更新。
    [created] = _diff(bare, added)
    assert _triples([created]) == [(spec.asset_type, "created", "甲/战甲")]
    assert (created["label_key"], created["label_params"], created["focus"]) == (label_key, {"id": "甲/战甲"}, focus)

    [updated] = _diff(added, edited)
    assert _triples([updated]) == [(spec.asset_type, "updated", "甲/战甲")]
    assert updated["focus"] == focus

    [deleted] = _diff(edited, bare)
    assert _triples([deleted]) == [(spec.asset_type, "deleted", "甲/战甲")]
    assert deleted["focus"] is None


@pytest.mark.parametrize("kind", sorted(SKELETONS))
def test_script_item_lifecycle_follows_the_skeleton_registry(kind):
    def state(items: list[dict]) -> ProjectState:
        return ProjectState(project=_project(), scripts={"episode_1.json": _script(kind, items)})

    base = state([_item(kind, "X1")])
    focus = {"pane": "episode", "episode": 1, "anchor_type": SKELETON_ANCHOR_TYPES[kind], "anchor_id": "X1"}

    [created] = _diff(state([]), base)
    assert _triples([created]) == [(SKELETON_ENTITY_TYPES[kind], "created", "X1")]
    assert (created["label_key"], created["focus"]) == (SKELETON_ITEM_LABEL_KEYS[kind], focus)
    assert (created["script_file"], created["episode"]) == ("episode_1.json", 1)

    [deleted] = _diff(base, state([]))
    assert (deleted["action"], deleted["focus"]) == ("deleted", None)

    # 条目正文取整条：任意字段的编辑都算更新。
    for field, value in (("note", "备注"), ("needs_replan", True), ("utterances", [{"kind": "voiceover"}])):
        [updated] = _diff(base, state([_item(kind, "X1", **{field: value})]))
        assert (updated["action"], updated["focus"]) == ("updated", focus), field

    storyboard = _item(kind, "X1")
    storyboard["generated_assets"]["storyboard_image"] = "storyboards/X1.png"
    [ready] = _diff(base, state([storyboard]))
    assert (ready["action"], ready["focus"]) == ("storyboard_ready", focus)

    video = copy.deepcopy(storyboard)
    video["generated_assets"]["video_clip"] = "videos/X1.mp4"
    assert _triples(_diff(state([storyboard]), state([video]))) == [(SKELETON_ENTITY_TYPES[kind], "video_ready", "X1")]

    # generated_assets 的其余变化归显式事件，快照差分不报。
    status_only = copy.deepcopy(video)
    status_only["generated_assets"]["status"] = "completed"
    assert _diff(state([video]), state([status_only])) == []


def test_residual_item_array_of_another_skeleton_does_not_vote():
    def state(shot_clip: str | None, unit_clip: str | None) -> ProjectState:
        shot = _item("shots", "E1S01")
        shot["generated_assets"]["video_clip"] = shot_clip
        unit = _item("video_units", "E1U01")
        unit["generated_assets"]["video_clip"] = unit_clip
        return ProjectState(
            project=_project(), scripts={"episode_1.json": _script("shots", [shot], video_units=[unit])}
        )

    # 残留数组的产物回写既不报条目事件，也不当作剧本层字段报集更新；当前骨架照常报就绪。
    changes = _diff(state(None, None), state("videos/E1S01.mp4", "videos/E1U01.mp4"))

    assert _triples(changes) == [("shot", "video_ready", "E1S01")]


def test_project_settings_are_everything_outside_assets_episodes_overview_and_metadata():
    before = ProjectState(project=_project(), scripts={})

    for key, value in (("style", "Realistic"), ("planning_cursor", {"offset": 3}), ("aspect_ratio", "9:16")):
        [change] = _diff(before, ProjectState(project=_project(**{key: value}), scripts={}))
        assert _triples([change]) == [("project", "updated", "project")], key
        assert (change["label_key"], change["focus"], change["important"]) == ("project_settings", None, False)


def test_overview_is_compared_whole():
    before = ProjectState(project=_project(overview={"synopsis": "s"}), scripts={})
    after = ProjectState(project=_project(overview={"synopsis": "s", "hook": "h"}), scripts={})

    assert _triples(_diff(before, after)) == [("overview", "updated", "overview")]


def test_episode_entries_are_compared_whole():
    def state(episodes) -> ProjectState:
        return ProjectState(project=_project(episodes=episodes), scripts={})

    first = {"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}
    second = {"episode": 2, "title": "第二集", "script_file": "scripts/episode_2.json"}

    [created] = _diff(state([first]), state([first, second]))
    assert _triples([created]) == [("episode", "created", "2")]
    assert (created["label_key"], created["label_params"], created["episode"]) == ("episode", {"episode": 2}, 2)

    [updated] = _diff(state([first]), state([{**first, "source_range": [0, 120], "hook": "悬念"}]))
    assert _triples([updated]) == [("episode", "updated", "1")]

    assert _triples(_diff(state([first, second]), state([first]))) == [("episode", "deleted", "2")]
    # episodes 为 null 的项目照常成快照
    assert _triples(_diff(state(None), state([first]))) == [("episode", "created", "1")]


def test_script_level_changes_report_their_episode_once():
    def state(hook: str | None, episode_title: str = "第一集") -> ProjectState:
        project = _project(episodes=[{"episode": 1, "title": episode_title, "script_file": "scripts/episode_1.json"}])
        scripts = (
            {} if hook is None else {"episode_1.json": _script("segments", [_item("segments", "E1S01")], hook=hook)}
        )
        return ProjectState(project=project, scripts=scripts)

    [script_only] = _diff(state("旧"), state("新"))
    assert _triples([script_only]) == [("episode", "updated", "1")]
    assert (script_only["script_file"], script_only["episode"]) == ("scripts/episode_1.json", 1)

    # 同一次写入同时改了集条目与剧本层字段：同一集只报一条。
    assert _triples(_diff(state("旧"), state("新", "改名"))) == [("episode", "updated", "1")]
    # 剧本文件首次生成或被删除，集条目不变：报所属集更新，不逐条报条目增删。
    assert _triples(_diff(state(None), state("新"))) == [("episode", "updated", "1")]
    assert _triples(_diff(state("旧"), state(None))) == [("episode", "updated", "1")]


def test_metadata_changes_are_not_project_events():
    before = ProjectState(
        project=_project(),
        scripts={"episode_1.json": _script("segments", [_item("segments", "E1S01")])},
    )
    after = copy.deepcopy(before)
    after.project["metadata"]["updated_at"] = "t1"
    after.scripts["episode_1.json"]["metadata"]["total_scenes"] = 9

    previous, current = build_snapshot(before), build_snapshot(after)

    assert previous.fingerprint == current.fingerprint
    assert diff_snapshots(previous, current) == []
