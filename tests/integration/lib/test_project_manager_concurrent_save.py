"""剧本并发写入竞态防护测试。

覆盖 `save_script` 在并发 PATCH 下的原子性，以及 lock 文件命名不会泄露到
`list_scripts()` 或项目导出 ZIP。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from lib import project_manager as project_manager_module
from lib.content_digest import canonical_json_digest
from lib.project_manager import ProjectManager, ScriptWriteConflict
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.services.project_archive import ProjectArchiveService


def _seed_project(pm: ProjectManager, name: str) -> None:
    """创建一个最小可用的项目 + 初始 episode_1 剧本。"""
    pm.create_project(name)
    pm.save_project(
        name,
        {
            "name": name,
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "episodes": [],
            "metadata": {"created_at": "2025-01-01", "updated_at": "2025-01-01"},
        },
    )


def _make_script(episode: int, payload_size: int) -> dict:
    """构造一个结构合法的旁白/解说剧本，segment 数量决定 JSON 体积。"""
    filler = "填充文本 " * (payload_size // 10 + 1)
    return {
        "episode": episode,
        "title": f"Episode {episode}",
        "content_mode": "narration",
        "summary": "测试摘要",
        "novel": {"title": "测试小说", "chapter": f"第{episode}章"},
        "segments": [
            {
                "segment_id": f"E{episode}S{i}",
                "duration_seconds": 4,
                "novel_text": filler,
                "characters_in_segment": ["角色A"],
                "image_prompt": {
                    "scene": filler,
                    "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                },
                "video_prompt": {
                    "action": "转身",
                    "camera_motion": "Static",
                    "ambiance_audio": "风声",
                },
            }
            for i in range(payload_size)
        ],
    }


class TestSaveScriptConcurrency:
    def test_same_name_project_creation_claims_directory_atomically(self, tmp_path: Path, monkeypatch) -> None:
        pm = ProjectManager(tmp_path)
        project_dir = tmp_path / "demo"
        barrier = threading.Barrier(2)
        synchronized_threads: set[int] = set()
        synchronization_lock = threading.Lock()
        original_mkdir = Path.mkdir

        def synchronized_mkdir(path: Path, *args, **kwargs) -> None:
            thread_id = threading.get_ident()
            with synchronization_lock:
                should_wait = path == project_dir and thread_id not in synchronized_threads
                if should_wait:
                    synchronized_threads.add(thread_id)
            if should_wait:
                _ = barrier.wait()
            original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", synchronized_mkdir)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(pm.create_project, "demo", mode) for mode in ("drama", "narration")]
            outcomes = []
            try:
                for future in futures:
                    try:
                        outcomes.append(future.result(timeout=5))
                    except FileExistsError as exc:
                        outcomes.append(exc)
            finally:
                barrier.abort()

        assert sum(isinstance(outcome, Path) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, FileExistsError) for outcome in outcomes) == 1
        assert (project_dir / "project.json").exists()

    async def test_async_file_lock_wait_keeps_event_loop_responsive(self, tmp_path: Path, monkeypatch) -> None:
        pm = ProjectManager(tmp_path)
        path = tmp_path / "draft.json"
        held = threading.Event()
        release = threading.Event()

        def hold_lock() -> None:
            with pm.file_lock(path):
                held.set()
                release.wait()

        holder = threading.Thread(target=hold_lock)
        holder.start()
        task: asyncio.Task[None] | None = None
        try:
            assert await asyncio.to_thread(held.wait, 1)

            attempted = asyncio.Event()
            entered = asyncio.Event()
            original_lock = project_manager_module.portalocker.lock

            def tracked_lock(*args, **kwargs) -> None:
                attempted.set()
                original_lock(*args, **kwargs)

            monkeypatch.setattr(project_manager_module.portalocker, "lock", tracked_lock)

            async def contend() -> None:
                async with pm.async_file_lock(path):
                    entered.set()

            task = asyncio.create_task(contend())
            await asyncio.wait_for(attempted.wait(), timeout=1)
            assert not entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            holder.join(timeout=1)

        async with pm.async_file_lock(path):
            pass

    async def test_async_file_lock_propagates_non_contention_errors(self, tmp_path: Path, monkeypatch) -> None:
        pm = ProjectManager(tmp_path)

        def fail_lock(*_args, **_kwargs) -> None:
            raise project_manager_module.portalocker.LockException("lock backend failed")

        monkeypatch.setattr(project_manager_module.portalocker, "lock", fail_lock)

        with pytest.raises(project_manager_module.portalocker.LockException, match="lock backend failed"):
            async with pm.async_file_lock(tmp_path / "draft.json"):
                pass

    def test_save_script_rejects_stale_expected_fingerprint(self, tmp_path: Path) -> None:
        pm = ProjectManager(tmp_path)
        name = "proj-occ"
        _seed_project(pm, name)
        path = pm.save_script(name, _make_script(1, payload_size=4), "episode_1.json")
        baseline = canonical_json_digest(json.loads(path.read_text(encoding="utf-8")))

        newer = _make_script(1, payload_size=5)
        pm.save_script(name, newer, "episode_1.json")

        with pytest.raises(ScriptWriteConflict) as caught:
            pm.save_script(
                name,
                _make_script(1, payload_size=6),
                "episode_1.json",
                expected_fingerprint=baseline,
            )

        assert caught.value.expected == baseline
        assert caught.value.actual == canonical_json_digest(pm.load_script(name, "episode_1.json"))
        assert len(pm.load_script(name, "episode_1.json")["segments"]) == 5

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
                except Exception as e:
                    errors.append(e)

        def _reader() -> None:
            while not stop.is_set():
                try:
                    pm.load_script(name, "episode_1.json")
                except Exception as e:
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


class TestConcurrentReadModifyWrite:
    """并发 read-modify-write 不应丢更新。"""

    def test_concurrent_update_scene_asset_preserves_all(self, tmp_path: Path) -> None:
        """并发对不同 segment 调用 update_scene_asset，所有写入都必须持久化。

        整段 read-modify-write 必须收进 locked_script 的单锁内：读在锁外时多个线程会拿到
        同一份快照并互相覆盖，只有最后一个写者的更新存活。
        """
        pm = ProjectManager(tmp_path)
        name = "proj-rmw"
        _seed_project(pm, name)

        n = 24
        pm.save_script(name, _make_script(1, payload_size=n), "episode_1.json")

        # 用 barrier 让所有线程尽量同时进入，最大化竞争窗口
        barrier = threading.Barrier(n)

        def _writer(i: int) -> None:
            barrier.wait()
            pm.update_scene_asset(
                project_name=name,
                script_filename="episode_1.json",
                scene_id=f"E1S{i}",
                asset_type="video_clip",
                asset_path=f"videos/scene_{i}.mp4",
            )

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(_writer, i) for i in range(n)]
            for fut in as_completed(futures):
                fut.result()

        loaded = pm.load_script(name, "episode_1.json")
        by_id = {s["segment_id"]: s for s in loaded["segments"]}
        for i in range(n):
            ga = by_id[f"E1S{i}"].get("generated_assets") or {}
            assert ga.get("video_clip") == f"videos/scene_{i}.mp4", f"E1S{i} 的更新丢失：{ga}"

    def test_update_scene_asset_missing_scene_does_not_write(self, tmp_path: Path) -> None:
        """目标 scene 不存在时抛 KeyError 且不改动文件（locked_script 跳过写回）。"""
        pm = ProjectManager(tmp_path)
        name = "proj-rmw-missing"
        _seed_project(pm, name)
        pm.save_script(name, _make_script(1, payload_size=4), "episode_1.json")

        before = (pm.get_project_path(name) / "scripts" / "episode_1.json").read_bytes()
        try:
            pm.update_scene_asset(
                project_name=name,
                script_filename="episode_1.json",
                scene_id="NOPE",
                asset_type="video_clip",
                asset_path="videos/nope.mp4",
            )
            raise AssertionError("应抛 KeyError")
        except KeyError:
            pass
        after = (pm.get_project_path(name) / "scripts" / "episode_1.json").read_bytes()
        assert before == after, "未命中时不应改写脚本文件"


def test_loaded_json_not_extra_data_after_save_script(tmp_path: Path) -> None:
    """save_script 后磁盘内容必须是单个 JSON 对象，不能有 Extra data。"""
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
