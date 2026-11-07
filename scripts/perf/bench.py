"""Large-project benchmark entry point; see the adjacent README.md.

uv run python -m scripts.perf.bench all                 # Build 90 episodes, measure idle/peak, print summary
uv run python -m scripts.perf.bench idle --only cost    # Measure one interface only
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import cProfile
import io
import json
import math
import os
import pstats
import random
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.perf import harness as h
from scripts.perf.harness import ENDPOINTS, REQ, pct

DEFAULT_DATA_ROOT = Path(tempfile.gettempdir()) / "arcreel-perf-bench"


def _out_dir() -> Path:
    out = h.data_root() / "out"
    out.mkdir(exist_ok=True)
    return out


def _ensure(a: argparse.Namespace) -> None:
    h.ensure_project(
        episodes=a.episodes,
        units=a.units,
        assets=a.assets,
        video_kb=a.video_kb,
        sheet_kb=a.sheet_kb,
        video_ratio=a.video_ratio,
        rebuild=a.rebuild,
    )


def _client(app):
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://perf", headers=h.auth_headers(), timeout=None
    )


async def _timed(client, probe: h.Probe | None, name: str, episode: int) -> dict[str, Any]:
    method, url, body = h.endpoint(name, episode)
    rec = h.new_request(name, time.perf_counter())
    token = REQ.set(rec)
    try:
        r = await client.request(method, url, json=body)
    finally:
        REQ.reset(token)
    rec["t"] = time.perf_counter() - rec["t_start"]
    rec["status"] = r.status_code
    if r.status_code >= 400:
        rec["err"] = r.text[:300]
    if probe is not None:
        rec["media"] = probe.media_read()
    return rec


# ---------------------------------------------------------------- Idle
async def _first_open(name: str, episode: int) -> dict[str, Any]:
    """First request in a fresh process after service restart; exclude module imports/table creation."""

    await h.ensure_db()
    probe = h.Probe()
    probe.install()
    async with _client(h.build_app()) as c:
        return await _timed(c, probe, name, episode)


async def _idle_in_process(names: list[str], episode: int, reps: int) -> dict[str, Any]:
    await h.ensure_db()
    probe = h.Probe()
    probe.install()
    out: dict[str, Any] = {}
    async with _client(h.build_app()) as c:
        for name in names:
            h.clear_caches()
            cold = await _timed(c, None, name, episode)
            warm = []
            for _ in range(reps):
                probe.reset_media()
                warm.append(await _timed(c, probe, name, episode))
            out[name] = {"cache_cleared": cold, "warm": warm}
    return out


def cmd_idle(a: argparse.Namespace) -> dict[str, Any]:
    _ensure(a)
    names = a.only or list(ENDPOINTS)
    print(f"\n== idle: first open after restart (one fresh process per endpoint), episode={a.ep} ==", flush=True)
    first: dict[str, Any] = {}
    for name in names:
        cmd = [sys.executable, "-m", "scripts.perf.bench", "_first-open", name, "--ep", str(a.ep)]
        p = subprocess.run(
            [*cmd, "--data-root", str(h.data_root())],
            capture_output=True,
            text=True,
            cwd=h.REPO_ROOT,
            check=False,
        )
        line = next((x for x in p.stdout.splitlines() if x.startswith("JSON")), None)
        if line is None:
            print(f"  {name:10s} FAILED\n{p.stderr[-3000:]}", flush=True)
            continue
        first[name] = json.loads(line[4:])
        r = first[name]
        print(
            f"  {name:10s} {r['t']:7.2f}s status={r['status']} media_read={r['media']} {r.get('err', '')}", flush=True
        )

    print(f"\n== idle: in-process, caches cleared once, then {a.reps} warm requests ==", flush=True)
    rows = asyncio.run(_idle_in_process(names, a.ep, a.reps))
    result: dict[str, Any] = {}
    for name, r in rows.items():
        warm_t = [x["t"] for x in r["warm"]]
        last = r["warm"][-1]
        result[name] = {
            "first_open_s": first.get(name, {}).get("t"),
            "first_open_media": first.get(name, {}).get("media"),
            "cache_cleared_s": r["cache_cleared"]["t"],
            "warm_s": warm_t,
            "p50": pct(warm_t, 50),
            "p95": pct(warm_t, 95),
            "warm_media": last["media"],
            "hops": last["hops"],
            "lock_acq": last["lock_acq"],
            "status": last["status"],
        }
        print(
            f"  {name:10s} cache-cleared={r['cache_cleared']['t']:7.2f}s warm p50={pct(warm_t, 50):6.2f}s "
            f"p95={pct(warm_t, 95):6.2f}s status={last['status']} thread_hops={last['hops']} "
            f"manifest_lock_acq={last['lock_acq']} media_read={last['media']} {last.get('err', '')}",
            flush=True,
        )
    (_out_dir() / f"idle-{a.tag}.json").write_text(json.dumps(result, indent=1))
    return result


# ---------------------------------------------------------------- Profiling
def cmd_profile(a: argparse.Namespace) -> None:
    """One cache-cleared and one warm request per interface; cProfile output in out/<接口>-<cold|warm>.prof."""

    _ensure(a)

    async def run():
        await h.ensure_db()

        async def inline(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        # Expose synchronous thread cores to cProfile by inlining thread calls onto the event-loop thread while profiling.
        asyncio.to_thread = inline
        async with _client(h.build_app()) as c:
            for name in a.only or ENDPOINTS:
                method, url, body = h.endpoint(name, a.ep)
                for phase in ("cold", "warm"):
                    if phase == "cold":
                        h.clear_caches()
                    prof = cProfile.Profile()
                    t = time.perf_counter()
                    prof.enable()
                    r = await c.request(method, url, json=body)
                    prof.disable()
                    path = _out_dir() / f"{name}-{phase}.prof"
                    prof.dump_stats(path)
                    buf = io.StringIO()
                    pstats.Stats(prof, stream=buf).sort_stats("cumulative").print_stats(r"(lib|server)/", a.top)
                    text = buf.getvalue()
                    print(
                        f"\n######## {name} {phase} wall={time.perf_counter() - t:.2f}s status={r.status_code} {path}"
                    )
                    print(text[text.find("ncalls") :] if "ncalls" in text else text)

    asyncio.run(run())


# ---------------------------------------------------------------- Peak
def _complete_video(unit_id: str, episode: int, video_kb: int) -> None:
    """Complete one video task: commit version/register manifest, then write the artifact into the script."""

    from lib.project.project_manager import get_project_manager
    from server.services.tasks.reference_video_tasks import apply_unit_video_assets

    script_file = f"scripts/episode_{episode}.json"
    h.commit_video(unit_id, video_kb, script_file)
    with get_project_manager().locked_script(h.PROJECT, f"episode_{episode}.json", validate=False) as script:
        apply_unit_video_assets(script, unit_id, video_uri=None, thumb_rel=None)


async def _peak(a: argparse.Namespace) -> dict[str, Any]:
    await h.ensure_db()
    probe = h.Probe()
    probe.install()
    names = a.only or list(ENDPOINTS)
    rnd = random.Random(1)
    reqs: list[dict[str, Any]] = []
    inflight: dict[str, int] = defaultdict(int)
    inflight_max: dict[str, int] = defaultdict(int)
    samples: list[dict[str, Any]] = []
    probes: dict[str, list[float]] = defaultdict(list)
    probe_errors: dict[str, int] = defaultdict(int)
    writes: list[float] = []
    stop = asyncio.Event()
    drained = asyncio.Event()
    tasks: set[asyncio.Task] = set()
    media_url = f"{h.API}/files/{h.PROJECT}/" + next(
        p.relative_to(h.project_path()).as_posix()
        for p in sorted((h.project_path() / "reference_videos").glob("*.mp4"))
        if not p.name.startswith(".")
    )

    async with _client(h.build_app()) as c:
        print("warming up (one request per endpoint, not counted) ...", flush=True)
        for name in names:
            await _timed(c, None, name, a.ep)
        loop = asyncio.get_running_loop()
        executor = loop._default_executor
        workers = executor._max_workers if executor else None
        print(f"os.cpu_count()={os.cpu_count()} default executor max_workers={workers}", flush=True)
        probe.reset_compute_max()
        t0 = time.perf_counter()

        async def server_req(rec: dict[str, Any], episode: int) -> None:
            name = rec["ep"]
            REQ.set(rec)
            method, url, body = h.endpoint(name, episode)
            inflight[name] += 1
            inflight_max[name] = max(inflight_max[name], inflight[name])
            try:
                r = await c.request(method, url, json=body)
                rec["status"] = r.status_code
            except Exception as exc:
                rec["status"] = repr(exc)[:120]
            finally:
                inflight[name] -= 1
                if stop.is_set() and not any(inflight.values()):
                    drained.set()
                rec["server_s"] = time.perf_counter() - t0 - rec["t_start"]

        class Client:
            """Frontend refetch behavior (useProjectEventsSSE): entity-change batch precedes task-terminal batch.

            Entity batches refetch details/units; production state and workflow planning follow project
            snapshots, with cost estimates debounced 0.5 seconds. Task-terminal batches do not refetch
            details, only invalidate units; production/workflow follow task fingerprints. New same-
            interface requests replace old ones; abandoned requests still finish computing server-side."""

            def __init__(self) -> None:
                self.pending: dict[str, dict[str, Any]] = {}
                self.cost_timer: asyncio.Task | None = None

            def fire(self, name: str, episode: int) -> None:
                prev = self.pending.get(name)
                if prev is not None and "server_s" not in prev:
                    prev["abandoned"] = True
                rec = h.new_request(name, time.perf_counter() - t0)
                reqs.append(rec)
                self.pending[name] = rec
                task = asyncio.create_task(server_req(rec, episode))
                tasks.add(task)
                task.add_done_callback(tasks.discard)

            def on_video_done(self, episode: int) -> None:
                for name in ("project", "units", "wf_status", "wf_plan"):  # Entity-change batch.
                    if name in names:
                        self.fire(name, episode)
                for name in ("units", "wf_status", "wf_plan"):  # Task-terminal batch.
                    if name in names:
                        self.fire(name, episode)
                if "cost" in names:
                    if self.cost_timer is not None and not self.cost_timer.done():
                        self.cost_timer.cancel()

                    async def debounced():
                        await asyncio.sleep(0.5)
                        self.fire("cost", episode)

                    self.cost_timer = asyncio.create_task(debounced())

        clients = [Client() for _ in range(a.clients)]

        async def writer():
            while not stop.is_set():
                await asyncio.sleep(rnd.uniform(*a.interval))
                if stop.is_set():
                    break
                episode = rnd.randint(1, a.episodes)
                unit = f"E{episode}U{rnd.randint(1, a.units)}"
                t = time.perf_counter()
                await asyncio.to_thread(_complete_video, unit, episode, a.video_kb)
                writes.append(time.perf_counter() - t)
                for cl in clients:
                    cl.on_video_done(episode)

        async def light(name: str, method: str, url: str, headers: dict[str, str], ok: int):
            while not stop.is_set():
                t = time.perf_counter()
                r = await c.request(method, url, headers=headers)
                probes[name].append(time.perf_counter() - t)
                if r.status_code != ok:
                    probe_errors[name] += 1
                await asyncio.sleep(0.2)

        async def sampler():
            while True:
                t = time.perf_counter()
                cpu = time.process_time()
                await asyncio.sleep(0.5)
                wall = time.perf_counter() - t
                samples.append(
                    {
                        "t": time.perf_counter() - t0,
                        "loop_lag": wall - 0.5,
                        # Process CPU cores: simultaneous computations staying near 1 indicate GIL serialization.
                        "cpu_cores": (time.process_time() - cpu) / wall,
                        "qsize": executor._work_queue.qsize() if executor else 0,
                        "inflight": dict(inflight),
                        "computing": dict(probe.computing),
                    }
                )

        bg = [
            asyncio.create_task(writer()),
            asyncio.create_task(light("media_range", "GET", media_url, {"Range": "bytes=0-65535"}, 206)),
            asyncio.create_task(light("auth_verify", "GET", f"{h.API}/auth/verify", {}, 200)),
        ]
        smp = asyncio.create_task(sampler())
        stacks = h.StackSampler(1 / a.sample_hz) if a.sample_hz else None
        if stacks is not None:
            stacks.start()
        while time.perf_counter() - t0 < a.duration:
            await asyncio.sleep(10)
            s = samples[-1] if samples else {}
            print(
                f"t={time.perf_counter() - t0:5.0f}s writes={len(writes)} thread_queue={s.get('qsize')} "
                f"inflight={s.get('inflight')} computing={s.get('computing')}",
                flush=True,
            )
        stop.set()
        t_stop = time.perf_counter()
        if stacks is not None:
            stacks.stop()
        await asyncio.gather(*bg, return_exceptions=True)
        # Last-write cost estimation may still be debounced; wait until sent before deciding whether requests remain.
        await asyncio.gather(*(cl.cost_timer for cl in clients if cl.cost_timer), return_exceptions=True)
        # In-flight count can temporarily reach zero during debounce and set drained before later debounced requests finish.
        drained.clear()
        if any(inflight.values()):
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(drained.wait(), a.drain)
        drain_s = time.perf_counter() - t_stop
        if stacks is not None:
            # Write results after drain timing, outside the event loop, without delaying still-running requests.
            await asyncio.to_thread(stacks.write_collapsed, _out_dir() / f"peak-{a.tag}.stacks.txt")
        smp.cancel()
        leftover = {k: v for k, v in inflight.items() if v}
        # Unfinished requests use drain-deadline elapsed time (a lower bound) and are marked unfinished.
        for r in reqs:
            if "server_s" not in r:
                r["server_s"] = time.perf_counter() - t0 - r["t_start"]
                r["unfinished"] = True

    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reqs:
        by[r["ep"]].append(r)
    endpoints: dict[str, Any] = {}
    for name in names:
        rs = by.get(name, [])
        all_s = [r["server_s"] for r in rs]
        delivered = [r["server_s"] for r in rs if not r["abandoned"]]
        computing_max = probe.computing_max.get(name) if name in h.COMPUTE_MARKERS else inflight_max.get(name, 0)
        endpoints[name] = {
            "n": len(rs),
            "abandoned": len(rs) - len(delivered),
            "unfinished": sum(1 for r in rs if r.get("unfinished")),
            "errors": sum(1 for r in rs if not r.get("unfinished") and r.get("status") != 200),
            "p50": pct(all_s, 50),
            "p95": pct(all_s, 95),
            "max": max(all_s, default=float("nan")),
            "delivered_p95": pct(delivered, 95),
            "inflight_max": inflight_max.get(name, 0),
            "computing_max": computing_max,
            "mean_thread_hops": statistics.mean(r["hops"] for r in rs) if rs else 0,
            "mean_thread_qwait_s": statistics.mean(r["qwait"] for r in rs) if rs else 0,
            "mean_lock_wait_s": statistics.mean(r["lock_wait"] for r in rs) if rs else 0,
            # Worker wall/thread CPU: CPU far below wall time indicates GIL or I/O waits.
            "mean_thread_run_s": statistics.mean(r["run"] for r in rs) if rs else 0,
            "mean_thread_cpu_s": statistics.mean(r["cpu"] for r in rs) if rs else 0,
        }
    return {
        "duration_s": a.duration,
        "writes": len(writes),
        "write_p95_s": pct(writes, 95),
        "drain_s": drain_s,
        "drain_capped": bool(leftover),
        "leftover": leftover,
        "manifest_lock_nb_failures": probe.lock_fail,
        # After writer registration all manifest entries need fingerprints, or warm checks fall back to video-content reads.
        "video_facts_after": h.video_fact_coverage(),
        "thread_queue_max": max((s["qsize"] for s in samples), default=0),
        "loop_lag_max_s": max((s["loop_lag"] for s in samples), default=0),
        "cpu_cores_mean": statistics.mean(s["cpu_cores"] for s in samples) if samples else 0,
        "cpu_cores_max": max((s["cpu_cores"] for s in samples), default=0),
        "stack_samples": stacks.summary() if stacks is not None else None,
        "endpoints": endpoints,
        "probes": {
            k: {"n": len(v), "p95": pct(v, 95), "max": max(v), "errors": probe_errors[k]} for k, v in probes.items()
        },
        "samples": samples,
    }


def cmd_peak(a: argparse.Namespace) -> dict[str, Any]:
    _ensure(a)
    # Avoid asyncio.run: shutdown waits for abandoned default-pool computations (up to 300 seconds), delaying result writes.
    result = asyncio.new_event_loop().run_until_complete(_peak(a))
    print(
        f"\n=== peak {a.duration:.0f}s: writes={result['writes']} drain_after_stop={result['drain_s']:.1f}s"
        f"{' (capped, leftover=' + str(result['leftover']) + ')' if result['drain_capped'] else ''} "
        f"thread_queue_max={result['thread_queue_max']} manifest_lock_nb_failures={result['manifest_lock_nb_failures']} "
        f"video_facts_after={result['video_facts_after']} "
        f"cpu_cores mean={result['cpu_cores_mean']:.2f} max={result['cpu_cores_max']:.2f}"
    )
    for name, e in result["endpoints"].items():
        print(
            f"  {name:10s} n={e['n']:4d} p50={e['p50']:7.2f}s p95={e['p95']:7.2f}s max={e['max']:7.2f}s "
            f"delivered_p95={e['delivered_p95']:7.2f}s abandoned={e['abandoned']} unfinished={e['unfinished']} errors={e['errors']} "
            f"inflight_max={e['inflight_max']} computing_max={e['computing_max']} "
            f"hops={e['mean_thread_hops']:.0f} qwait={e['mean_thread_qwait_s']:.2f}s lock_wait={e['mean_lock_wait_s']:.2f}s "
            f"thread_run={e['mean_thread_run_s']:.2f}s thread_cpu={e['mean_thread_cpu_s']:.2f}s"
        )
    for name, p in result["probes"].items():
        print(
            f"  {name:12s} n={p['n']:4d} p95={p['p95'] * 1000:7.1f}ms max={p['max'] * 1000:7.1f}ms errors={p['errors']}"
        )
    if result["stack_samples"]:
        st = result["stack_samples"]
        print(f"  stack samples: busy threads per tick {st['busy_threads_hist']}")
        for label, share in st["self"][:15]:
            print(f"    self {share:6.1%} {label}")
    (_out_dir() / f"peak-{a.tag}.json").write_text(json.dumps(result, indent=1))
    sys.stdout.flush()
    # Abandoned requests may still run in threads after truncated drain; normal exit waits, so terminate the process directly.
    os._exit(0)


# ---------------------------------------------------------------- Summary
def summarize(idle: dict[str, Any], peak: dict[str, Any]) -> str:
    def s(x: float | None) -> str:
        return "-" if x is None or x != x else f"{x:.2f}s"

    lines = [
        "| 接口 | 重启后首开 | 空闲 p95 | 高峰 p95 | 高峰/空闲 | 同时在算最大份数 | 高峰请求数（放弃/未完成） |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in ENDPOINTS:
        i, p = idle.get(name), peak["endpoints"].get(name)
        if i is None and p is None:
            continue
        ip95 = i["p95"] if i else None
        pp95 = p["p95"] if p else None
        ratio = f"{pp95 / ip95:.1f}x" if ip95 and pp95 and pp95 == pp95 else "-"  # nan != nan
        # With unfinished requests, p95 is only a lower bound.
        at_least = "≥" if p and p["unfinished"] else ""
        lines.append(
            f"| {name} | {s(i and i['first_open_s'])} | {s(ip95)} | {at_least}{s(pp95)} | {at_least}{ratio} | "
            f"{p['computing_max'] if p else '-'} | {f'{p["n"]} ({p["abandoned"]}/{p["unfinished"]})' if p else '-'} |"
        )
    lines.append("")
    drain = f"{peak['drain_s']:.1f}s" + (
        f"（达到上限，仍有 {peak['leftover']} 未完成）" if peak["drain_capped"] else ""
    )
    lines.append(f"- 停止写入后的排空时间：{drain}")
    for name, p in peak["probes"].items():
        lines.append(f"- 轻量请求 {name} 高峰 p95：{p['p95'] * 1000:.0f}ms（max {p['max'] * 1000:.0f}ms）")
    if "project" in idle:
        lines.append(
            f"- 热态打开项目详情读取媒体内容（次数与字节）：{idle['project']['warm_media']}；"
            f"重启后首开：{idle['project']['first_open_media']}"
        )
    migration = h.data_root() / "migration.json"
    if migration.exists():
        m = json.loads(migration.read_text())
        lines.append(
            f"- 启动迁移（v17→v18，构建时测）：{m['startup_migration_s']:.1f}s；"
            f"迁移后带内容指纹的视频条目 {m['with_facts']}/{m['videos']}"
        )
    lines.append(
        f"- 高峰期清单锁非阻塞加锁失败（重试）次数：{peak['manifest_lock_nb_failures']}；线程池队列最大长度：{peak['thread_queue_max']}"
    )
    return "\n".join(lines)


def cmd_all(a: argparse.Namespace) -> None:
    t = time.perf_counter()
    _ensure(a)
    passthrough = [
        "--data-root", str(h.data_root()), "--episodes", str(a.episodes), "--units", str(a.units),
        "--assets", str(a.assets), "--video-kb", str(a.video_kb), "--sheet-kb", str(a.sheet_kb),
        "--video-ratio", str(a.video_ratio), "--ep", str(a.ep), "--tag", a.tag,
    ]  # fmt: skip
    if a.only:
        passthrough += ["--only", *a.only]
    peak_args = [
        "--duration", str(a.duration), "--drain", str(a.drain), "--interval", *map(str, a.interval),
        "--clients", str(a.clients), "--sample-hz", str(a.sample_hz),
    ]  # fmt: skip
    for sub, extra in (
        ("idle", ["--reps", str(a.reps)]),
        ("peak", peak_args),
    ):
        subprocess.run(
            [sys.executable, "-m", "scripts.perf.bench", sub, *passthrough, *extra], cwd=h.REPO_ROOT, check=True
        )
    idle = json.loads((_out_dir() / f"idle-{a.tag}.json").read_text())
    peak = json.loads((_out_dir() / f"peak-{a.tag}.json").read_text())
    table = summarize(idle, peak)
    (_out_dir() / f"summary-{a.tag}.md").write_text(table + "\n")
    print(f"\n=== summary ({a.episodes} episodes x {a.units} units, total {time.perf_counter() - t:.0f}s) ===\n{table}")


# ---------------------------------------------------------------- CLI
def _sample_hz(value: str) -> float:
    # Negative or inf intervals become nonpositive, spinning the sampler and stealing measured-process CPU.
    hz = float(value)
    if hz != 0 and not (math.isfinite(hz) and hz > 0):
        raise argparse.ArgumentTypeError("采样频率须为 0 或有限正数")
    return hz


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m scripts.perf.bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    common.add_argument("--episodes", type=int, default=90)
    common.add_argument("--units", type=int, default=28)
    common.add_argument("--assets", type=int, default=30)
    common.add_argument("--video-kb", type=int, default=128)
    common.add_argument("--sheet-kb", type=int, default=512)
    common.add_argument("--video-ratio", type=float, default=0.9)
    common.add_argument("--rebuild", action="store_true")
    common.add_argument("--ep", type=int, default=45, help="单元列表等按集请求使用的集号")
    common.add_argument("--only", nargs="+", choices=ENDPOINTS)
    common.add_argument("--tag", default="default", help="输出文件名后缀")
    idle_opts = argparse.ArgumentParser(add_help=False)
    idle_opts.add_argument("--reps", type=int, default=5)
    peak_opts = argparse.ArgumentParser(add_help=False)
    peak_opts.add_argument("--duration", type=float, default=240)
    peak_opts.add_argument("--interval", type=float, nargs=2, default=[2.0, 5.0], help="两次视频完成的间隔范围（秒）")
    peak_opts.add_argument("--drain", type=float, default=300, help="排空等待上限（秒）")
    peak_opts.add_argument("--clients", type=int, default=1)
    peak_opts.add_argument(
        "--sample-hz",
        type=_sample_hz,
        default=0,
        help="高峰期栈采样频率，0 为关闭；折叠栈写入 out/peak-<tag>.stacks.txt",
    )
    sub.add_parser("build", parents=[common])
    sub.add_parser("idle", parents=[common, idle_opts])
    prof = sub.add_parser("profile", parents=[common])
    prof.add_argument("--top", type=int, default=30)
    sub.add_parser("peak", parents=[common, peak_opts])
    sub.add_parser("all", parents=[common, idle_opts, peak_opts])
    first = sub.add_parser("_first-open")
    first.add_argument("name", choices=ENDPOINTS)
    first.add_argument("--ep", type=int, required=True)
    first.add_argument("--data-root", type=Path, required=True)
    a = ap.parse_args()
    h.configure(a.data_root.resolve())
    if a.cmd == "_first-open":
        print("JSON" + json.dumps(asyncio.run(_first_open(a.name, a.ep)), default=str), flush=True)
        os._exit(0)
    {"build": _ensure, "idle": cmd_idle, "profile": cmd_profile, "peak": cmd_peak, "all": cmd_all}[a.cmd](a)


if __name__ == "__main__":
    main()
