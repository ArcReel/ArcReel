"""`ProjectManager.locked_episode_script` 的跨锁竞态（TOCTOU）防护测试。

覆盖：
  1. 写回跳过 `sync_episode_from_script`（`sync_project=False`），避免已持项目锁时自死锁；
  2. 解析→写入全程持 `_project_lock`，并发 `update_project` 被挡到临界区之外；
  3. 加锁前后绑定改变（并发 PATCH 改绑）抛 `EpisodeScriptReboundError`，不误写任何脚本；
  4. 整段不挂起（sync 自死锁回归）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from lib.project_manager import EpisodeScriptReboundError, ProjectManager


def _seed(pm: ProjectManager, name: str) -> None:
    """创建项目 + 一个 reference_video 模式的 episode_1 剧本。"""
    pm.create_project(name)
    pm.save_project(
        name,
        {
            "name": name,
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
    )


def test_locked_episode_script_skips_project_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """locked_episode_script 写回时不触发 sync（sync_project=False）；对照 locked_script 会触发。"""
    pm = ProjectManager(tmp_path)
    name = "p-sync"
    _seed(pm, name)

    calls: list[tuple] = []
    monkeypatch.setattr(pm, "sync_episode_from_script", lambda *a, **k: calls.append(a))

    with pm.locked_episode_script(name, lambda _proj: "episode_1.json") as script:
        script["video_units"] = [{"unit_id": "E1U1", "generated_assets": {"status": "pending"}}]
    assert calls == [], "locked_episode_script 不应调用 sync_episode_from_script（会二次取项目锁）"

    # 对照：现有 locked_script 仍会同步到 project.json
    with pm.locked_script(name, "episode_1.json") as script:
        script.setdefault("video_units", [])
    assert len(calls) == 1, "locked_script 应保持触发 sync（默认 sync_project=True）"


def test_locked_episode_script_holds_project_lock_until_write_done(tmp_path: Path) -> None:
    """临界区内持有项目锁：并发 update_project 被阻塞直到 with 块退出。"""
    pm = ProjectManager(tmp_path)
    name = "p-lock"
    _seed(pm, name)

    inside = threading.Event()
    other_done = threading.Event()

    def _other() -> None:
        inside.wait(timeout=5)
        pm.update_project(name, lambda p: p.setdefault("episodes", []))  # 需要 _project_lock
        other_done.set()

    t = threading.Thread(target=_other)
    t.start()
    try:
        with pm.locked_episode_script(name, lambda _proj: "episode_1.json") as script:
            inside.set()  # 此刻已持脚本锁 + 项目锁
            time.sleep(0.3)
            assert not other_done.is_set(), "持项目锁期间 update_project 不应完成"
            script["video_units"] = []
        t.join(timeout=5)
        assert other_done.is_set(), "退出临界区后 update_project 应能完成"
    finally:
        t.join(timeout=5)


def test_locked_episode_script_detects_rebind(tmp_path: Path) -> None:
    """加锁前后解析出的 script_file 不同（并发改绑）→ 抛 EpisodeScriptReboundError，不误写。"""
    pm = ProjectManager(tmp_path)
    name = "p-rebind"
    _seed(pm, name)

    # 有状态解析器：候选解析返回旧绑定，持锁复核返回新绑定
    seq = iter(["scripts/episode_1.json", "scripts/episode_2.json"])

    def _resolver(_project: dict) -> str:
        return next(seq)

    with pytest.raises(EpisodeScriptReboundError):
        with pm.locked_episode_script(name, _resolver) as script:
            script["video_units"] = [{"unit_id": "SHOULD_NOT_PERSIST"}]

    # 旧脚本未被写入（with 体未执行）
    units = pm.load_script(name, "episode_1.json").get("video_units") or []
    assert units == [], "重绑检测命中时不应改写旧脚本"


def test_locked_episode_script_no_self_deadlock(tmp_path: Path) -> None:
    """正常写入路径不挂起（sync 自死锁回归）：写入在超时内完成且生效。"""
    pm = ProjectManager(tmp_path)
    name = "p-nodeadlock"
    _seed(pm, name)

    done = threading.Event()

    def _run() -> None:
        with pm.locked_episode_script(name, lambda _proj: "episode_1.json") as script:
            script.setdefault("video_units", []).append({"unit_id": "E1U1"})
        done.set()

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=10)
    assert done.is_set(), "locked_episode_script 挂起（疑似 sync 自死锁回归）"

    units = pm.load_script(name, "episode_1.json").get("video_units") or []
    assert any(u.get("unit_id") == "E1U1" for u in units), "写入未生效"
