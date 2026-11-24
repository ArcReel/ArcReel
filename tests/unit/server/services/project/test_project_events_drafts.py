"""项目快照读盘入口对 ``drafts/`` 下目录名的处理：只认目录，路径沿用磁盘真实目录名。"""

from __future__ import annotations

import os

from lib.episode.episode_paths import SCRIPT_PLAN_FILENAMES
from lib.project.project_manager import ProjectManager
from lib.script.draft_quarantine import QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, quarantine_filename
from server.services.project.project_events import project_input_stamp, read_project_state

_DRAFT_FILENAME = quarantine_filename(QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)


def _make_pm(tmp_path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    return pm


def test_plain_file_named_like_episode_dir_is_ignored(tmp_path):
    pm = _make_pm(tmp_path)
    drafts_root = pm.get_project_path("demo") / "drafts"
    drafts_root.mkdir(parents=True, exist_ok=True)
    (drafts_root / "episode_3").write_text("not a directory", encoding="utf-8")

    stamp = project_input_stamp(pm, "demo")
    state = read_project_state(pm, "demo")

    assert all(not label.startswith("drafts:") for label, _identity in stamp)
    assert state.drafts == {}


def test_zero_padded_episode_dir_draft_change_alters_stamp(tmp_path):
    pm = _make_pm(tmp_path)
    draft = pm.get_project_path("demo") / "drafts" / "episode_007" / _DRAFT_FILENAME
    draft.parent.mkdir(parents=True)
    draft.write_text("{}", encoding="utf-8")
    before = project_input_stamp(pm, "demo")
    assert any(label.startswith("drafts:") for label, _identity in before)
    assert len(read_project_state(pm, "demo").drafts) == 1

    draft.write_text('{"edited": true}', encoding="utf-8")
    os.utime(draft, ns=(1, 1))

    assert project_input_stamp(pm, "demo") != before


def test_formal_script_plan_edit_alters_stamp_and_state_digest(tmp_path):
    pm = _make_pm(tmp_path)
    plan = pm.get_project_path("demo") / "drafts" / "episode_1" / SCRIPT_PLAN_FILENAMES["narration"]
    plan.parent.mkdir(parents=True)
    plan.write_text('{"segments": []}', encoding="utf-8")

    before_stamp = project_input_stamp(pm, "demo")
    before_state = read_project_state(pm, "demo")
    assert any(label.startswith("script_plans:") for label, _identity in before_stamp)
    assert len(before_state.script_plans) == 1

    plan.write_text('{"segments": [{"segment_id": "E1S01"}]}', encoding="utf-8")
    os.utime(plan, ns=(2, 2))

    assert project_input_stamp(pm, "demo") != before_stamp
    assert read_project_state(pm, "demo").script_plans != before_state.script_plans
