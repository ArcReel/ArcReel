"""剧本并发写入竞态防护测试。

覆盖 `save_script` 在并发 PATCH 下的原子性，以及 lock 文件命名不会泄露到
`list_scripts()` 或项目导出 ZIP。
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from lib.project_manager import ProjectManager
from server.services.project_archive import ProjectArchiveService


def _seed_project(pm: ProjectManager, name: str) -> None:
    """创建一个最小可用的项目 + 初始 episode_1 剧本。"""
    pm.create_project(name)
    pm.save_project(
        name,
        {
            "name": name,
            "episodes": [],
            "metadata": {"created_at": "2025-01-01", "updated_at": "2025-01-01"},
        },
    )


def _make_script(episode: int, payload_size: int) -> dict:
    """构造一个说书模式剧本，segment 数量决定 JSON 体积。"""
    return {
        "episode": episode,
        "title": f"Episode {episode}",
        "content_mode": "narration",
        "segments": [
            {
                "segment_id": f"E{episode}S{i}",
                "duration_seconds": 4,
                "image_prompt": "图像提示词 " * (payload_size // 10),
                "video_prompt": "视频提示词 " * (payload_size // 10),
            }
            for i in range(payload_size)
        ],
    }


class TestSaveScriptConcurrency:
    def test_concurrent_save_script_no_corruption(self, tmp_path: Path) -> None:
        """并发写入同一 script 文件，最终应可被 json.load 成功解析。"""
        pm = ProjectManager(tmp_path)
        name = "proj-concurrent"
        _seed_project(pm, name)

        # 预置一次，确保文件存在
        pm.save_script(name, _make_script(1, payload_size=40), "episode_1.json")

        def _writer(i: int) -> int:
            script = _make_script(1, payload_size=40 + (i % 7))  # 各线程 JSON 长度不同
            script["segments"][0]["note"] = f"writer-{i}"
            pm.save_script(name, script, "episode_1.json")
            return i

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_writer, i) for i in range(32)]
            for fut in as_completed(futures):
                fut.result()  # 任一写入抛异常即失败

        # 最终文件必须是完整合法的 JSON 且结构完整
        loaded = pm.load_script(name, "episode_1.json")
        assert loaded["episode"] == 1
        assert isinstance(loaded["segments"], list)
        assert len(loaded["segments"]) >= 40

    def test_save_script_atomicity_during_read(self, tmp_path: Path) -> None:
        """写入流程中并发 load_script 不应看到半写入/损坏状态。"""
        pm = ProjectManager(tmp_path)
        name = "proj-rw"
        _seed_project(pm, name)
        pm.save_script(name, _make_script(1, payload_size=60), "episode_1.json")

        stop = threading.Event()
        errors: list[Exception] = []

        def _writer() -> None:
            i = 0
            while not stop.is_set():
                i += 1
                try:
                    pm.save_script(name, _make_script(1, payload_size=40 + (i % 30)), "episode_1.json")
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

        def _reader() -> None:
            while not stop.is_set():
                try:
                    pm.load_script(name, "episode_1.json")
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

        threads = [threading.Thread(target=_writer), threading.Thread(target=_reader), threading.Thread(target=_reader)]
        for t in threads:
            t.start()
        time.sleep(0.8)
        stop.set()
        for t in threads:
            t.join(timeout=5)

        assert errors == [], f"并发读写中出现异常：{errors[:3]}"


class TestLockFileNaming:
    def test_script_lock_is_hidden_and_not_listed(self, tmp_path: Path) -> None:
        """script lock 文件应以 `.` 开头并不出现在 list_scripts 结果中。"""
        pm = ProjectManager(tmp_path)
        name = "proj-lock"
        _seed_project(pm, name)
        pm.save_script(name, _make_script(1, payload_size=10), "episode_1.json")

        scripts_dir = pm.get_project_path(name) / "scripts"
        entries = {p.name for p in scripts_dir.iterdir()}
        assert ".episode_1.json.lock" in entries, f"期望找到隐藏锁文件，实际 {entries}"
        assert "episode_1.json.lock" not in entries
        assert "episode_1.lock" not in entries

        assert pm.list_scripts(name) == ["episode_1.json"]

    def test_project_lock_is_hidden(self, tmp_path: Path) -> None:
        """project.json 的 lock 文件也应为隐藏命名（与注释一致）。"""
        pm = ProjectManager(tmp_path)
        name = "proj-proj-lock"
        _seed_project(pm, name)
        pm.save_project(name, pm.load_project(name))

        project_dir = pm.get_project_path(name)
        names = {p.name for p in project_dir.iterdir()}
        assert ".project.json.lock" in names, f"期望 .project.json.lock，实际 {names}"
        assert "project.lock" not in names


class TestLockPathAliasing:
    def test_aliased_filenames_share_single_lock(self, tmp_path: Path) -> None:
        """`./episode_1.json`、`episode_1.json`、`scripts/episode_1.json` 必须解析到同一把锁文件。"""
        pm = ProjectManager(tmp_path)
        name = "proj-alias"
        _seed_project(pm, name)
        pm.save_script(name, _make_script(1, payload_size=10), "episode_1.json")

        scripts_dir = pm.get_project_path(name) / "scripts"

        # 分别用三种别名进入 _script_lock 并检查拿到的 lock_path 相同
        lock_paths = set()
        for alias in ["episode_1.json", "./episode_1.json", "scripts/episode_1.json"]:
            with pm._script_lock(name, alias):
                # 锁持有期间断言 scripts 下应存在唯一的隐藏锁文件
                hidden = [p for p in scripts_dir.iterdir() if p.name.startswith(".") and p.name.endswith(".lock")]
                assert len(hidden) == 1, f"期望唯一锁文件，实际 {[p.name for p in hidden]}"
                lock_paths.add(hidden[0].resolve())

        assert len(lock_paths) == 1, f"别名应共享同一锁文件，实际产生 {lock_paths}"
        # 项目根目录不应出现 script 相关锁的逸出（防止 `./ep.json` 造成 scripts/../.ep.json.lock）
        project_dir = pm.get_project_path(name)
        strays = [
            p.name for p in project_dir.iterdir() if p.is_file() and p.name.endswith(".lock") and "episode" in p.name
        ]
        assert strays == [], f"项目根目录不应出现 script 锁文件残留，实际 {strays}"


class TestArchiveExcludesLocks:
    def test_is_hidden_member_filters_lock_and_tmp(self) -> None:
        """导出 ZIP 的隐藏成员判定应覆盖 lock 与原子写入的 tmp 残留。"""
        assert ProjectArchiveService._is_hidden_member((".project.json.lock",))
        assert ProjectArchiveService._is_hidden_member(("scripts", ".episode_1.json.lock"))
        assert ProjectArchiveService._is_hidden_member((".project.abc.tmp",))
        assert ProjectArchiveService._is_hidden_member(("scripts", ".project.xyz.tmp"))
        # 正常文件不被过滤
        assert not ProjectArchiveService._is_hidden_member(("scripts", "episode_1.json"))
        assert not ProjectArchiveService._is_hidden_member(("project.json",))


class TestSyncAfterConcurrentWrite:
    def test_project_json_reflects_last_script_after_concurrent_writes(self, tmp_path: Path) -> None:
        """并发 save_script 后 project.json 的 episode 条目应与 script 一致。"""
        pm = ProjectManager(tmp_path)
        name = "proj-sync"
        _seed_project(pm, name)

        def _writer(i: int) -> None:
            script = _make_script(1, payload_size=20)
            script["title"] = f"Title-{i}"
            pm.save_script(name, script, "episode_1.json")

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(_writer, range(12)))

        project = pm.load_project(name)
        episodes = project.get("episodes", [])
        assert len(episodes) == 1
        assert episodes[0]["episode"] == 1
        assert episodes[0]["script_file"] == "scripts/episode_1.json"
        # title 应来自某一次写入，非空且符合模式
        assert episodes[0]["title"].startswith("Title-")

        # 剧本仍然是合法 JSON
        loaded = pm.load_script(name, "episode_1.json")
        assert loaded["title"] == episodes[0]["title"]


def test_loaded_json_not_extra_data_after_save_script(tmp_path: Path) -> None:
    """回归：save_script 后磁盘内容必须是单个 JSON 对象，不能有 Extra data。"""
    pm = ProjectManager(tmp_path)
    name = "proj-regress"
    _seed_project(pm, name)
    pm.save_script(name, _make_script(1, payload_size=50), "episode_1.json")

    raw = (pm.get_project_path(name) / "scripts" / "episode_1.json").read_bytes().decode("utf-8")
    # 直接用 json.loads 解析整个文件——若末尾多 `}` 会抛 Extra data
    json.loads(raw)
    assert raw.strip().endswith("}")
    # 末尾不应连续两个及以上 `}` 字符（排除合法嵌套尾巴，最内层 items 数组收尾为 "]\n}"）
    assert not raw.rstrip().endswith("}}")
