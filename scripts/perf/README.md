# Large-project performance benchmarks

Measure aggregate-endpoint latency on a large synthetic reference-to-video project: idle, first open after restart, and peak generation load. Run manually, outside CI gates. Projects contain synthetic data only.

## Run everything with one command

From repository root:

```bash
uv run python -m scripts.perf.bench all
```

Runs three steps in order:

1. Build 90 episodes × 28 units, return the project to schema 17, then run startup-style migration. v17→v18 backfills content fingerprints for existing videos and records migration time. Reuse the project when parameters match the previous build.
2. Measure idle performance.
3. Simulate peak load.

Print a summary table and write `<数据目录>/out/summary-<tag>.md`. On an 8-core Apple M3, a full run takes about 11 minutes: roughly 40 seconds building, 1 minute idle measurements, a fixed 4-minute peak simulation, plus draining. The pre-change baseline reaches the 5-minute drain limit.

Default data directory is `$TMPDIR/arcreel-perf-bench`, overridden by `--data-root`. Rebuilding clears its `projects`, so only empty or benchmark-created directories are accepted. Concurrent worktrees must use separate data directories. The benchmark drives an in-process ASGI application without listening on a port.

## Run one endpoint or step

| Command | Effect |
|---|---|
| `bench build` | Build only; `--rebuild` forces rebuilding |
| `bench idle --only cost` | Idle only: one first-after-restart request, one after clearing caches, then `--reps` consecutive requests (default 5) |
| `bench peak --only cost` | Refetch only the selected endpoint during peak load; probes run normally |
| `bench profile --only project` | cProfile one cache-cleared request and one warm request; write `.prof` to `out/` |
| `bench all --only project cost` | Full flow, restricted endpoints |

Endpoint names: `project` (project details), `cost` (cost estimation), `wf_status` (production status), `wf_plan` (workflow planning), `units` (reference-unit list; episode from `--ep`, default episode 45).

For before/after comparisons, run each checkout with `--tag before` / `--tag after` to distinguish outputs. Peak simulation appends video versions, so use `--rebuild` for both runs to start from identical projects.

## Common parameters

| Parameter | Default | Meaning |
|---|---|---|
| `--episodes` / `--units` / `--assets` | 90 / 28 / 30 | Episodes, units per episode, asset design images |
| `--video-kb` / `--sheet-kb` | 128 / 512 | Size of each video and design image (KB) |
| `--video-ratio` | 0.9 | Proportion of completed units; each video also has a version snapshot |
| `--duration` | 240 | Seconds of writes during peak load |
| `--interval` | 2 5 | Range of seconds between video completions |
| `--drain` | 300 | Maximum seconds to drain after writing stops |

## Outputs and acceptance criteria

| Summary item | Meaning |
|---|---|
| First open after restart | First request per endpoint in a fresh process, excluding module imports; separately records first open after service restart |
| Idle p95 | p95 of `--reps` consecutive warm requests |
| Peak p95 | Server latency p95 across all peak requests, including abandoned frontend requests. `peak-<tag>.json` also has `delivered_p95`, excluding abandoned requests |
| Peak/idle | Hardware-independent criterion: no greater than 2× |
| Maximum simultaneous computations | Project details and cost estimates count concurrent compute-function executions (see `harness.COMPUTE_MARKERS`), independent of coalescing; other endpoints count in-flight requests |
| Drain time | Time for all in-flight requests to finish after writes stop; identify unfinished requests at the `--drain` limit |
| Lightweight-request p95 | `media_range`: video `Range: bytes=0-65535`; `auth_verify`: authentication with a real JWT. Every 0.2 seconds |
| Startup migration | Build-time v17→v18 migration latency (process caches cleared, OS page cache warm), and resulting fingerprinted-video count. Reused projects retain the previous build record |
| Warm media-content reads | Video/audio content-read count and bytes while opening warm project details. One-byte readability probes excluded. Acceptance requires 0 |

`idle-<tag>.json` and `peak-<tag>.json` contain all raw numbers, including per-request thread hops, pool queueing, manifest-lock waits, and samples every 0.5 seconds.

## Peak-simulation behavior

- **Writer:** complete one video each `--interval`, following production commits: stage, register the artifact manifest during version commit, then write the artifact back to the script. The benchmark never edits the manifest directly. Registration records video fingerprints; `peak-<tag>.json` `video_facts_after` counts fingerprinted entries at peak end and should equal total video entries.
- **Client:** follow current frontend refetches. Each write first emits an entity-change batch: refetch project details, production status, workflow plan, and episode units; debounce cost estimates 0.5 seconds. Then the task-terminal batch refetches units, production status, and workflow plan, without project details. New same-endpoint requests replace old ones; abandoned requests still finish computing server-side.
- Warm each endpoint once before starting; exclude warmup requests from statistics.

## Maintenance

The benchmark calls production functions directly. Update these when functions move or change names:

- `harness.COMPUTE_MARKERS`: missing functions fail immediately at benchmark startup.
- `harness.commit_video`: writer commit path.
- `harness.build`: project builder.
- `harness.downgrade_to_v17`: update when project schema or content-fingerprint field names change.
