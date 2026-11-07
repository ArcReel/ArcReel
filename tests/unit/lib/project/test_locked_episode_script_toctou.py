"""Cross-lock race (TOCTOU) protection for `ProjectManager.locked_episode_script`.

Cover:
  1. Script writes avoid `sync_episode_from_script`, which reacquires the project lock and self-deadlocks, while inline
     synchronization still refreshes project.json `updated_at` under lock.
  2. Hold `_project_lock` throughout resolution and writing, blocking concurrent `update_project` outside the critical section.
  3. Binding changes across lock acquisition (concurrent PATCH rebinding) raise `EpisodeScriptReboundError` without writing any wrong script.
  4. The whole section completes without hanging (sync self-deadlock regression).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from lib.project.project_manager import EpisodeScriptReboundError, ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION


def _seed(pm: ProjectManager, name: str) -> None:
    """Create a project with one reference-to-video episode_1 script."""
    pm.create_project(name)
    pm.save_project(
        name,
        {
            "name": name,
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "generation_mode": "reference_video",
            "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
            "metadata": {"created_at": "2025-01-01", "updated_at": "2025-01-01"},
        },
    )
    pm.save_script(
        name,
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "drama",
            "generation_mode": "reference_video",
            "video_units": [],
        },
        "episode_1.json",
        validate=False,  # Simplified script substitute for locking/TOCTOU tests; structural guards are outside their scope.
    )


def test_locked_episode_script_no_relock_but_refreshes_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """locked_episode_script avoids project-lock-reacquiring sync_episode_from_script while still refreshing project.json."""
    pm = ProjectManager(tmp_path)
    name = "p-sync"
    _seed(pm, name)

    # Set project.json updated_at to an old sentinel, bypassing automatically touching writes.
    from lib.infra.json_io import atomic_write_json

    project_file = pm._get_project_file_path(name)
    project = pm.load_project(name)
    project["metadata"]["updated_at"] = "2020-01-01T00:00:00+00:00"
    atomic_write_json(project_file, project)

    calls: list[tuple] = []
    monkeypatch.setattr(pm, "sync_episode_from_script", lambda *a, **k: calls.append(a))

    with pm.locked_episode_script(name, lambda _proj: "episode_1.json", validate=False) as script:
        script["video_units"] = [{"unit_id": "E1U1", "generated_assets": {"status": "pending"}}]

    # Do not call sync_episode_from_script, which would reacquire the project lock and self-deadlock.
    assert calls == [], "locked_episode_script 不应调用 sync_episode_from_script"
    # Still refresh project.json updated_at inline, matching the old path.
    refreshed = pm.load_project(name)
    assert refreshed["metadata"]["updated_at"] != "2020-01-01T00:00:00+00:00", "project.json updated_at 未刷新"


def test_locked_episode_script_holds_project_lock_until_write_done(tmp_path: Path) -> None:
    """Hold the project lock inside the critical section; concurrent update_project blocks until the with block exits."""
    pm = ProjectManager(tmp_path)
    name = "p-lock"
    _seed(pm, name)

    inside = threading.Event()
    attempting = threading.Event()
    other_done = threading.Event()
    order: list[str] = []

    def _other() -> None:
        inside.wait(timeout=5)
        attempting.set()  # About to acquire the project lock.
        pm.update_project(name, lambda p: p.setdefault("episodes", []))  # Requires _project_lock.
        order.append("other")
        other_done.set()

    t = threading.Thread(target=_other)
    t.start()
    try:
        with pm.locked_episode_script(name, lambda _proj: "episode_1.json", validate=False) as script:
            inside.set()  # Both script and project locks are now held.
            # Wait until the other thread actually requests the lock; incompletion then proves blocking without guessed sleeps.
            assert attempting.wait(timeout=5), "另一线程应已开始申请项目锁"
            assert not other_done.is_set(), "持项目锁期间 update_project 不应完成"
            order.append("critical-section")
            script["video_units"] = []
        t.join(timeout=5)
        assert other_done.is_set(), "退出临界区后 update_project 应能完成"
        assert order == ["critical-section", "other"]
    finally:
        t.join(timeout=5)


def test_locked_episode_script_detects_rebind(tmp_path: Path) -> None:
    """Different script_file resolutions before/after locking (concurrent rebinding) raise EpisodeScriptReboundError without misdirected writes."""
    pm = ProjectManager(tmp_path)
    name = "p-rebind"
    _seed(pm, name)

    # Stateful resolver: candidate resolution yields the old binding; locked recheck yields the new one.
    seq = iter(["scripts/episode_1.json", "scripts/episode_2.json"])

    def _resolver(_project: dict) -> str:
        return next(seq)

    with pytest.raises(EpisodeScriptReboundError), pm.locked_episode_script(name, _resolver, validate=False) as script:
        script["video_units"] = [{"unit_id": "SHOULD_NOT_PERSIST"}]

    # The old script was not written; the with body never ran.
    units = pm.load_script(name, "episode_1.json").get("video_units") or []
    assert units == [], "重绑检测命中时不应改写旧脚本"


def test_locked_episode_script_title_write_mirrors_to_project(tmp_path: Path) -> None:
    """Writing script top-level title lets inline _apply_episode_sync mirror it into project.json episodes[].title;
    later script saves retain the title, as required by PATCH /episodes/{episode} renaming."""
    pm = ProjectManager(tmp_path)
    name = "p-title"
    _seed(pm, name)

    with pm.locked_episode_script(name, lambda _proj: "episode_1.json", validate=False) as script:
        script["title"] = "改后的集名"

    # Persist script top-level title and synchronize its project.json mirror.
    assert pm.load_script(name, "episode_1.json")["title"] == "改后的集名"
    ep = next(e for e in pm.load_project(name)["episodes"] if e["episode"] == 1)
    assert ep["title"] == "改后的集名"

    # Later script saves without explicitly changing title retain it.
    with pm.locked_script(name, "episode_1.json", validate=False) as script:
        script.setdefault("video_units", [])
    assert pm.load_script(name, "episode_1.json")["title"] == "改后的集名"
    ep_after = next(e for e in pm.load_project(name)["episodes"] if e["episode"] == 1)
    assert ep_after["title"] == "改后的集名"


def test_locked_episode_script_no_self_deadlock(tmp_path: Path) -> None:
    """Normal writes do not hang (sync self-deadlock regression): finish within timeout and take effect."""
    pm = ProjectManager(tmp_path)
    name = "p-nodeadlock"
    _seed(pm, name)

    done = threading.Event()

    def _run() -> None:
        with pm.locked_episode_script(name, lambda _proj: "episode_1.json", validate=False) as script:
            script.setdefault("video_units", []).append({"unit_id": "E1U1"})
        done.set()

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=10)
    assert done.is_set(), "locked_episode_script 挂起（疑似 sync 自死锁回归）"

    units = pm.load_script(name, "episode_1.json").get("video_units") or []
    assert any(u.get("unit_id") == "E1U1" for u in units), "写入未生效"
