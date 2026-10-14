"""项目快照读盘入口对 ``drafts/`` 下目录名的处理：只认目录，路径沿用磁盘真实目录名。"""

from __future__ import annotations

import os
from pathlib import Path

from lib.project.project_manager import ProjectManager
from lib.script.draft_quarantine import QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, quarantine_path
from server.services.project.project_events import project_input_stamp, read_project_state

_DRAFT_FILENAME = quarantine_path(Path("."), 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN).name


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
