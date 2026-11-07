# Unified usage rows: linking generation tasks and provider calls

> Date:2026-09-02. [#2287](https://github.com/ArcReel/ArcReel/issues/2287), map [#2286](https://github.com/ArcReel/ArcReel/issues/2286).
> Goal: one call per usage row, combining in-progress `tasks` and completed `api_calls` into timelines. Answer five ticket questions with code locations at main `1b6a0c2be`, then feasible composition/gaps.
> Scope: `lib/db/models/{api_call,task}.py`, `lib/db/repositories/{usage_repo,task_repo,base}.py`, `lib/billing/ledger.py`, `lib/generation/media_generator.py`, `lib/backends/text_generator.py`, `lib/generation/generation_worker.py`, `server/agent_runtime/session_manager.py`, `lib/custom_provider/endpoint_test/trial_run.py`, `server/routers/{usage,tasks}.py`, `server/auth.py`; frontend `GlobalHeader` / `UsageDrawer` / `TaskHud` / `StudioLayout` / `useTaskRefresh` / `usage-store` / `tasks-store`; ADR0021/0053. Static reading only, no code executed; unverified findings are inferences.

## Findings overview

| Question | Finding |
|---|---|
| 1 Backreferences | `api_calls` has no `task_id` / `resource_id` / `provider_job_id`; only exact link **`tasks.payload_json.api_call_id` → `api_calls.id`**, video-only. `segment_id` / `output_path` weak. At most one call per task, download retries/resume reuse rows; many calls lack tasks |
| 2 Origins | Text has only `project_name`, possibly empty, `provider`, `model`, first500 `prompt` characters; no text purpose/session/endpoint IDs |
| 3 Visibility | Both have NOT NULL `user_id`, always `"default"`; `_scope_query` no-op, `/usage/*`/`/tasks*` no user args; auth maps tokens to `DEFAULT_USER_ID`. ADR0021 subclass isolation, same semantics |
| 4 Demo fallback | `GlobalHeader.tsx:76-77` sets `usageProjectName` null, giving **global** drawer/header costs; `StudioLayout.tsx:70-72` + `useTaskRefresh.ts:44-54` disable/clear tasks. Hide cost/drawer/radar buttons and close existing panels on demo changes |
| 5 Read APIs | `/usage/calls` default20/max100, `started_at desc`; `/projects/{name}/tasks` default50/max500, `updated_at desc, queued_at desc`; `/tasks/stats` unpaged counts. Offset paging, different timestamps, no usage tiebreak |

## 1. Can `api_calls` reference `tasks`?

### Current state

**Available `api_calls` linkage columns** (`lib/db/models/api_call.py:14-50`): `project_name` L18, `call_type` L19, `model` L20, nullable Text `output_path` L28, indexed nullable String(20) `segment_id` L29, `started_at` / `finished_at` L30–31, `provider` L36, JSON `last_provider_response` L44, `user_id` (`UserOwnedMixin`, `lib/db/base.py:33-42`). **No** `task_id`, `resource_id`, `provider_job_id`, `resource_type`, `task_type`.

**`tasks`** (`lib/db/models/task.py:13-69`): NOT NULL `resource_id` L21, image_edit-only `resource_type` L24, `payload_json` L26, `provider_id` / `provider_job_id` L35–36, `queued_at` / `started_at` / `finished_at` / `updated_at` L47–50. **No** `api_call_id` column.

Per-field linkage:

| Clue | Match quality | Evidence |
|---|---|---|
| `tasks.payload_json["api_call_id"]` → `api_calls.id` | **Exact, video/reference_video only** | Sole writer inside video ledger scope: `lib/generation/media_generator.py:1007-1014`, `task_id is not None` calls `persist_api_call_id`; `lib/db/repositories/task_repo.py:1049-1056` `_merge_payload_field`. Image `media_generator.py:613-624`/audio `media_generator.py:729-737` do not; their `task_id` only stages paths, `media_generator.py:48-71` |
| `tasks.resource_id` ↔ `api_calls.segment_id` | **Weak, allowlisted resources** | `segment_id_for`, `media_generator.py:141-154`: image only `storyboards/videos/grids`, video only `storyboards/videos/reference_videos`, write `resource_id` into `segment_id`; audio always. Asset sheets have NULL `segment_id` (ADR0053 `docs/adr/0053-cost-attribution-ledger-key-primary.md:7`). ADR0053:14 IDs neither globally unique nor episode-consistent; regenerations accumulate calls per `resource_id` |
| `tasks.provider_job_id` ↔ `api_calls.?` | **None** | `api_calls` has no such column; tasks write `provider_job_id` through `persist_provider_job_id`, `task_repo.py:966-992`. Arbitrary `last_provider_response` JSON, `usage_repo.py:42-56`,64KiB cap, may contain IDs without guaranteed structure, unverified |
| `api_calls.output_path` ↔ task outputs | **Weak, successful rows only** | Success-only `output_path`, `lib/billing/ledger.py:146` → `finish_call(output_path=...)` in `usage_repo.py:367-411`; failure/pending NULL. Tasks use `result_json`, `task_repo.py:551` `mark_succeeded(result)`, structure unverified, no separate column |
| `project_name` + `call_type` + time | **Heuristic** | Shared `project_name`; `tasks.media_type` (`task.py:20`) approximately `api_calls.call_type`; `api_calls.started_at` within `tasks.started_at..finished_at`. Concurrent same-type jobs collide |

**Multiple calls per task?** Every inspected path reuses one `api_calls` row:

- Download retry `task_repo.retry_artifact_download`, `task_repo.py:601-648`, flips original `ApiCall` failed/pending→pending; L623–625 explicitly says same call, no new billing row.
- Restart/crash resume `generate_video_resume_async` opens no new ledger scope (`media_generator.py:1135-1138`); `ledger.resume_success` / `resume_failed` update precise `call_id` (`lib/billing/ledger.py:152-162`, `media_generator.py:1229-1239`). Predispatch terminal rejection also updates originals (`lib/generation/generation_worker.py:1337-1355`).
- `api_calls.retry_count` (`api_call.py:33`) has no writer, read only in `_row_to_dict`, `usage_repo.py:144`, always0.
- Unverified: `grid` / `reference_video` may sequentially issue multiple backend calls per task, such as multiunit references. If so, only final video IDs remain in `payload.api_call_id`, overwritten by `_merge_payload_field`.

**Calls without tasks?** Many:

- All text: `TextGenerator.generate`, `lib/backends/text_generator.py:51-66`, accepts no tasks. Sites: scripts `lib/script/script_generator.py:262,1328`, planning `lib/episode/episode_planner.py:412,659`, overview `lib/project/project_manager.py:3612-3625`, style `server/routers/files.py:1023-1024`, `server/text_generation.py:850,1512,1659`.
- Assistant backfills: `session_manager._record_assistant_usage`→`ledger.backfill`, `server/agent_runtime/session_manager.py:1184-1210`, terminal rows directly.
- Endpoint trials `trial_run.py:375-386`, `project_name=""`, `call_type="video"`, no task.
- Any ledger scope with `task_id=None` lacks task clues; defaults `task_id: str | None = None` at `media_generator.py:487,532`.
- Reverse also holds: crashed image tasks become `restart_lost_image`, `generation_worker.py:1163-1173`, but pending `ApiCall` lacks `api_call_id` for updates, remaining pending forever. Inference: scopes settle only `CancelledError` / `Exception`, `ledger.py:133-150`; repository has no sweeper, `stale|sweep|orphan` searches of `usage_repo.py` / `ledger.py` empty.

### Feasible composition

- **Primary link**: `tasks.payload_json.api_call_id`, only exact existing FK, joins videos. API returns unchanged `payload` through `_task_to_dict`, which includes `payload`, `task_repo.py:96-125`; `_localize_task` in `server/routers/tasks.py:74-112` removes only `execution_checkpoint_json`. Frontend `TaskItem.payload`, `frontend/src/types/task.ts:32`, sees it.
- **Weak fallback**: nonvideo `(project_name, user_id, media_type≈call_type, segment_id==resource_id, started_at ∈ [task.started_at, task.finished_at])`. For NULL-`segment_id` sheets, match `output_path` prefixes/`resource_id` filenames; reuse `usage_repo._classify_asset_output_path`, `usage_repo.py:108-124`.
- **Rows**: terminal `api_calls` authoritative; active queued/running/cancelling `tasks` authoritative until linked calls settle, then merge by `api_call_id` without double counting.

### Gaps

1. `api_calls` lacks nullable/indexed `task_id`; `ledger.record` lacks `task_id`; image/audio scopes never persist `api_call_id`.
2. Unwritten `retry_count` cannot express execution attempts; retries only refresh `started_at`, `task_repo.py:634-639`.
3. No orphan-image pending reclamation.

## 2. Origin fields for taskless text calls

### Current state

Only `ledger.record` (`lib/billing/ledger.py:95-111` arguments) and `ledger.backfill` (`ledger.py:169-207`) write. Persisted text fields:

| Field | Value | Evidence |
|---|---|---|
| `project_name` | Caller-supplied, **possibly empty** | `text_generator.py:58` `project_name or ""`; `trial_run.py:376` fixed `""`; assistant `managed.project_name`, `session_manager.py:1197` |
| `call_type` | `"text"` | `text_generator.py:59`, `session_manager.py:1198` |
| `provider` / `model` | Resolved provider_id/backend model | `text_generator.py:60-61`; assistant fixed `PROVIDER_ANTHROPIC`, `session_manager.py:1201` |
| `prompt` | First500characters | `text_generator.py:62`; last user prompt at `session_manager.py:1200` |
| `status` / `error_message` / times / tokens / costs | Settlement | `usage_repo.py:367-411` |
| `segment_id` / `output_path` | NULL | Text does not pass them |

`TextTaskType` SCRIPT/OVERVIEW/STYLE_ANALYSIS (`text_generator.py:44-49` factories) is known at construction but **not passed to ledger**. `session_id` is available through `managed.session_id`, `session_manager.py:1179`, but unpersisted. Trials have no endpoint identity.

Frontend `UsageCall`, `frontend/src/stores/usage-store.ts:21-40`, declares only `project_name / call_type / model / status / cost / provider / output_path / resolution / duration / error / 时间 / token`, not `prompt`/`segment_id`. Drawer `frontend/src/components/layout/UsageDrawer.tsx:235,290,321` shows output filename/model/tokens/time, not `project_name`/`prompt`.

### Feasible composition

- Available origin inference: `provider == anthropic && call_type == text` assistant; `project_name == ""` trial/taskless text; `prompt` prefixes for tooltips. Cannot distinguish scripts/planning.
- Encoding `TextTaskType` in existing `ledger.record` fields such as `prompt` tags is a short-term hack, not recommended.

### Gaps

4. Add nullable `session_id` and purpose/origin (`purpose` or `origin`: `script_generation` / `episode_planning` / `overview` / `style_analysis` / `assistant_session` / `endpoint_trial` / `task:<task_type>`) to `api_calls`; extend `ledger.record` / `backfill`, five text sites plus `session_manager` / `trial_run`.
5. `UsageCall` lacks existing `/usage/calls` response `segment_id` / `prompt`, `usage_repo.py:126-158`; extend frontend types.

## 3. User visibility for `user_id` and tasks

### Current state

- Both use `UserOwnedMixin`, indexed NOT NULL `user_id`, `server_default="default"`; `lib/db/base.py:33-42`, `api_call.py:14`, `task.py:13`.
- `BaseRepository._scope_query` is open-source no-op (`lib/db/repositories/base.py:17-19`). `UsageRepository.get_calls` / `get_stats*`, `usage_repo.py:665,672`, and `TaskRepository.get / list_tasks / get_stats`, `task_repo.py:1195,1224,1235,1252`, all apply it.
- `/usage/calls`, `server/routers/usage.py:56-81`, and `/tasks*`, `server/routers/tasks.py:114-165`, have no `CurrentUser` or repository `user_id` parameters.
- Auth returns `DEFAULT_USER_ID` for any tokens: `_anonymous_user`, `server/auth.py:69-73`, and `_payload_to_user`, `server/auth.py:426-432`.
- Media writes set `user_id` from `MediaGenerator._user_id`, `media_generator.py:173,622,736,1001`; text omits `user_id`, using `ledger.record` default `DEFAULT_USER_ID`, `ledger.py:107` / `text_generator.py:57-63`; assistants `getattr(self, "_user_id", DEFAULT_USER_ID)`, `session_manager.py:1202`. Enqueue callers supply `user_id`, `lib/generation/generation_queue.py:398` / `server/tool_runtime.py:254-265`; unique dedupe includes `user_id`, `task.py:57-68`.
- ADR0021 `docs/adr/0021-multi-user-preembed-scope-query.md:7-12`: commercial subclasses override `_scope_query`; raw-SQL `claim_next` is accepted exception.

### Feasible composition

Let `_scope_query` own visibility: same seam/`user_id` semantics, merged queries through the same `BaseRepository` subclass, without extra user filters.

### Gaps

6. Text lacks `user_id`, assigning every commercial call to `"default"` unlike caller-owned task writes (`text_generator.py:57-63` versus `generation_queue.py:398`), making merged text disappear/misattribute in multiuser contexts.

## 4. Demo projectName=null fallback and hidden entry points

### Current state

- **Usage fallback**: `frontend/src/components/layout/GlobalHeader.tsx:73-77` sets `usageProjectName = null` when `demoMode || isDemoProject(currentProjectName)` (no backend demo records; project requests404, so global). Header stats L88–99 `API.getUsageStats(usageProjectName ? {...} : {})` and `UsageDrawer` L339–344 `projectName={usageProjectName}` receive it. `projectName ?? undefined`, `UsageDrawer.tsx:65,80-83`, omits `project_name`, returning **global** calls (`usage.py:59`, `usage_repo.py:431-432`).
- **Task disablement, not fallback**: `frontend/src/components/layout/StudioLayout.tsx:66-72` computes `isEffectivelyDemo`, `useTaskRefresh(sseProjectName, !isEffectivelyDemo)`; `frontend/src/hooks/useTaskRefresh.ts:44-54` clears `refreshScope / tasks / stats / connected` when `enabled=false`. Comments define `projectName=null` as no project filtering, not disablement (`useTaskRefresh.ts:28-30`, `tasks-store.ts:35-38`).
- **Demo detection**: `frontend/src/onboarding/demo-project.ts:34,45-47`, case-insensitive `DEMO_PROJECT_NAME = "onboarding_demo"`; `useDemoWorkbench`, `GlobalHeader.tsx:9,76`.
- **Entries**: costs `GlobalHeader.tsx:287-296`, radar `GlobalHeader.tsx:348-380`, `<TaskHud>`L380, **remain clickable in demos**. Only export disables by `demoMode`, L398/408–409, with close-on-demo effect L79–85.

### Feasible composition

Choose one fixed demo policy: (a) tasks' hidden entries/cleared stores, or (b) usage's global fallback. Current contradictions yield historical calls without tasks in demos. Recommend(a).

### Gaps/change sites

7. Cost badge `GlobalHeader.tsx:287-296`, drawer `339-344`, radar `348-380` + `TaskHud`: hide/disable for `demoMode || isDemoProject(currentProjectName)`; copy L79–85's effect to close drawers/HUD on demo entry.
8. Remove `GlobalHeader.tsx:76-77` null fallback after hiding entries, avoiding residual global header requests.
9. `TaskHud.tsx:609` reads `useTasksStore().tasks` without demo awareness; retained rendering needs internal empty states.

## 5. Pagination/ordering across three read APIs

| API | Parameters/limits | Ordering | Filters | Other |
|---|---|---|---|---|
| `GET /usage/calls`, `server/routers/usage.py:56-81` | `page`≥1 default1; `page_size` **20**,1..**100**,L64–65 | `started_at DESC`, **no tiebreak**, `usage_repo.py:671` | `call_id / project_name / call_type / status / start_date / end_date`; UTC whole dates filter `started_at`, `usage_repo.py:439-444`; `_build_filters` supports `provider`, not forwarded by `get_calls`, `usage_repo.py:643-660` | Offset `{items,total,page,page_size}`, `usage_repo.py:670-682`; no repository clamp, route `le=100` |
| `GET /projects/{name}/tasks`, `server/routers/tasks.py:144-165` | `page`≥1 default1; `page_size` **50**,1..**500**,L151–152; repository clamp1..500, `task_repo.py:1210-1211` | `updated_at DESC, queued_at DESC`, `task_repo.py:1231` | `status / task_type / source`; path `project_name` | Offset/same envelope, `task_repo.py:1239-1244`; `_localize_task` localizes errors (`tasks.py:74-112`), unlike usage |
| `GET /tasks/stats`, `server/routers/tasks.py:114-119` | Unpaged | None | Optional `project_name` | `status` GROUP BY with zero-fill, `task_repo.py:1246-1274`; `{stats:{queued,running,cancelling,succeeded,failed,cancelled,total}}` |

Frontend actually uses global `GET /tasks`, `server/routers/tasks.py:121-141`, same semantics, `TASKS_PAGE_SIZE = 200`, `frontend/src/stores/tasks-store.ts:8,250-252`, not `/projects/{name}/tasks`. Usage drawer `page_size`20, `usage-store.ts:67`. `/usage/stats`, `usage.py:19-53`, counts provider/type/currency, unlike task-state `/tasks/stats`.

### Feasible composition

- Times: calls `started_at`, `api_call.py:30`, NOT NULL; tasks `queued_at`, `task.py:47`, NOT NULL. State-changing `tasks.updated_at` is unsuitable as timeline key.
- UNION terminal `api_calls` ∪ active `tasks`, excluding tasks linked by `payload.api_call_id`, keyset `(ts DESC, id DESC)` in one-page queries. Alternatively merge two frontend pages, but different sizes/totals20/100 versus50/500 misalign pagination.

### Gaps

10. No common tiebreak: multiple calls in one second can duplicate/drop offset rows. Unified timelines need timestamp+ID keysets.
11. `project_name` semantics agree, but `status` sets differ: usage `pending/success/failed`, tasks `queued/running/cancelling/succeeded/failed/cancelled`; map explicitly.
12. Task routes localize errors, usage returns raw `error_message`, `usage_repo.py:138`; merging otherwise mixes meanings in one column.

## Overall recommendation

**Recommended composition**: add server timeline reads, e.g. `GET /usage/timeline`, through a `UsageRepository` method:

1. Use every `api_calls` row, including pending, as completed/in-progress call rows;
2. Supplement active `tasks` with `status IN (queued, running, cancelling)` **without** matching calls, video `payload.api_call_id`, other tasks section1 weak heuristics;
3. Keyset `(ts, id)` with `ts` from `api_calls.started_at` / `tasks.queued_at`;
4. Same `_scope_query` isolation;
5. Carry `origin`, nullable `task_id`, mapped common `status`.

**Prerequisite gaps**, prioritized:

| # | Gap | Suggested change |
|---|---|---|
| 1 | No `api_calls` `task_id`, image/audio `api_call_id` persistence | Add nullable/indexed `api_calls.task_id`; `ledger.record` `task_id` through three media scopes; migrate reverse links from `tasks.payload_json.api_call_id` |
| 4 | No text/trial/assistant origins | `api_calls.purpose` enum string + nullable `session_id`; known `TextTaskType` in `TextGenerator.create` passes into `generate` accounting |
| 6 | Text `user_id` always default | Pass `user_id` through `TextGenerator`→`ledger.record` |
| 10/11/12 | Tiebreak/status/localization | New API handles uniformly; leave existing three unchanged |
| 7/8/9 | Demo entries | Frontend section4 checklist |
| 2/3 | Unwritten `retry_count`, orphan-image pending | Defer; provisional unknown display after pending>N hours, policy undecided/product decision |
