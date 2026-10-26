"""大项目性能基准的共享部分：环境、合成项目构建器、进程内 ASGI 应用与计数探针。

调用 :func:`configure` 之前不要 import ``lib`` / ``server``：数据目录与数据库地址在模块导入时就会被读取，
所以本模块的 lib / server 导入一律放在函数体内。
"""

from __future__ import annotations

import asyncio
import builtins
import contextvars
import functools
import importlib
import inspect
import io
import itertools
import json
import os
import random
import shutil
import sys
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT = "demo"
API = "/api/v1"
# 合成项目里角色 / 场景 / 道具的比例（12:10:8）。
ASSET_MIX = (12, 10, 8)
# 被测聚合接口；名字同时是 --only 的取值。
ENDPOINTS = ("project", "cost", "wf_status", "wf_plan", "units")
# 「同一份聚合计算同时在算的份数」按真正做计算的函数计数，与请求是否合流无关。
# 只有项目详情与费用估算会合流；其余接口一次请求就是一份计算，按在途请求数统计。
# 函数改名或挪动时同步改这里，找不到时 install 会直接报错。
COMPUTE_MARKERS = {
    "project": ("lib.workflow.workflow_state", "WorkflowStateService", "get_project_summary"),
    "cost": ("server.services.admission.cost_estimation", "CostEstimationService", "compute"),
}
# 与 lib.infra.content_digest.FILE_DIGEST_RACY_WINDOW_NS 对齐，多留半秒余量。
FILE_DIGEST_RACY_WINDOW_S = 3.5
MEDIA_SUFFIXES = (".mp4", ".mov", ".webm", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")


def configure(data_root: Path) -> None:
    """把数据目录、profile、数据库与鉴权密钥指向基准自己的目录。必须先于任何 lib / server 导入。"""

    data_root.mkdir(parents=True, exist_ok=True)
    # 重建会删掉整个 projects 目录：只接受空目录或带基准标记的目录，防止误指真实数据目录。
    sentinel = data_root / ".arcreel-perf-bench"
    if not sentinel.exists():
        if any(data_root.iterdir()):
            raise SystemExit(f"--data-root {data_root} 不是空目录，也不是基准建的目录，拒绝使用")
        sentinel.touch()
    profile = data_root / "profile"
    profile.mkdir(exist_ok=True)
    (profile / "CLAUDE.md").touch()
    os.environ["ARCREEL_DATA_DIR"] = str(data_root)
    os.environ["ARCREEL_PROFILE_DIR"] = str(profile)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{data_root / 'perf.db'}"
    os.environ["AUTH_ENABLED"] = "true"
    os.environ["AUTH_TOKEN_SECRET"] = "perf-bench-synthetic-secret-0123456789abcdef"
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def data_root() -> Path:
    return Path(os.environ["ARCREEL_DATA_DIR"])


def project_path() -> Path:
    return data_root() / "projects" / PROJECT


# ---------------------------------------------------------------- 合成项目
def _unit_text(rnd: random.Random, chars: list[str], scenes: list[str], props: list[str]) -> str:
    c1, c2, s, p = rnd.choice(chars), rnd.choice(chars), rnd.choice(scenes), rnd.choice(props)
    return (
        f"中景，@[{c1}]站在@[{s}]的屋檐下，雨水顺着瓦片滴落，镜头缓慢推近。\n"
        f"@[{c1}]：{{你终于来了。}}\n@[{c2}]握紧@[{p}]，沉默片刻后转身，远处传来钟声。"
    )


def commit_video(unit_id: str, video_kb: int, script_file: str, *, register: bool = True) -> None:
    """按上传路径的形状提交一条参考生视频：版本激活与产物清单登记在同一次提交里完成。

    ``register=False`` 只提交版本，留给构建末尾一次性补录：逐条登记每次都重写整份清单，构建会变成平方级。
    """

    from lib.artifacts.artifact_registration import register_current_resource_artifact
    from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager

    path = project_path()
    current = path / "reference_videos" / f"{unit_id}.mp4"
    current.parent.mkdir(parents=True, exist_ok=True)
    staged = current.parent / f".{unit_id}.upload.mp4"
    staged.write_bytes(os.urandom(video_kb * 1024))
    VersionManager(path).commit_staged_version(
        "reference_videos",
        unit_id,
        "",
        staged_file=staged,
        current_file=current,
        on_commit=functools.partial(
            register_current_resource_artifact,
            path,
            resource_type="reference_videos",
            resource_id=unit_id,
            script_file=script_file,
        )
        if register
        else None,
        source=MANUAL_UPLOAD_VERSION_SOURCE,
    )


def build(*, episodes: int, units: int, assets: int, video_kb: int, sheet_kb: int, video_ratio: float) -> None:
    """构建参考生视频合成项目：整本源文、每集已确认的单元规划与剧本、按比例已出片的视频。"""

    from lib.artifacts.artifact_activation import activate_artifact_target_state
    from lib.artifacts.artifact_manifest import ArtifactKey
    from lib.artifacts.artifact_registration import register_current_artifact, register_current_artifact_if_provable
    from lib.episode.episode_ledger import SOURCE_FINGERPRINTS_KEY, compute_source_fingerprints
    from lib.episode.episode_source_commands import register_whole_source_file
    from lib.episode.episode_sources import discover_sources
    from lib.infra.json_io import atomic_write_json
    from lib.project.project_manager import ProjectManager
    from lib.script import script_review

    rnd = random.Random(7)
    pm = ProjectManager(data_root())
    pm.create_project(PROJECT)
    pm.create_project_metadata(
        PROJECT, "Perf", "", "drama", extras={"generation_mode": "reference_video", "grid_storyboard": False}
    )
    path = pm.get_project_path(PROJECT)
    novel = "".join(f"第{n}集正文。" + "他走进雨夜的长街，灯火摇曳。" * 214 for n in range(1, episodes + 1))
    with pm.locked_source_registration(PROJECT) as (source_dir, project, _undo):
        (source_dir / "novel.txt").write_text(novel, encoding="utf-8")
        register_whole_source_file(project, "source/novel.txt")
    per = len(novel) // episodes

    total = sum(ASSET_MIX)
    n_char = max(1, assets * ASSET_MIX[0] // total)
    n_scene = max(1, assets * ASSET_MIX[1] // total)
    n_prop = max(1, assets - n_char - n_scene)
    buckets = {
        ("character", "characters", "character_sheet"): {f"角色{i}": "黑发红衣" for i in range(1, n_char + 1)},
        ("scene", "scenes", "scene_sheet"): {f"场景{i}": "雨夜长街" for i in range(1, n_scene + 1)},
        ("prop", "props", "prop_sheet"): {f"道具{i}": "古铜色" for i in range(1, n_prop + 1)},
    }
    specs: dict[str, dict[str, dict[str, str]]] = {}
    for (_kind, folder, sheet_key), names in buckets.items():
        specs[folder] = {}
        for name, desc in names.items():
            rel = f"{folder}/{name}.png"
            (path / folder).mkdir(parents=True, exist_ok=True)
            (path / rel).write_bytes(os.urandom(sheet_kb * 1024))
            specs[folder][name] = {"description": f"{name}，{desc}" * 4, sheet_key: rel}

    def _plan(project: dict[str, Any]) -> None:
        project.update(specs)
        project["episodes"] = [
            {
                "episode": n,
                "title": f"第{n}集",
                "script_file": f"scripts/episode_{n}.json",
                "ledger_status": "consumed",
                "source_range": {
                    "source_file": "source/novel.txt",
                    "start": per * (n - 1),
                    "end": per * n if n < episodes else len(novel),
                },
            }
            for n in range(1, episodes + 1)
        ]
        project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(path, project))

    pm.update_project(PROJECT, _plan)
    for (kind, _folder, _key), names in buckets.items():
        for name in names:
            register_current_artifact(path, ArtifactKey.asset_sheet(kind, name))

    chars, scenes, props = (list(names) for names in buckets.values())
    t0 = time.perf_counter()
    for n in range(1, episodes + 1):
        draft = path / "drafts" / f"episode_{n}"
        draft.mkdir(parents=True, exist_ok=True)
        (path / "source" / f"episode_{n}.txt").write_text(novel[per * (n - 1) : per * n], encoding="utf-8")
        plan_units, video_units, filmed = [], [], []
        for u in range(1, units + 1):
            uid = f"E{n}U{u}"
            text = _unit_text(rnd, chars, scenes, props)
            plan_units.append({"unit_id": uid, "text": text, "duration_seconds": 8})
            assets_done: dict[str, str] = {}
            if rnd.random() < video_ratio:
                filmed.append(uid)
                assets_done = {
                    "video_clip": f"reference_videos/{uid}.mp4",
                    "video_generated_at": "2026-10-01T00:00:00+00:00",
                    "status": "completed",
                }
            video_units.append(
                {
                    "unit_id": uid,
                    "text": text,
                    "duration_seconds": 8,
                    "source_text": "",
                    "generated_assets": assets_done,
                }
            )
        atomic_write_json(draft / "script_plan_reference_units.json", {"units": plan_units})
        script = {"episode": n, "title": f"第{n}集", "content_mode": "drama", "video_units": video_units}
        atomic_write_json(path / "scripts" / f"episode_{n}.json", script)
        register_current_artifact_if_provable(path, ArtifactKey.episode_script_plan(n))
        register_current_artifact(path, ArtifactKey.episode_script(n))
        for uid in filmed:
            commit_video(uid, video_kb, f"scripts/episode_{n}.json", register=False)
        if n % 10 == 0:
            print(f"  built episode {n}/{episodes} ({time.perf_counter() - t0:.0f}s)", flush=True)

    revisions = {
        n: script_review.content_fingerprint(path / "drafts" / f"episode_{n}" / "script_plan_reference_units.json")
        for n in range(1, episodes + 1)
    }

    def _confirm(project: dict[str, Any]) -> None:
        for n, rev in revisions.items():
            script_review.apply_confirmation(project, n, rev, "2026-10-01T00:00:00Z")

    pm.update_project(PROJECT, _confirm)
    activate_artifact_target_state(path, bump_schema=False)


CONTENT_FACT_FIELDS = ("content_digest", "content_size", "content_mtime_ns")


def downgrade_to_v17() -> None:
    """把构建好的项目退回 schema 17 的形态：抹掉清单条目与版本记录上的内容指纹。

    存量项目升级时，内容指纹由启动迁移的 v17→v18 补录写入；基准据此让存量视频走生产补录拿到指纹，
    并测出这一步的耗时。
    """

    from lib.artifacts.artifact_manifest import MANIFEST_FILENAME
    from lib.artifacts.version_manager import VERSION_CONTENT_DIGEST, VersionManager
    from lib.infra.json_io import atomic_write_json

    path = project_path()
    manifest = json.loads((path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    for entry in manifest["entries"].values():
        for field in CONTENT_FACT_FIELDS:
            entry.pop(field, None)
    atomic_write_json(path / MANIFEST_FILENAME, manifest)
    versions_file = VersionManager(path).versions_file
    versions = json.loads(versions_file.read_text(encoding="utf-8"))
    for bucket in versions.values():
        for resource in bucket.values():
            for record in resource["versions"]:
                record.pop(VERSION_CONTENT_DIGEST, None)
    atomic_write_json(versions_file, versions)
    project = json.loads((path / "project.json").read_text(encoding="utf-8"))
    project["schema_version"] = 17
    atomic_write_json(path / "project.json", project)


def video_fact_coverage() -> dict[str, int]:
    """清单里已登记的视频条目中，带内容指纹与不带的条数。"""

    from lib.artifacts.artifact_manifest import MANIFEST_FILENAME

    entries = json.loads((project_path() / MANIFEST_FILENAME).read_text(encoding="utf-8"))["entries"]
    videos = [e for e in entries.values() if str(e.get("artifact_path", "")).endswith(".mp4")]
    with_facts = sum(1 for e in videos if "content_digest" in e)
    return {"videos": len(videos), "with_facts": with_facts}


def run_startup_migration() -> dict[str, Any]:
    """按服务启动的方式跑一遍项目迁移并计时。先清进程内缓存，模拟新进程；操作系统页缓存仍是热的。"""

    from lib.project.project_migrations import run_project_migrations

    clear_caches()
    t = time.perf_counter()
    summary = run_project_migrations(data_root() / "projects")
    elapsed = time.perf_counter() - t
    if summary.migrated != [PROJECT]:
        raise SystemExit(f"startup migration did not migrate {PROJECT}: {summary}")
    return {"startup_migration_s": elapsed, **video_fact_coverage()}


def ensure_project(
    *, episodes: int, units: int, assets: int, video_kb: int, sheet_kb: int, video_ratio: float, rebuild: bool
) -> None:
    """参数与上次构建一致就复用已有项目，否则清掉重建。"""

    marker = data_root() / f"built-{episodes}x{units}x{assets}-v{video_kb}-s{sheet_kb}-r{video_ratio}.ok"
    if rebuild or not marker.exists():
        shutil.rmtree(data_root() / "projects", ignore_errors=True)
        for old in data_root().glob("built-*.ok"):
            old.unlink()
        t = time.perf_counter()
        print(f"building {episodes} episodes x {units} units ...", flush=True)
        build(
            episodes=episodes,
            units=units,
            assets=assets,
            video_kb=video_kb,
            sheet_kb=sheet_kb,
            video_ratio=video_ratio,
        )
        downgrade_to_v17()
        migration = run_startup_migration()
        print(
            f"startup migration v17->v18: {migration['startup_migration_s']:.1f}s, "
            f"video entries with content facts {migration['with_facts']}/{migration['videos']}",
            flush=True,
        )
        (data_root() / "migration.json").write_text(json.dumps(migration))
        marker.write_text("ok")
        # 刚写完的文件落在内容指纹缓存的 racy 窗口里不入缓存，等窗口过去再测，热态才是真热态。
        time.sleep(FILE_DIGEST_RACY_WINDOW_S)
        print(f"built project in {time.perf_counter() - t:.0f}s", flush=True)
    videos = sum(1 for p in project_path().rglob("*.mp4") if not p.name.startswith("."))
    print(f"project={project_path()} mp4 files (formal + version snapshots)={videos}", flush=True)


async def ensure_db() -> None:
    """建表，并给真实内置供应商配一份假凭证：让报价与能力求值走真实路径（不会发出请求）。"""

    from lib.agent.agent_session_store.models import register_models as register_agent_models
    from lib.db import async_session_factory
    from lib.db.base import Base
    from lib.db.engine import async_engine
    from lib.db.models import register_models
    from lib.db.repositories.credential_repository import CredentialRepository

    register_agent_models()
    register_models()
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as session:
        repo = CredentialRepository(session)
        for provider in ("ark", "gemini-aistudio"):
            if not await repo.list_by_provider(provider):
                await repo.create(provider, "default", api_key="perf-fake-key")
        await session.commit()


def clear_caches() -> None:
    """清掉进程内的读缓存，模拟冷态（不含模块导入与首次初始化，那部分用新进程测）。"""

    from lib.artifacts.artifact_manifest import reset_manifest_read_cache_for_tests
    from lib.infra.content_digest import reset_file_digest_cache_for_tests

    reset_file_digest_cache_for_tests()
    reset_manifest_read_cache_for_tests()


# ---------------------------------------------------------------- 应用
def build_app():
    """按生产注册方式挂载被测路由（真实鉴权依赖），不跑应用的 lifespan。"""

    from fastapi import Depends, FastAPI

    from server.auth import get_current_user
    from server.error_handlers import register_error_handlers
    from server.routers import auth, cost_estimation, files, projects, reference_videos

    app = FastAPI()
    register_error_handlers(app)
    guarded = [Depends(get_current_user)]
    for router in (projects.router, cost_estimation.router, reference_videos.router, auth.router):
        app.include_router(router, prefix=API, dependencies=guarded)
    app.include_router(files.public_router, prefix=API)
    return app


def auth_headers() -> dict[str, str]:
    from server.auth import create_token

    return {"Authorization": f"Bearer {create_token('perf')}"}


def endpoint(name: str, episode: int) -> tuple[str, str, dict[str, Any] | None]:
    base = f"{API}/projects/{PROJECT}"
    return {
        "project": ("GET", base, None),
        "cost": ("GET", f"{base}/cost-estimate", None),
        "wf_status": ("GET", f"{base}/workflow-status", None),
        "wf_plan": ("POST", f"{base}/workflow-plan", {}),
        "units": ("GET", f"{base}/reference-videos/episodes/{episode}/units", None),
    }[name]


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


# ---------------------------------------------------------------- 计数探针
REQ: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("perf_req", default=None)


class Probe:
    """进程内计数：线程跳数与排队、清单锁、同时在算的计算份数、媒体文件内容读取。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.computing: dict[str, int] = defaultdict(int)
        self.computing_max: dict[str, int] = defaultdict(int)
        self.lock_fail = 0
        self.media_bytes: dict[str, int] = defaultdict(int)  # 每次打开一个键：<路径>#<序号>
        self._media_fds: dict[int, str] = {}

    def reset_media(self) -> None:
        with self.lock:
            self.media_bytes.clear()

    def media_read(self) -> dict[str, int]:
        """读出了内容的媒体文件打开次数与字节数。只读 1 字节的打开是可读性探测，不算读取内容。"""

        with self.lock:
            reads = [b for b in self.media_bytes.values() if b > 1]
        return {"reads": len(reads), "bytes": sum(reads)}

    def reset_compute_max(self) -> None:
        with self.lock:
            self.computing_max = defaultdict(int, self.computing)

    # -- 安装
    def install(self) -> None:
        self._install_to_thread()
        self._install_manifest_lock()
        self._install_compute_markers()
        self._install_media_reads()

    def _install_to_thread(self) -> None:
        orig = asyncio.to_thread

        async def to_thread(func, /, *args, **kwargs):
            req = REQ.get()
            submit = time.perf_counter()
            box: dict[str, float] = {}

            def run():
                box["start"] = time.perf_counter()
                cpu = time.thread_time()
                try:
                    return func(*args, **kwargs)
                finally:
                    box["run"] = time.perf_counter() - box["start"]
                    box["cpu"] = time.thread_time() - cpu

            try:
                return await orig(run)
            finally:
                if req is not None and "run" in box:
                    req["hops"] += 1
                    req["qwait"] += box["start"] - submit
                    req["run"] += box["run"]
                    req["cpu"] += box["cpu"]

        asyncio.to_thread = to_thread

    def _install_manifest_lock(self) -> None:
        import lib.artifacts.artifact_manifest as am

        adapter = getattr(am, "ProjectArtifactManifestAdapter", None)
        orig_locked = getattr(adapter, "_locked", None)
        if adapter is not None and orig_locked is not None:

            @contextmanager
            def _locked(self_):
                req = REQ.get()
                t0 = time.perf_counter()
                with orig_locked(self_) as fd:
                    if req is not None:
                        req["lock_acq"] += 1
                        req["lock_wait"] += time.perf_counter() - t0
                    yield fd

            adapter._locked = _locked
        portalocker = getattr(am, "portalocker", None)
        if portalocker is not None:
            real_lock = portalocker.lock
            probe = self

            class _Counting:
                def __getattr__(self, name):
                    return getattr(portalocker, name)

                def lock(self, handle, flags):
                    try:
                        real_lock(handle, flags)
                    except Exception:
                        with probe.lock:
                            probe.lock_fail += 1
                        raise

            am.portalocker = _Counting()

    def _enter(self, name: str) -> None:
        with self.lock:
            self.computing[name] += 1
            self.computing_max[name] = max(self.computing_max[name], self.computing[name])

    def _leave(self, name: str) -> None:
        with self.lock:
            self.computing[name] -= 1

    def _wrap_compute(self, name: str, orig):
        if inspect.iscoroutinefunction(orig):

            async def awrapped(*args, **kwargs):
                self._enter(name)
                try:
                    return await orig(*args, **kwargs)
                finally:
                    self._leave(name)

            return functools.wraps(orig)(awrapped)

        def wrapped(*args, **kwargs):
            self._enter(name)
            try:
                return orig(*args, **kwargs)
            finally:
                self._leave(name)

        return functools.wraps(orig)(wrapped)

    def _install_compute_markers(self) -> None:
        for name, (module, cls_name, attr) in COMPUTE_MARKERS.items():
            cls = getattr(importlib.import_module(module), cls_name)
            setattr(cls, attr, self._wrap_compute(name, getattr(cls, attr)))

    def _install_media_reads(self) -> None:
        """统计媒体文件被读出的字节：覆盖 ``os.open``/``os.read`` 与 ``open``/``io.open`` 两条读取途径。"""

        probe = self
        seq = itertools.count()
        real_os_open, real_os_read, real_os_close = os.open, os.read, os.close
        real_open = builtins.open

        def is_media(path: object) -> bool:
            name = os.fsdecode(path) if isinstance(path, (str, bytes, os.PathLike)) else ""
            return name.lower().endswith(MEDIA_SUFFIXES)

        def os_open(path, flags, *args, **kwargs):
            fd = real_os_open(path, flags, *args, **kwargs)
            if not flags & (os.O_WRONLY | os.O_RDWR) and is_media(path):
                with probe.lock:
                    probe._media_fds[fd] = f"{os.fsdecode(path)}#{next(seq)}"
            return fd

        def os_read(fd, n):
            data = real_os_read(fd, n)
            key = probe._media_fds.get(fd)
            if key is not None:
                with probe.lock:
                    probe.media_bytes[key] += len(data)
            return data

        def os_close(fd):
            with probe.lock:
                probe._media_fds.pop(fd, None)
            return real_os_close(fd)

        class _CountingFile:
            def __init__(self, handle, name):
                self._handle, self._key = handle, f"{name}#{next(seq)}"

            def _count(self, n):
                with probe.lock:
                    probe.media_bytes[self._key] += n or 0

            def read(self, *args):
                data = self._handle.read(*args)
                self._count(len(data))
                return data

            def readinto(self, buffer):
                n = self._handle.readinto(buffer)
                self._count(n)
                return n

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return self._handle.__exit__(*exc)

            def __getattr__(self, name):
                return getattr(self._handle, name)

        def counting_open(file, mode="r", *args, **kwargs):
            handle = real_open(file, mode, *args, **kwargs)
            if "b" in mode and not set(mode) & set("wax+") and is_media(file):
                return _CountingFile(handle, os.fsdecode(file))
            return handle

        os.open, os.read, os.close = os_open, os_read, os_close
        builtins.open = io.open = counting_open


def new_request(name: str, started: float) -> dict[str, Any]:
    return {
        "ep": name,
        "t_start": started,
        "hops": 0,
        "qwait": 0.0,
        "run": 0.0,
        "cpu": 0.0,
        "lock_acq": 0,
        "lock_wait": 0.0,
        "abandoned": False,
    }


# ---------------------------------------------------------------- 栈采样
# 叶子帧落在这些位置的线程视为空闲：等队列、等锁、等 IO 多路复用，以及 aiosqlite 工作线程在 C 层等队列。
_IDLE_LEAVES = {
    ("threading.py", "wait"),
    ("queue.py", "get"),
    ("selectors.py", "select"),
    ("thread.py", "_worker"),
    ("core.py", "_connection_worker_thread"),
}


class StackSampler(threading.Thread):
    """py-spy 的进程内替代：每隔 ``interval`` 秒记下所有忙线程的调用栈。

    采样线程本身也要拿 GIL，持有 GIL 的线程跑得越久采样越稀疏，所以只看比例不看绝对次数。
    同一时刻忙线程的份数说明多份计算是否在同时推进；它们是否真在并行，看进程 CPU 占用。
    """

    def __init__(self, interval: float) -> None:
        super().__init__(name="perf-stack-sampler", daemon=True)
        self.interval = interval
        self.stacks: dict[str, int] = defaultdict(int)
        self.busy_hist: dict[int, int] = defaultdict(int)
        self.ticks = 0
        self._stop_event = threading.Event()

    @staticmethod
    def _frame_label(frame) -> str:
        code = frame.f_code
        name = os.path.basename(code.co_filename)
        return f"{name}:{code.co_qualname}"

    def run(self) -> None:
        me = threading.get_ident()
        while not self._stop_event.wait(self.interval):
            busy = 0
            for ident, frame in sys._current_frames().items():
                if ident == me:
                    continue
                if (os.path.basename(frame.f_code.co_filename), frame.f_code.co_name) in _IDLE_LEAVES:
                    continue
                labels = []
                f = frame
                while f is not None:
                    labels.append(self._frame_label(f))
                    f = f.f_back
                self.stacks[";".join(reversed(labels))] += 1
                busy += 1
            self.busy_hist[busy] += 1
            self.ticks += 1

    def stop(self) -> None:
        self._stop_event.set()
        self.join()

    def summary(self, top: int = 25) -> dict[str, Any]:
        """自身（叶子帧）与累计（栈上出现过）占比最高的函数，以及同时忙的线程份数分布。"""

        total = sum(self.stacks.values()) or 1
        self_counts: dict[str, int] = defaultdict(int)
        incl_counts: dict[str, int] = defaultdict(int)
        for stack, n in self.stacks.items():
            frames = stack.split(";")
            self_counts[frames[-1]] += n
            for label in set(frames):
                incl_counts[label] += n

        def ranked(counts: dict[str, int]) -> list[tuple[str, float]]:
            return [(k, round(v / total, 4)) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:top]]

        return {
            "ticks": self.ticks,
            "busy_samples": total,
            "busy_threads_hist": dict(sorted(self.busy_hist.items())),
            "self": ranked(self_counts),
            "inclusive": ranked(incl_counts),
        }

    def write_collapsed(self, path: Path) -> None:
        """flamegraph.pl / speedscope 可读的折叠栈格式。"""

        ranked = sorted(self.stacks.items(), key=lambda kv: -kv[1])
        path.write_text("".join(f"{stack} {n}\n" for stack, n in ranked), encoding="utf-8")
