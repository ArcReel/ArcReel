"""`ProjectManager.locked_episode_script` 的跨锁竞态（TOCTOU）防护测试。

覆盖：
  1. 写脚本不经会二次取项目锁的 `sync_episode_from_script`（避免自死锁），但仍在持锁内联
     刷新 project.json 的 `updated_at`；
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
        validate=False,  # 测锁/TOCTOU 用简化替身剧本，结构守卫不在本测试关注范围
    )


def test_locked_episode_script_no_relock_but_refreshes_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """locked_episode_script 不走会二次取项目锁的 sync_episode_from_script，但仍刷新 project.json。"""
    pm = ProjectManager(tmp_path)
    name = "p-sync"
    _seed(pm, name)

    # 把 project.json 的 updated_at 置为哨兵旧值（绕过会自动 touch 的写入路径）
    from lib.json_io import atomic_write_json

    project_file = pm._get_project_file_path(name)
    project = pm.load_project(name)
    project["metadata"]["updated_at"] = "2020-01-01T00:00:00+00:00"
    atomic_write_json(project_file, project)

    calls: list[tuple] = []
    monkeypatch.setattr(pm, "sync_episode_from_script", lambda *a, **k: calls.append(a))

    with pm.locked_episode_script(name, lambda _proj: "episode_1.json", validate=False) as script:
        script["video_units"] = [{"unit_id": "E1U1", "generated_assets": {"status": "pending"}}]

    # 不调用会二次取项目锁的 sync_episode_from_script（否则自死锁）
    assert calls == [], "locked_episode_script 不应调用 sync_episode_from_script"
    # 但 project.json 的 updated_at 仍被内联刷新（对齐旧路径行为）
    refreshed = pm.load_project(name)
    assert refreshed["metadata"]["updated_at"] != "2020-01-01T00:00:00+00:00", "project.json updated_at 未刷新"


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
        with pm.locked_episode_script(name, lambda _proj: "episode_1.json", validate=False) as script:
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
        with pm.locked_episode_script(name, _resolver, validate=False) as script:
            script["video_units"] = [{"unit_id": "SHOULD_NOT_PERSIST"}]

    # 旧脚本未被写入（with 体未执行）
    units = pm.load_script(name, "episode_1.json").get("video_units") or []
    assert units == [], "重绑检测命中时不应改写旧脚本"


def test_locked_episode_script_title_write_mirrors_to_project(tmp_path: Path) -> None:
    """写剧本顶层 title → 内联 _apply_episode_sync 把镜像同步进 project.json episodes[].title；
    且后续保存脚本不丢标题（这是 PATCH /episodes/{episode} 改名端点依赖的机制）。"""
    pm = ProjectManager(tmp_path)
    name = "p-title"
    _seed(pm, name)

    with pm.locked_episode_script(name, lambda _proj: "episode_1.json", validate=False) as script:
        script["title"] = "改后的集名"

    # 剧本顶层 title 落盘 + project.json 镜像同步
    assert pm.load_script(name, "episode_1.json")["title"] == "改后的集名"
    ep = next(e for e in pm.load_project(name)["episodes"] if e["episode"] == 1)
    assert ep["title"] == "改后的集名"

    # 后续保存脚本（不显式动 title）不丢标题
    with pm.locked_script(name, "episode_1.json", validate=False) as script:
        script.setdefault("video_units", [])
    assert pm.load_script(name, "episode_1.json")["title"] == "改后的集名"
    ep_after = next(e for e in pm.load_project(name)["episodes"] if e["episode"] == 1)
    assert ep_after["title"] == "改后的集名"


def test_locked_episode_script_no_self_deadlock(tmp_path: Path) -> None:
    """正常写入路径不挂起（sync 自死锁回归）：写入在超时内完成且生效。"""
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
