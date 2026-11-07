"""Shot ending-frame setting/clearing endpoints.

Cover upload/project-image channels sharing snapshots, in-place replacement/clear semantics,
out-of-bounds/missing rejection, and source decoupling: source edits/deletion never affect fixed ending frames.
"""

import asyncio
import json
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

from lib.artifacts.version_manager import VersionManager
from lib.i18n import _ as i18n_message
from lib.infra.json_io import atomic_write_json
from lib.project.data_validator import DataValidator
from lib.project.project_manager import ProjectManager
from lib.script.script_editor import ScriptEditError
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import end_frames
from server.services.currency import upload_finalize
from server.services.project import end_frame as end_frame_service
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.fakes import use_project_manager

END_FRAME_REL = "end_frames/scene_E1S01.png"


class _ScriptIOHooks:
    """Mutable ``ProjectManager`` script IO seam hooks.

    Default real IO passes through; injected ``read`` / ``write`` target precise locked reads/
    writes to create failures/concurrent interleavings without replacing manager internals.
    """

    def __init__(self) -> None:
        self.read: Callable[[Path], dict] | None = None
        self.write: Callable[[Path, dict], None] | None = None

    @staticmethod
    def real_read(path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"剧本文件不存在: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def real_write(path: Path, script: dict) -> None:
        atomic_write_json(path, script)

    def reader(self, path: Path) -> dict:
        return self.read(path) if self.read is not None else self.real_read(path)

    def writer(self, path: Path, script: dict) -> None:
        if self.write is not None:
            self.write(path, script)
        else:
            self.real_write(path, script)


def _img_bytes(fmt="JPEG", size=(8, 8), color=(255, 0, 0)):
    image = Image.new("RGB", size, color)
    buf = BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def _seed_project(tmp_path, hooks: _ScriptIOHooks) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects", script_reader=hooks.reader, script_writer=hooks.writer)
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
    """Connect ProjectManager to ending-frame routes, returning request-ready TestClient."""
    use_project_manager(monkeypatch, pm)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(end_frames.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return TestClient(app)


@pytest.fixture
def script_io(tmp_path) -> _ScriptIOHooks:
    return _ScriptIOHooks()


@pytest.fixture
def end_frames_client(tmp_path, monkeypatch, script_io):
    pm = _seed_project(tmp_path, script_io)
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
        # Normalize JPEG uploads into PNG, matching storyboard uploads.
        with Image.open(pm.get_project_path("demo") / END_FRAME_REL) as img:
            assert img.format == "PNG"

    def test_replacing_overwrites_in_place(self, end_frames_client):
        c, pm = end_frames_client
        _upload(c, _img_bytes("JPEG", size=(8, 8)))
        resp = _upload(c, _img_bytes("PNG", size=(16, 16)))
        assert resp.status_code == 200, resp.text
        # Fixed paths/unchanged fields; file contents replaced.
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
        # Fields store snapshots rather than source-image paths; no structural reference relationship exists.
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
        """Project-image sources exceeding upload-equivalent limits reject (read_project_image).

        Dedicated text + 400: bodies contain path strings only, so 413 payload-too-large semantics do not apply,
        distinct from upload 413 + upload_too_large (test_shot_uploads_router).
        """
        c, pm = end_frames_client
        monkeypatch.setattr(end_frame_service, "UPLOAD_IMAGE_MAX_BYTES", 16)
        _write_source_image(pm, "storyboards/scene_E1S02.png", _img_bytes("PNG", size=(64, 64)))

        resp = _select(c, "storyboards/scene_E1S02.png")
        assert resp.status_code == 400
        # max_mb is byte caps divided by 1 MB; this test's 16-byte cap yields zero.
        assert resp.json()["detail"] == i18n_message(
            "end_frame_source_too_large", max_mb=0, path="storyboards/scene_未命名集 · S02.png"
        )
        assert _segment(pm).get("end_frame_image") is None

    def test_source_image_read_is_bounded(self, end_frames_client, monkeypatch):
        """Oversized sources never load fully: individual reads cap at limit +1, stopping once size excess is known."""
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
    """Snapshots/source images are fully decoupled; later source edits/deletion never alter fixed ending frames."""

    def test_source_rewrite_and_delete_leave_end_frame_intact(self, end_frames_client):
        c, pm = end_frames_client
        source_rel = "storyboards/scene_E1S02.png"
        _write_source_image(pm, source_rel, _img_bytes("PNG", size=(12, 12), color=(255, 0, 0)))
        _select(c, source_rel)

        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        before = snapshot.read_bytes()

        # Regenerate source content/dimensions, then delete, simulating version rollback/resource cleanup.
        _write_source_image(pm, source_rel, _img_bytes("PNG", size=(24, 24), color=(0, 0, 255)))
        (pm.get_project_path("demo") / source_rel).unlink()

        assert snapshot.read_bytes() == before
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL

    def test_source_version_rollback_leaves_end_frame_intact(self, end_frames_client):
        """Source rollback never changes fixed ending frames; snapshot paths are independent of source version axes."""
        c, pm = end_frames_client
        source_rel = "storyboards/scene_E1S02.png"
        source_abs = pm.get_project_path("demo") / source_rel
        vm = VersionManager(pm.get_project_path("demo"))

        _write_source_image(pm, source_rel, _img_bytes("PNG", size=(12, 12), color=(255, 0, 0)))
        vm.add_version("storyboards", "E1S02", "v1", source_file=source_abs)
        _select(c, source_rel)

        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        before = snapshot.read_bytes()

        # Regenerate source v2, then roll back to v1.
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

        # Clearing one leaves the other unaffected.
        _delete(c, shot_id="E1S01")
        assert _segment(pm, 0)["end_frame_image"] is None
        assert _segment(pm, 1)["end_frame_image"] == "end_frames/scene_E1S02.png"
        assert (pm.get_project_path("demo") / "end_frames/scene_E1S02.png").exists()


class _InterleavedResponses(NamedTuple):
    """Responses from both `_interleave_across_critical_section` requests."""

    first: httpx.Response
    second: httpx.Response


def _interleave_across_critical_section(
    script_io: _ScriptIOHooks, first: Callable[[], httpx.Response], second: Callable[[], httpx.Response]
) -> _InterleavedResponses:
    """Pause `first` inside script locks, confirm `second` waits outside, then release and return responses.

    Hooks align interleavings rather than lucky scheduling: script-write seams run at the last step before `locked_script`
    exits, after first file side effects but before field persistence, precisely the
    dangling-reference window where others could edit between file writes and field writes. Later requests must
    remain locked out; completion would prove operations are not truly exclusive.

    Staggering requests also matters: TestClient uses independent threads/loops, while FastAPI
    `include_router` lazily expands routes on first matches without locks; simultaneous first requests hit
    half-built tables and randomly return route 404. Send second requests only after first routing completes.

    Asserting later requests cannot finish uses fixed observation windows, a one-way falsification: broken exclusion on slow
    machines may miss detection because work has not finished, but never falsely rejects compliant implementations, avoiding
    timing sensitivity. Entire unlocked set/clear operations take milliseconds, much shorter than windows.
    """
    entered_section = threading.Event()
    resume = threading.Event()
    gate_lock = threading.Lock()
    gate_open = True

    def _gated_persist(path: Path, script: dict) -> None:
        nonlocal gate_open
        with gate_lock:
            take_gate, gate_open = gate_open, False
        if take_gate:
            entered_section.set()
            assert resume.wait(timeout=10), "主线程未放行临界区"
        script_io.real_write(path, script)

    script_io.write = _gated_persist

    results: dict[str, httpx.Response] = {}
    errors: dict[str, BaseException] = {}

    def _run(key: str, call: Callable[[], httpx.Response]) -> threading.Thread:
        def _target() -> None:
            try:
                results[key] = call()
            except Exception as exc:  # Reraise on main threads, never stderr-only.
                errors[key] = exc

        thread = threading.Thread(target=_target)
        thread.start()
        return thread

    t_first = _run("first", first)
    try:
        assert entered_section.wait(timeout=10), "先到的请求没能进入剧本锁临界区"

        t_second = _run("second", second)
        # Earlier requests remain inside critical sections; later requests must have no chance of finishing.
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
    """Interleaved set/clear must keep fields/snapshot files consistent without dangling references."""

    def test_clear_blocked_until_set_leaves_critical_section(self, end_frames_client, script_io):
        """Setting enters first; clearing waits for the entire operation, finally winning without residual references."""
        c, pm = end_frames_client
        snapshot = pm.get_project_path("demo") / END_FRAME_REL

        results = _interleave_across_critical_section(
            script_io,
            first=lambda: _upload(c, _img_bytes("PNG")),
            second=lambda: _delete(c),
        )

        assert results.first.status_code == 200, results.first.text
        assert results.second.status_code == 200, results.second.text
        # Later clearing runs entirely after setting: null fields delete snapshot files.
        assert _segment(pm).get("end_frame_image") is None
        assert not snapshot.exists()

    def test_set_blocked_until_clear_leaves_critical_section(self, end_frames_client, script_io):
        """Clearing enters first; setting waits for completion, leaving nonempty fields and existing snapshots."""
        c, pm = end_frames_client
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        assert _upload(c, _img_bytes("PNG")).status_code == 200

        results = _interleave_across_critical_section(
            script_io,
            first=lambda: _delete(c),
            second=lambda: _upload(c, _img_bytes("PNG")),
        )

        assert results.first.status_code == 200, results.first.text
        assert results.second.status_code == 200, results.second.text
        # Later setting runs entirely after clearing: nonempty fields require snapshot files, never dangling references.
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        assert snapshot.exists()


def _fail_first_persist(script_io: _ScriptIOHooks) -> None:
    """Fail the next script persistence once, then restore real writes for compensation's second locked invocation.

    Rollback also uses `locked_script` to inspect/update/persist snapshots; always rejecting writes
    would break rollback persistence too, so fail the first only.
    """
    calls = {"n": 0}

    def _boom(path: Path, script: dict) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("simulated persist failure")
        script_io.real_write(path, script)

    script_io.write = _boom


def _inject_concurrent_takeover_before_nth_load(
    script_io: _ScriptIOHooks, key, target: Path, content: bytes | None, n: int
) -> None:
    """Simulate concurrent operations finishing fully before rollback reacquires locks.

    Rollback checks operation generations, not bytes (see end_frame.py degenerate-value docs),
    so change generation counters as well as bytes to reproduce guarded takeovers.
    `content=None` means concurrent clearing/deletion; other values mean setting.

    Compensation calls `locked_script` again; its read begins rollback critical sections. Inject takeover at read n,
    equivalent to completion immediately before rollback acquires locks. Script-read seams
    serve both unlocked `load_script` and locked read-modify-write, counting both critical-section starts.
    """
    calls = {"n": 0}

    def _load_with_race(path: Path) -> dict:
        calls["n"] += 1
        if calls["n"] == n:
            end_frame_service._advance_shot_generation(key)
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(content)
        return script_io.real_read(path)

    script_io.read = _load_with_race


class TestPersistFailureRestoresSnapshot:
    """Failed script persistence inside critical sections restores preoperation snapshots without dangling references/silent loss."""

    def test_set_failure_restores_previous_snapshot_bytes(self, end_frames_client, script_io):
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG", size=(8, 8)))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        before = snapshot.read_bytes()

        _fail_first_persist(script_io)
        resp = _upload(c, _img_bytes("PNG", size=(16, 16)))
        assert resp.status_code == 500

        # Unchanged fields from skipped writeback require snapshots restored to prewrite contents, not replacement bytes.
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        assert snapshot.read_bytes() == before

    def test_set_failure_on_first_write_removes_snapshot(self, end_frames_client, script_io):
        """Failed first-time setting with no previous snapshot must undo entirely, leaving no orphan files."""
        c, pm = end_frames_client
        snapshot = pm.get_project_path("demo") / END_FRAME_REL

        _fail_first_persist(script_io)
        resp = _upload(c, _img_bytes("PNG"))
        assert resp.status_code == 500

        assert _segment(pm).get("end_frame_image") is None
        assert not snapshot.exists()

    def test_clear_failure_restores_deleted_snapshot(self, end_frames_client, script_io):
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG"))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        before = snapshot.read_bytes()

        _fail_first_persist(script_io)
        resp = _delete(c)
        assert resp.status_code == 500

        # Unchanged original pointers require snapshot restoration, or field rollback points to deleted files.
        assert _segment(pm)["end_frame_image"] == END_FRAME_REL
        assert snapshot.exists()
        assert snapshot.read_bytes() == before

    def test_set_failure_skips_restore_when_concurrent_write_supersedes(self, end_frames_client, script_io):
        """Advanced shot-operation generations when rollback reacquires locks prove concurrent takeover;
        skip rollback rather than overwrite successfully persisted new bytes with stale contents."""
        c, pm = end_frames_client
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        key = ("demo", "episode_1.json", "E1S01")
        concurrent_bytes = _img_bytes("PNG", size=(32, 32))

        _fail_first_persist(script_io)
        # Load order: _locate_shot(1) → failing locked_script(2) → rollback locked_script(3)
        _inject_concurrent_takeover_before_nth_load(script_io, key, snapshot, concurrent_bytes, n=3)

        resp = _upload(c, _img_bytes("PNG", size=(16, 16)))
        assert resp.status_code == 500

        assert snapshot.read_bytes() == concurrent_bytes

    def test_clear_failure_skips_restore_when_concurrent_write_recreates_snapshot(self, end_frames_client, script_io):
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG"))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        key = ("demo", "episode_1.json", "E1S01")
        concurrent_bytes = _img_bytes("PNG", size=(40, 40))

        _fail_first_persist(script_io)
        _inject_concurrent_takeover_before_nth_load(script_io, key, snapshot, concurrent_bytes, n=3)

        resp = _delete(c)
        assert resp.status_code == 500

        # Skipped rollback retains concurrent new file contents, never overwriting with deleted states.
        assert snapshot.exists()
        assert snapshot.read_bytes() == concurrent_bytes

    def test_clear_failure_skips_restore_when_concurrent_clear_wins(self, end_frames_client, script_io):
        """Degenerate case: failed clearing deleted files, with expected None identical to no-takeover expectations;
        another concurrent clearing succeeds, likewise leaving no files.
        Byte-only comparison cannot distinguish takeover, incorrectly restoring old snapshots as orphans.
        Generation checks detect concurrent clearing advances and correctly skip rollback."""
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG"))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        key = ("demo", "episode_1.json", "E1S01")

        _fail_first_persist(script_io)
        _inject_concurrent_takeover_before_nth_load(script_io, key, snapshot, None, n=3)

        resp = _delete(c)
        assert resp.status_code == 500

        # Skipped rollback preserves concurrent deletion, never restoring old bytes as orphan files.
        assert not snapshot.exists()

    def test_set_failure_skips_restore_when_concurrent_takeover_uses_other_alias(self, end_frames_client, script_io):
        """Alias normalization: failed operations use `scripts/episode_1.json`;
        unnormalized generations differ from concurrent `episode_1.json` canonical keys,
        hiding takeover and letting old-byte rollback overwrite successful concurrent writes."""
        c, pm = end_frames_client
        snapshot = pm.get_project_path("demo") / END_FRAME_REL
        normalized_key = ("demo", "episode_1.json", "E1S01")
        concurrent_bytes = _img_bytes("PNG", size=(48, 48))

        _fail_first_persist(script_io)
        _inject_concurrent_takeover_before_nth_load(script_io, normalized_key, snapshot, concurrent_bytes, n=3)

        resp = c.post(
            "/api/v1/projects/demo/shots/E1S01/end-frame/upload?script_file=scripts/episode_1.json",
            files={"file": ("f.png", BytesIO(_img_bytes("PNG", size=(16, 16))), "application/octet-stream")},
        )
        assert resp.status_code == 500

        # Unnormalized generation keys miss takeovers, letting rollback overwrite concurrent new bytes with old contents.
        assert snapshot.read_bytes() == concurrent_bytes

    def test_set_failure_restore_uses_bytes_read_inside_critical_section(self, end_frames_client, script_io):
        """Stale baselines: prereading `old_bytes` before script locks captures precritical-section contents,
        not contents at critical-section entry. Here do not advance generations; rewrite files during this
        operation's own script read at entry. Generation checks cannot block rollback
        because nobody claims takeover; restoring rewritten contents distinguishes baselines read
        inside locks from prereads, which would overwrite those rewrites with old bytes."""
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG", size=(8, 8)))
        snapshot = pm.get_project_path("demo") / END_FRAME_REL

        calls = {"n": 0}
        content_at_critical_section_entry = _img_bytes("PNG", size=(56, 56))

        def _load_with_rewrite(path: Path) -> dict:
            calls["n"] += 1
            # Reads: _locate_shot(1) → failing operation locked_script(2)
            if calls["n"] == 2:
                snapshot.write_bytes(content_at_critical_section_entry)
            return script_io.real_read(path)

        script_io.read = _load_with_rewrite
        _fail_first_persist(script_io)

        resp = _upload(c, _img_bytes("PNG", size=(16, 16)))
        assert resp.status_code == 500

        # Read baselines inside critical sections after rewrites, restoring those contents on rollback;
        # prereading outside locks would capture old contents and overwrite concurrent rewrites.
        assert snapshot.read_bytes() == content_at_critical_section_entry


class TestErrorMapping:
    """Domain-error HTTP mappings agree across three endpoints without server-path leaks."""

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
        """Out-of-bounds script shot IDs must reject snapshot paths rather than write outside projects."""
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
        """Absent/bypassed Content-Length still rejects by actual bytes read."""
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
        """Invalid project-name ValueError from `get_project_path` maps to 400, never fallback 500."""
        c, _pm = end_frames_client
        resp = c.post(
            "/api/v1/projects/demo.evil/shots/E1S01/end-frame/upload?script_file=episode_1.json",
            files={"file": ("f.png", BytesIO(_img_bytes("PNG")), "application/octet-stream")},
        )
        assert resp.status_code == 400

    def test_illegal_script_file_returns_400(self, end_frames_client):
        """Traversal script_file ValueError from `load_script` `_safe_subpath` maps to 400."""
        c, pm = end_frames_client
        resp = c.post(
            "/api/v1/projects/demo/shots/E1S01/end-frame/upload?script_file=../../../../etc/passwd",
            files={"file": ("f.png", BytesIO(_img_bytes("PNG")), "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert _segment(pm).get("end_frame_image") is None

    def test_illegal_path_returns_400_on_select_and_clear(self, end_frames_client):
        """All endpoints share `_locate_shot`; select/clear also return 400 rather than 500."""
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
        """`JSONDecodeError` subclasses `ValueError`: corrupt scripts require fallback 500,
        never invalid-script_file 400, which would misrepresent diagnostic causes."""
        c, pm = end_frames_client
        (pm.get_project_path("demo") / "scripts" / "episode_1.json").write_text("{ not json", encoding="utf-8")

        assert _upload(c, _img_bytes("PNG")).status_code == 500

    def test_non_utf8_script_still_maps_to_500(self, end_frames_client):
        """`UnicodeDecodeError` also subclasses `ValueError`: invalid UTF-8 scripts require fallback 500,
        never invalid-script_file 400."""
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
    """Construct clients with controlled project generation_mode for admission tests."""
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
    """Assert reference-video-specific rejection rather than coincidental other 400s."""
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
    """Reference-video projects reject all three ending-frame endpoints.

    Decide from project.json only: ad skeletons lack script-level generation_mode stamps (script_generator);
    all creation types share this rule.
    """

    def test_ad_project_level_reference_video_rejects_all_three_endpoints(self, tmp_path, monkeypatch):
        # ad skeletons lack generation_mode stamps; reference video is declared project-wide only.
        c, pm = _client_with_project(
            tmp_path, monkeypatch, content_mode="ad", script=_ad_script(), project_generation_mode="reference_video"
        )

        _assert_reference_video_rejected(_upload(c, _img_bytes("PNG"), shot_id="E1S01"))
        _assert_reference_video_rejected(_select(c, "storyboards/whatever.png", shot_id="E1S01"))
        _assert_reference_video_rejected(_delete(c, shot_id="E1S01"))

        assert pm.load_script("demo", "episode_1.json")["shots"][0].get("end_frame_image") is None

    def test_ad_storyboard_route_allows_end_frame(self, tmp_path, monkeypatch):
        # Storyboard image-to-video ad projects still pass.
        c, _pm = _client_with_project(
            tmp_path,
            monkeypatch,
            content_mode="ad",
            script=_ad_script(),
            project_generation_mode="storyboard",
        )
        assert _upload(c, _img_bytes("PNG"), shot_id="E1S01").status_code == 200

    def test_drama_reference_video_script_rejected(self, tmp_path, monkeypatch):
        # Drama/narration reference-video scripts share this rejection.
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
        # Normal storyboard + grid_storyboard routes remain unaffected, admitting ending frames.
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
        # Scripts/filenames lack episode numbers; project modes alone determine rejection, never 500.
        script = {
            "title": "E1",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "novel_text": "t", "duration_seconds": 5}],
        }
        c, pm = _client_with_project(
            tmp_path, monkeypatch, content_mode="narration", script=script, project_generation_mode="reference_video"
        )
        # Script writers reject filenames without episode numbers; direct writes simulate external files.
        (pm.get_project_path("demo") / "scripts" / "custom.json").write_text(json.dumps(script), encoding="utf-8")

        resp = c.post(
            "/api/v1/projects/demo/shots/E1S01/end-frame/upload?script_file=custom.json",
            files={"file": ("frame.png", BytesIO(_img_bytes("PNG")), "application/octet-stream")},
        )
        _assert_reference_video_rejected(resp)


class TestValidatorAcceptsWrittenSnapshot:
    def test_written_project_passes_tree_validation(self, end_frames_client):
        c, pm = end_frames_client
        _upload(c, _img_bytes("PNG"))
        # end_frames is an allowed root entry, never unknown directories.
        assert "end_frames" in DataValidator.ALLOWED_ROOT_ENTRIES
        result = DataValidator(projects_dir=str(pm.projects_dir)).validate_project_tree("demo")
        assert not [e for e in result.errors if "end_frames" in e]
