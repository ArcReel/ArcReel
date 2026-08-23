"""分镜尾帧设置/清除端点测试。

覆盖两条设置通道（上传 / 项目内选图）落到同一快照路径、换图原地覆盖、清除语义、
越界与缺失拒绝，以及快照与源图的解耦（源图被改写/删除不影响已定尾帧）。
"""

import asyncio
import threading
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.data_validator import DataValidator
from lib.i18n import _ as i18n_message
from lib.project_manager import ProjectManager
from lib.script_editor import ScriptEditError
from lib.version_manager import VersionManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import end_frames
from server.services import end_frame as end_frame_service
from server.services import upload_finalize
from tests.auth_deps import AUTH_DEPENDENCIES

END_FRAME_REL = "end_frames/scene_E1S01.png"


def _img_bytes(fmt="JPEG", size=(8, 8), color=(255, 0, 0)):
    image = Image.new("RGB", size, color)
    buf = BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def _seed_project(tmp_path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "novel_text": "t",
                    "duration_seconds": 5,
                    "generated_assets": {"status": "pending"},
                },
                {
                    "segment_id": "E1S02",
                    "novel_text": "t2",
                    "duration_seconds": 5,
                    "generated_assets": {"status": "pending"},
                },
            ],
        },
        "episode_1.json",
        validate=False,
    )
    return pm


def _build_client(pm: ProjectManager, monkeypatch) -> TestClient:
    """把给定 ProjectManager 接到尾帧路由上，返回可发请求的 TestClient。"""
    monkeypatch.setattr(end_frame_service, "get_project_manager", lambda: pm)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(end_frames.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


@pytest.fixture
def end_frames_client(tmp_path, monkeypatch):
    pm = _seed_project(tmp_path)
    return _build_client(pm, monkeypatch), pm


def _upload(end_frames_client, content: bytes, filename="frame.jpg", shot_id="E1S01"):
    return end_frames_client.post(
        f"/api/v1/projects/demo/shots/{shot_id}/end-frame/upload?script_file=episode_1.json",
        files={"file": (filename, BytesIO(content), "application/octet-stream")},
    )


def _select(end_frames_client, source_path: str, shot_id="E1S01"):
    return end_frames_client.post(
        f"/api/v1/projects/demo/shots/{shot_id}/end-frame/select",
        json={"script_file": "episode_1.json", "source_path": source_path},
    )


def _delete(end_frames_client, shot_id="E1S01"):
    return end_frames_client.delete(f"/api/v1/projects/demo/shots/{shot_id}/end-frame?script_file=episode_1.json")


def _segment(pm: ProjectManager, index: int = 0) -> dict:
    return pm.load_script("demo", "episode_1.json")["segments"][index]


def _write_source_image(pm: ProjectManager, rel: str, content: bytes) -> None:
    target = pm.get_project_path("demo") / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


class TestUploadChannel:
    def test_upload_writes_normalized_png_snapshot_and_field(self, end_frames_client):
        c, pm = end_frames_client
        resp = _upload(c, _img_bytes("JPEG"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["end_frame_image"] == END_FRAME_REL

        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        # JPEG 入参统一归一为 PNG（与分镜图上传同口径）
        with Image.open(pm.get_project_path("demo") / END_FRAME_REL) as img:
            assert img.format == "PNG"

    def test_replacing_overwrites_in_place(self, end_frames_client):
        c, pm = end_frames_client
        _upload(c, _img_bytes("JPEG", size=(8, 8)))
        resp = _upload(c, _img_bytes("PNG", size=(16, 16)))
        assert resp.status_code == 200, resp.text
        # 路径固定，字段值不变；文件内容被换掉
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        with Image.open(pm.get_project_path("demo") / END_FRAME_REL) as img:
            assert img.size == (16, 16)

    def test_undecodable_bytes_rejected(self, end_frames_client):
        c, pm = end_frames_client
        resp = _upload(c, b"not an image", filename="frame.png")
        assert resp.status_code == 400
        assert _segment(pm).get("end_frame_image") is None

    def test_unsupported_extension_rejected(self, end_frames_client):
        c, _pm = end_frames_client
        assert _upload(c, _img_bytes("PNG"), filename="frame.gif").status_code == 400

    def test_unknown_shot_returns_404(self, end_frames_client):
        c, _pm = end_frames_client
        assert _upload(c, _img_bytes("PNG"), shot_id="E9S99").status_code == 404


class TestSelectChannel:
    def test_select_project_image_snapshots_to_end_frames(self, end_frames_client):
        c, pm = end_frames_client
        _write_source_image(pm, "storyboards/scene_E1S02.png", _img_bytes("PNG", size=(12, 12)))

        resp = _select(c, "storyboards/scene_E1S02.png")
        assert resp.status_code == 200, resp.text
        assert resp.json()["end_frame_image"] == END_FRAME_REL
        # 字段存的是快照路径，不是源图路径 —— 引用关系从结构上就不存在
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        with Image.open(pm.get_project_path("demo") / END_FRAME_REL) as img:
            assert img.format == "PNG"
            assert img.size == (12, 12)

    def test_traversal_path_rejected(self, end_frames_client):
        c, pm = end_frames_client
        resp = _select(c, "../../../etc/passwd")
        assert resp.status_code == 400
        assert _segment(pm).get("end_frame_image") is None

    def test_missing_source_returns_404(self, end_frames_client):
        c, _pm = end_frames_client
        assert _select(c, "storyboards/scene_E1S99.png").status_code == 404

    def test_oversized_source_image_rejected(self, end_frames_client, monkeypatch):
        """项目内选图源超过上传通道同一上限时须拒绝（见 read_project_image）。

        专属文案 + 400：选图请求体只是路径字符串，413（请求体过大）语义不适用，
        与上传通道自身的 413 + upload_too_large 分流（见 test_shot_uploads_router）。
        """
        c, pm = end_frames_client
        monkeypatch.setattr(end_frame_service, "UPLOAD_IMAGE_MAX_BYTES", 16)
        _write_source_image(pm, "storyboards/scene_E1S02.png", _img_bytes("PNG", size=(64, 64)))

        resp = _select(c, "storyboards/scene_E1S02.png")
        assert resp.status_code == 400
        # max_mb 由字节上限整除 1 MB 得出，本例上限被压到 16 字节故为 0
        assert resp.json()["detail"] == i18n_message(
            "end_frame_source_too_large", max_mb=0, path="storyboards/scene_E1S02.png"
        )
        assert _segment(pm).get("end_frame_image") is None

    def test_source_image_read_is_bounded(self, end_frames_client, monkeypatch):
        """超限源图不得整读入内存：单次 read 请求的字节数以上限 +1 为界，够判超限即止。"""
        _c, pm = end_frames_client
        _write_source_image(pm, "storyboards/scene_E1S02.png", _img_bytes("PNG", size=(64, 64)))
        project_path = pm.get_project_path("demo")
        monkeypatch.setattr(end_frame_service, "UPLOAD_IMAGE_MAX_BYTES", 16)

        read_sizes: list[int] = []
        real_open = Path.open

        class _SpyHandle:
            def __init__(self, handle) -> None:
                self._handle = handle

            def read(self, size: int = -1) -> bytes:
                read_sizes.append(size)
                return self._handle.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return self._handle.__exit__(*exc_info)

        monkeypatch.setattr(Path, "open", lambda self, *a, **kw: _SpyHandle(real_open(self, *a, **kw)))

        with pytest.raises(end_frame_service.EndFrameError):
            end_frame_service.read_project_image(project_path, "storyboards/scene_E1S02.png")

        assert read_sizes == [17]


class TestClear:
    def test_clear_deletes_snapshot_and_nulls_field(self, end_frames_client):
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG"))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        assert snapshot.exists()

        resp = _delete(c)
        assert resp.status_code == 200, resp.text
        assert _segment(pm)["end_frame_image"] is None
        assert not snapshot.exists()

    def test_clear_without_end_frame_is_idempotent(self, end_frames_client):
        c, pm = end_frames_client
        assert _delete(c).status_code == 200
        assert _segment(pm)["end_frame_image"] is None

    def test_clear_unknown_shot_returns_404(self, end_frames_client):
        c, _pm = end_frames_client
        assert _delete(c, shot_id="E9S99").status_code == 404


class TestSnapshotDecoupling:
    """快照与源图彻底解耦：源图后续被改写或删除都动不到已定尾帧。"""

    def test_source_rewrite_and_delete_leave_end_frame_intact(self, end_frames_client):
        c, pm = end_frames_client
        source_rel = "storyboards/scene_E1S02.png"
        _write_source_image(pm, source_rel, _img_bytes("PNG", size=(12, 12), color=(255, 0, 0)))
        _select(c, source_rel)

        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        before = snapshot.read_bytes()

        # 源图重生成（内容与尺寸都变）后再删除，模拟版本回滚 / 资源清理
        _write_source_image(pm, source_rel, _img_bytes("PNG", size=(24, 24), color=(0, 0, 255)))
        (pm.get_project_path("demo") / source_rel).unlink()

        assert snapshot.read_bytes() == before
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL

    def test_source_version_rollback_leaves_end_frame_intact(self, end_frames_client):
        """源图回滚到旧版本后已定尾帧不变——尾帧存的是快照路径，与源图的版本轴无关。"""
        c, pm = end_frames_client
        source_rel = "storyboards/scene_E1S02.png"
        source_abs = pm.get_project_path("demo") / source_rel
        vm = VersionManager(pm.get_project_path("demo"))

        _write_source_image(pm, source_rel, _img_bytes("PNG", size=(12, 12), color=(255, 0, 0)))
        vm.add_version("storyboards", "E1S02", "v1", source_file=source_abs)
        _select(c, source_rel)

        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        before = snapshot.read_bytes()

        # 源图重生成为 v2，再回滚到 v1
        _write_source_image(pm, source_rel, _img_bytes("PNG", size=(24, 24), color=(0, 0, 255)))
        vm.add_version("storyboards", "E1S02", "v2", source_file=source_abs)
        vm.restore_version("storyboards", "E1S02", 1, source_abs)

        assert snapshot.read_bytes() == before
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL

    def test_per_shot_snapshots_are_independent(self, end_frames_client):
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG", size=(8, 8)), shot_id="E1S01")
        _upload(c, _img_bytes("PNG", size=(16, 16)), shot_id="E1S02")

        assert _segment(pm, 0)["end_frame_image"] == "end_frames/scene_E1S01.png"
        assert _segment(pm, 1)["end_frame_image"] == "end_frames/scene_E1S02.png"

        # 清一个不影响另一个
        _delete(c, shot_id="E1S01")
        assert _segment(pm, 0)["end_frame_image"] is None
        assert _segment(pm, 1)["end_frame_image"] == "end_frames/scene_E1S02.png"
        assert (pm.get_project_path("demo") / "end_frames/scene_E1S02.png").exists()


class _InterleavedResponses(NamedTuple):
    """`_interleave_across_critical_section` 的两个请求各自的响应。"""

    first: httpx.Response
    second: httpx.Response


def _interleave_across_critical_section(
    monkeypatch, first: Callable[[], httpx.Response], second: Callable[[], httpx.Response]
) -> _InterleavedResponses:
    """让 `first` 停在剧本锁临界区内，确认 `second` 被锁挡在外面后再放行，返回两者的响应。

    交错点由钩子对齐而非靠线程调度碰运气：钩子挂在锁内的剧本持久化上（`locked_script`
    退出前的最后一步），此时先到的一方已经做完自己的文件副作用、字段写回尚未落盘——正是
    「一方写完文件、对方抢在字段写回前动手」这一悬空引用场景的临界点。后到的一方在此期间
    必须仍被剧本锁挡住：它若能完成，就说明两段操作没有真正互斥。

    两个请求错开发出还有一层必要性：TestClient 每个请求起独立线程与事件循环，而 FastAPI 对
    `include_router` 的路由展开是首次匹配时惰性构建、且不加锁；同时发出的两个首请求会撞上
    半构建的路由表，随机得到路由级 404。此处第二个请求在第一个请求完成路由之后才发出。

    「后到的一方没能完成」这一断言以固定等待窗口否证，是单向的：互斥若被破坏，慢机上后到
    的一方也可能因窗口内尚未跑完而漏报；但绝不会反过来把守规矩的实现判成失败，故不引入
    时序敏感性。不带锁的整条 set/clear 是毫秒级，窗口取值远宽于此。
    """
    original = ProjectManager._write_script_unlocked
    entered_section = threading.Event()
    resume = threading.Event()
    gate_lock = threading.Lock()
    gate_open = True

    def _gated_persist(self, *args, **kwargs):
        nonlocal gate_open
        with gate_lock:
            take_gate, gate_open = gate_open, False
        if take_gate:
            entered_section.set()
            assert resume.wait(timeout=10), "主线程未放行临界区"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ProjectManager, "_write_script_unlocked", _gated_persist)

    results: dict[str, httpx.Response] = {}
    errors: dict[str, BaseException] = {}

    def _run(key: str, call: Callable[[], httpx.Response]) -> threading.Thread:
        def _target() -> None:
            try:
                results[key] = call()
            except Exception as exc:  # 主线程复述，不能只打印到 stderr
                errors[key] = exc

        thread = threading.Thread(target=_target)
        thread.start()
        return thread

    t_first = _run("first", first)
    try:
        assert entered_section.wait(timeout=10), "先到的请求没能进入剧本锁临界区"

        t_second = _run("second", second)
        # 先到的一方还停在临界区内，后到的一方不该有任何完成的可能
        t_second.join(timeout=0.3)
        assert "second" not in results, f"后到的请求插进了对方的临界区：{results.get('second')}"
    finally:
        resume.set()
    t_first.join(timeout=10)
    t_second.join(timeout=10)
    assert not t_first.is_alive(), "先到的请求未在超时内结束"
    assert not t_second.is_alive(), "后到的请求未在超时内结束"
    assert not errors, f"请求线程内抛出异常：{errors}"
    return _InterleavedResponses(first=results["first"], second=results["second"])


class TestConcurrentSetClear:
    """设置与清除并发交错时，字段与快照文件必须同进同退，不留悬空引用。"""

    def test_clear_blocked_until_set_leaves_critical_section(self, end_frames_client, monkeypatch):
        """设置先进临界区：清除被挡到设置整段完成之后，最终状态是「清除赢」且无残留引用。"""
        c, pm = end_frames_client
        snapshot = pm.get_project_path("demo") / END_FRAME_REL

        results = _interleave_across_critical_section(
            monkeypatch,
            first=lambda: _upload(c, _img_bytes("PNG")),
            second=lambda: _delete(c),
        )

        assert results.first.status_code == 200, results.first.text
        assert results.second.status_code == 200, results.second.text
        # 清除后到、整段落在设置之后：字段置空，快照文件随之删除
        assert _segment(pm).get("end_frame_image") is None
        assert not snapshot.exists()

    def test_set_blocked_until_clear_leaves_critical_section(self, end_frames_client, monkeypatch):
        """清除先进临界区：设置被挡到清除整段完成之后，最终字段非空且快照文件在位。"""
        c, pm = end_frames_client
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        assert _upload(c, _img_bytes("PNG")).status_code == 200

        results = _interleave_across_critical_section(
            monkeypatch,
            first=lambda: _delete(c),
            second=lambda: _upload(c, _img_bytes("PNG")),
        )

        assert results.first.status_code == 200, results.first.text
        assert results.second.status_code == 200, results.second.text
        # 设置后到、整段落在清除之后：字段非空则快照文件必须存在——不允许指向缺失文件的引用
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        assert snapshot.exists()


def _fail_first_persist(monkeypatch):
    """让下一次剧本持久化失败一次，随后（补偿回滚重新过锁触发的第二次）恢复原实现。

    补偿回滚本身也要走一次 `locked_script`（读快照现状、必要时改写、重新持久化），若一律
    拦截会连回滚自身的持久化也打断，所以只失败第一次。
    """
    original = ProjectManager._write_script_unlocked
    calls = {"n": 0}

    def _boom(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("simulated persist failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ProjectManager, "_write_script_unlocked", _boom)


def _inject_concurrent_takeover_before_nth_load(monkeypatch, key, target: Path, content: bytes | None, n: int):
    """模拟「回滚重新过锁前，另一个并发请求已抢先完整地执行完自己的一次操作」。

    回滚判定的是「操作代次」而非文件内容（见 end_frame.py 模块 docstring 的退化值说明），
    所以这里不能只改文件字节，还要推进该分镜的代次，否则复现不出代次判定要拦截的场景。
    `content=None` 表示并发的那一次是清除（文件被删）；否则表示并发的那一次是设置。

    补偿回滚会再发起一次 `locked_script`，其剧本读取即回滚临界区的起点——在其第 n 次被
    调用时执行注入的「并发接管」，相当于恰好在回滚拿到锁之前完成。挂钩点取底层的
    `_read_script_unlocked`：锁外的 `load_script` 与锁内的读-改-写都经它读盘，计数才覆盖
    两类临界区起点。
    """
    original = ProjectManager._read_script_unlocked
    calls = {"n": 0}

    def _load_with_race(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == n:
            end_frame_service._advance_shot_generation(key)
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(content)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ProjectManager, "_read_script_unlocked", _load_with_race)


class TestPersistFailureRestoresSnapshot:
    """剧本持久化在临界区内失败时，快照文件须回滚到操作前状态，不留悬空引用或静默丢失。"""

    def test_set_failure_restores_previous_snapshot_bytes(self, end_frames_client, monkeypatch):
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG", size=(8, 8)))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        before = snapshot.read_bytes()

        _fail_first_persist(monkeypatch)
        resp = _upload(c, _img_bytes("PNG", size=(16, 16)))
        assert resp.status_code == 500

        # 字段未变（写回被跳过），快照文件也须还原成写前内容——不能是被换图覆盖后的新内容
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        assert snapshot.read_bytes() == before

    def test_set_failure_on_first_write_removes_snapshot(self, end_frames_client, monkeypatch):
        """此前未设置过尾帧时失败：快照文件此前不存在，须整段撤回而非留下孤儿文件。"""
        c, pm = end_frames_client
        snapshot = pm.get_project_path("demo") / END_FRAME_REL

        _fail_first_persist(monkeypatch)
        resp = _upload(c, _img_bytes("PNG"))
        assert resp.status_code == 500

        assert _segment(pm).get("end_frame_image") is None
        assert not snapshot.exists()

    def test_clear_failure_restores_deleted_snapshot(self, end_frames_client, monkeypatch):
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG"))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        before = snapshot.read_bytes()

        _fail_first_persist(monkeypatch)
        resp = _delete(c)
        assert resp.status_code == 500

        # 字段未变（仍指向原路径），快照文件须恢复——否则字段回滚后指向的是已删除的文件
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        assert snapshot.exists()
        assert snapshot.read_bytes() == before

    def test_set_failure_skips_restore_when_concurrent_write_supersedes(self, end_frames_client, monkeypatch):
        """回滚重新过锁时若该分镜的操作代次已前移，说明并发请求已经接管，
        必须跳过回滚——否则会用陈旧字节覆盖对方刚成功落盘的内容。"""
        c, pm = end_frames_client
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        key = ("demo", "episode_1.json", "E1S01")
        concurrent_bytes = _img_bytes("PNG", size=(32, 32))

        _fail_first_persist(monkeypatch)
        # load 序列：_locate_shot(1) → 本次失败的 locked_script(2) → 回滚的 locked_script(3)
        _inject_concurrent_takeover_before_nth_load(monkeypatch, key, snapshot, concurrent_bytes, n=3)

        resp = _upload(c, _img_bytes("PNG", size=(16, 16)))
        assert resp.status_code == 500

        assert snapshot.read_bytes() == concurrent_bytes

    def test_clear_failure_skips_restore_when_concurrent_write_recreates_snapshot(self, end_frames_client, monkeypatch):
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG"))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        key = ("demo", "episode_1.json", "E1S01")
        concurrent_bytes = _img_bytes("PNG", size=(40, 40))

        _fail_first_persist(monkeypatch)
        _inject_concurrent_takeover_before_nth_load(monkeypatch, key, snapshot, concurrent_bytes, n=3)

        resp = _delete(c)
        assert resp.status_code == 500

        # 回滚跳过：文件保留并发请求写入的新内容，没有被「删除态」覆盖
        assert snapshot.exists()
        assert snapshot.read_bytes() == concurrent_bytes

    def test_clear_failure_skips_restore_when_concurrent_clear_wins(self, end_frames_client, monkeypatch):
        """退化值场景：清除失败后文件已被删（期望内容与「无人接手」的期望值同为 None），
        并发的另一次清除随后也成功完成，结果同样是文件不存在——若
        仅比对文件内容将无法区分两者，误判为无人接手并把旧快照字节回滚出孤儿文件。
        代次判定下，并发清除会推进代次，回滚据此正确跳过。"""
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG"))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        key = ("demo", "episode_1.json", "E1S01")

        _fail_first_persist(monkeypatch)
        _inject_concurrent_takeover_before_nth_load(monkeypatch, key, snapshot, None, n=3)

        resp = _delete(c)
        assert resp.status_code == 500

        # 回滚跳过：文件保持并发清除后的「已删除」结果，没有被旧字节重新写回制造孤儿文件
        assert not snapshot.exists()

    def test_set_failure_skips_restore_when_concurrent_takeover_uses_other_alias(self, end_frames_client, monkeypatch):
        """别名归一：失败的这次操作用 `scripts/episode_1.json` 这个别名，
        代次键若不归一化会与并发接管（用 `episode_1.json` 归一后的键）各自生成一把代次，
        互相看不见——回滚会误判「无人接手」，用旧字节覆盖对方已成功落盘的内容。"""
        c, pm = end_frames_client
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        normalized_key = ("demo", "episode_1.json", "E1S01")
        concurrent_bytes = _img_bytes("PNG", size=(48, 48))

        _fail_first_persist(monkeypatch)
        _inject_concurrent_takeover_before_nth_load(monkeypatch, normalized_key, snapshot, concurrent_bytes, n=3)

        resp = c.post(
            "/api/v1/projects/demo/shots/E1S01/end-frame/upload?script_file=scripts/episode_1.json",
            files={"file": ("f.png", BytesIO(_img_bytes("PNG", size=(16, 16))), "application/octet-stream")},
        )
        assert resp.status_code == 500

        # 未归一化则代次键不同，回滚会看不到接管、用旧字节覆盖并发写入的新内容
        assert snapshot.read_bytes() == concurrent_bytes

    def test_set_failure_restore_uses_bytes_read_inside_critical_section(self, end_frames_client, monkeypatch):
        """陈旧基线问题：`old_bytes` 若在取得剧本锁之前预读，读到的是「进入临界区之前」
        而非「进入临界区那一刻」的文件内容。这里不推进代次，只在本次操作自己的
        `_read_script_unlocked` 调用（进入临界区的时点）直接改写文件——代次检查不会拦截
        （因为没有别的操作声称接管），能否回滚出这份改写后的内容，才真正区分基线是在
        锁内读还是锁外预读：锁外预读会读到改写前的旧内容，回滚会把改写后的内容覆盖掉。"""
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG", size=(8, 8)))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL

        original_read_script = ProjectManager._read_script_unlocked
        calls = {"n": 0}
        content_at_critical_section_entry = _img_bytes("PNG", size=(56, 56))

        def _load_with_rewrite(self, *args, **kwargs):
            calls["n"] += 1
            # 读盘序列：_locate_shot(1) → 失败操作自身的 locked_script(2)
            if calls["n"] == 2:
                snapshot.write_bytes(content_at_critical_section_entry)
            return original_read_script(self, *args, **kwargs)

        monkeypatch.setattr(ProjectManager, "_read_script_unlocked", _load_with_rewrite)
        _fail_first_persist(monkeypatch)

        resp = _upload(c, _img_bytes("PNG", size=(16, 16)))
        assert resp.status_code == 500

        # 基线在临界区内读取，读到的就是这份改写后的内容，回滚据此还原；
        # 若基线在锁外预读，读到的是改写前的旧内容，回滚会把这份改写覆盖掉
        assert snapshot.read_bytes() == content_at_critical_section_entry


class TestErrorMapping:
    """领域错误到 HTTP 的映射：三个端点行为一致，不泄漏服务器路径。"""

    def test_unknown_script_file_returns_404(self, end_frames_client):
        c, _pm = end_frames_client
        assert (
            c.post(
                "/api/v1/projects/demo/shots/E1S01/end-frame/upload?script_file=missing.json",
                files={"file": ("f.png", BytesIO(_img_bytes("PNG")), "application/octet-stream")},
            ).status_code
            == 404
        )
        assert (
            c.post(
                "/api/v1/projects/demo/shots/E1S01/end-frame/select",
                json={"script_file": "missing.json", "source_path": "storyboards/x.png"},
            ).status_code
            == 404
        )
        resp = c.delete("/api/v1/projects/demo/shots/E1S01/end-frame?script_file=missing.json")
        assert resp.status_code == 404

    def test_unknown_project_returns_404(self, end_frames_client):
        c, _pm = end_frames_client
        resp = c.post(
            "/api/v1/projects/nope/shots/E1S01/end-frame/upload?script_file=episode_1.json",
            files={"file": ("f.png", BytesIO(_img_bytes("PNG")), "application/octet-stream")},
        )
        assert resp.status_code == 404

    def test_empty_source_path_rejected(self, end_frames_client):
        c, pm = end_frames_client
        assert _select(c, "   ").status_code == 400
        assert _segment(pm).get("end_frame_image") is None

    def test_oversized_upload_rejected(self, end_frames_client, monkeypatch):
        c, pm = end_frames_client
        monkeypatch.setattr(upload_finalize, "UPLOAD_IMAGE_MAX_BYTES", 16)
        resp = _upload(c, _img_bytes("PNG", size=(64, 64)))
        assert resp.status_code == 413
        assert _segment(pm).get("end_frame_image") is None

    def test_traversal_shot_id_rejected_before_write(self, end_frames_client):
        """剧本里存在越界形状的镜头 id 时，快照路径解析必须拒绝而非写出项目目录。"""
        _c, pm = end_frames_client
        script = pm.load_script("demo", "episode_1.json")
        script["segments"][0]["segment_id"] = "../../../../evil"
        pm.save_script("demo", script, "episode_1.json", validate=False)

        with pytest.raises(end_frame_service.EndFrameError) as exc:
            asyncio.run(
                end_frame_service.set_end_frame_from_bytes(
                    project_name="demo",
                    script_file="episode_1.json",
                    shot_id="../../../../evil",
                    content=_img_bytes("PNG"),
                )
            )
        assert exc.value.key == "invalid_resource_id"

    def test_body_exceeding_limit_rejected_after_read(self, end_frames_client, monkeypatch):
        """Content-Length 缺失/被绕过时，按实际读入字节数兜底拒绝。"""
        c, pm = end_frames_client
        monkeypatch.setattr(end_frames, "validate_upload", lambda *a, **k: 16)
        assert _upload(c, _img_bytes("PNG", size=(64, 64))).status_code == 413
        assert _segment(pm).get("end_frame_image") is None

    def test_script_edit_error_maps_to_400(self, end_frames_client, monkeypatch):
        c, _pm = end_frames_client

        async def _boom(**_kwargs):
            raise ScriptEditError("坏掉的剧本结构")

        monkeypatch.setattr(end_frames, "set_end_frame_from_bytes", _boom)
        assert _upload(c, _img_bytes("PNG")).status_code == 400

    def test_unexpected_error_maps_to_500_without_internals(self, end_frames_client, monkeypatch):
        c, _pm = end_frames_client

        async def _boom(**_kwargs):
            raise RuntimeError("/absolute/server/path/leaked")

        monkeypatch.setattr(end_frames, "set_end_frame_from_bytes", _boom)
        resp = _upload(c, _img_bytes("PNG"))
        assert resp.status_code == 500
        assert "leaked" not in resp.text

    def test_illegal_project_name_returns_400(self, end_frames_client):
        """`get_project_path` 对非法项目名（正则不匹配）抛 ValueError，须映射为 400 而非落 500 兜底。"""
        c, _pm = end_frames_client
        resp = c.post(
            "/api/v1/projects/demo.evil/shots/E1S01/end-frame/upload?script_file=episode_1.json",
            files={"file": ("f.png", BytesIO(_img_bytes("PNG")), "application/octet-stream")},
        )
        assert resp.status_code == 400

    def test_illegal_script_file_returns_400(self, end_frames_client):
        """`load_script` 底层 `_safe_subpath` 对越界 script_file 抛 ValueError，须映射为 400。"""
        c, pm = end_frames_client
        resp = c.post(
            "/api/v1/projects/demo/shots/E1S01/end-frame/upload?script_file=../../../../etc/passwd",
            files={"file": ("f.png", BytesIO(_img_bytes("PNG")), "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert _segment(pm).get("end_frame_image") is None

    def test_illegal_path_returns_400_on_select_and_clear(self, end_frames_client):
        """三个端点共用 `_locate_shot`，选图与清除通道同样须落 400 而非 500。"""
        c, _pm = end_frames_client
        select_resp = c.post(
            "/api/v1/projects/demo.evil/shots/E1S01/end-frame/select",
            json={"script_file": "episode_1.json", "source_path": "storyboards/scene_E1S01.png"},
        )
        assert select_resp.status_code == 400

        clear_resp = c.delete(
            "/api/v1/projects/demo/shots/E1S01/end-frame?script_file=../../../../etc/passwd",
        )
        assert clear_resp.status_code == 400

    def test_corrupt_script_still_maps_to_500(self, end_frames_client):
        """`JSONDecodeError` 是 `ValueError` 子类：剧本文件损坏须落 500 兜底，
        不能被非法 script_file 的 400 分支吞掉，否则错误原因对排查完全失真。"""
        c, pm = end_frames_client
        (pm.get_project_path("demo") / "scripts" / "episode_1.json").write_text("{ not json", encoding="utf-8")

        assert _upload(c, _img_bytes("PNG")).status_code == 500

    def test_non_utf8_script_still_maps_to_500(self, end_frames_client):
        """`UnicodeDecodeError` 同样是 `ValueError` 子类：剧本文件含非法 UTF-8 字节须落 500 兜底，
        不能被非法 script_file 的 400 分支吞掉。"""
        c, pm = end_frames_client
        (pm.get_project_path("demo") / "scripts" / "episode_1.json").write_bytes(b"\xff\xfe not utf-8")

        assert _upload(c, _img_bytes("PNG")).status_code == 500


def _client_with_project(
    tmp_path,
    monkeypatch,
    *,
    content_mode,
    script,
    project_generation_mode=None,
    grid_storyboard=None,
):
    """构造项目 generation_mode 可控的测试 client，用于覆盖生成模式准入判定。"""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo", content_mode=content_mode)
    pm.create_project_metadata("demo", "Demo", "Anime", content_mode)

    project = pm.load_project("demo")
    if project_generation_mode is not None:
        project["generation_mode"] = project_generation_mode
    if grid_storyboard is not None:
        project["grid_storyboard"] = grid_storyboard
    project["episodes"] = [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}]
    pm.save_project("demo", project)
    pm.save_script("demo", script, "episode_1.json", validate=False)

    return _build_client(pm, monkeypatch), pm


def _assert_reference_video_rejected(resp) -> None:
    """断言响应是参考生视频专属的拒绝，而非碰巧同为 400 的其他错误。"""
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == i18n_message("end_frame_reference_video_unsupported")


def _ad_script(shot_id="E1S01") -> dict:
    return {
        "episode": 1,
        "title": "E1",
        "content_mode": "ad",
        "shots": [
            {
                "shot_id": shot_id,
                "section": "hook",
                "duration_seconds": 4,
                "voiceover_text": "t",
                "characters_in_shot": [],
                "image_prompt": "img",
                "video_prompt": "vid",
                "generated_assets": {"status": "pending"},
            }
        ],
    }


class TestReferenceVideoRejection:
    """项目生成模式为 reference_video 时，尾帧三端点一律拒绝。

    判定只看 project.json：ad 剧本骨架不携带剧本级 generation_mode 戳（见 script_generator），
    各创作类型共用这一口径。
    """

    def test_ad_project_level_reference_video_rejects_all_three_endpoints(self, tmp_path, monkeypatch):
        # ad 剧本骨架不携带 generation_mode 戳，参考生视频只由项目级配置声明。
        c, pm = _client_with_project(
            tmp_path, monkeypatch, content_mode="ad", script=_ad_script(), project_generation_mode="reference_video"
        )

        _assert_reference_video_rejected(_upload(c, _img_bytes("PNG"), shot_id="E1S01"))
        _assert_reference_video_rejected(_select(c, "storyboards/whatever.png", shot_id="E1S01"))
        _assert_reference_video_rejected(_delete(c, shot_id="E1S01"))

        assert pm.load_script("demo", "episode_1.json")["shots"][0].get("end_frame_image") is None

    def test_ad_storyboard_route_allows_end_frame(self, tmp_path, monkeypatch):
        # 分镜图生视频的 ad 项目照常放行。
        c, _pm = _client_with_project(
            tmp_path,
            monkeypatch,
            content_mode="ad",
            script=_ad_script(),
            project_generation_mode="storyboard",
        )
        assert _upload(c, _img_bytes("PNG"), shot_id="E1S01").status_code == 200

    def test_drama_reference_video_script_rejected(self, tmp_path, monkeypatch):
        # drama/narration 的参考生视频剧本同样落到这条拒绝上。
        script = {
            "episode": 1,
            "title": "E1",
            "content_mode": "drama",
            "generation_mode": "reference_video",
            "video_units": [{"unit_id": "E1S01", "scenes": [], "props": []}],
        }
        c, _pm = _client_with_project(
            tmp_path, monkeypatch, content_mode="drama", script=script, project_generation_mode="reference_video"
        )
        _assert_reference_video_rejected(_upload(c, _img_bytes("PNG"), shot_id="E1S01"))

    def test_grid_mode_no_regression(self, tmp_path, monkeypatch):
        # 常规宫格分镜路径（storyboard + grid_storyboard）不受影响，尾帧设置照常放行。
        script = {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "novel_text": "t", "duration_seconds": 5}],
        }
        c, pm = _client_with_project(
            tmp_path,
            monkeypatch,
            content_mode="narration",
            script=script,
            project_generation_mode="storyboard",
            grid_storyboard=True,
        )
        resp = _upload(c, _img_bytes("PNG"), shot_id="E1S01")
        assert resp.status_code == 200, resp.text
        assert pm.load_script("demo", "episode_1.json")["segments"][0]["end_frame_image"] == END_FRAME_REL

    def test_script_without_episode_number_rejected_by_project_route(self, tmp_path, monkeypatch):
        # 剧本与文件名都不含集号：判定只需项目生成模式，照常拒绝且不落到 500。
        script = {
            "title": "E1",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "novel_text": "t", "duration_seconds": 5}],
        }
        c, pm = _client_with_project(
            tmp_path, monkeypatch, content_mode="narration", script=script, project_generation_mode="reference_video"
        )
        pm.save_script("demo", script, "custom.json", validate=False)

        resp = c.post(
            "/api/v1/projects/demo/shots/E1S01/end-frame/upload?script_file=custom.json",
            files={"file": ("frame.png", BytesIO(_img_bytes("PNG")), "application/octet-stream")},
        )
        _assert_reference_video_rejected(resp)


class TestValidatorAcceptsWrittenSnapshot:
    def test_written_project_passes_tree_validation(self, end_frames_client):
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG"))
        # end_frames 已登记为允许的项目根目录条目，不被判为未知目录
        assert "end_frames" in DataValidator.ALLOWED_ROOT_ENTRIES
        result = DataValidator(projects_root=str(pm.projects_root)).validate_project_tree("demo")
        assert not [e for e in result.errors if "end_frames" in e]
