import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { TaskItem, TaskStats, TaskStatus } from "@/types";

/** Maximum tasks fetched in one refresh. */
const TASKS_PAGE_SIZE = 200;

/**
 * All resource kinds used for occupancy checks, excluding image_edit, which maps to one of them through resource_type.
 *
 * Occupancy writers/readers share this source: {@link TasksState.beginOptimisticActive} marks slots and
 * {@link selectActiveResourceIds} filters them. Both kind spellings must match the same slot.
 * A union type moves silent nonoccupancy caused by typos into compile-time failures.
 */
export type ResourceKind =
  | "character"
  | "character_derivative"
  | "scene"
  | "prop"
  | "product"
  | "storyboard"
  | "video"
  | "tts"
  | "reference_video"
  | "grid"
  | "text_episode_script"
  | "text_episode_plan"
  | "text_script_plan"
  | "text_draft_repair";

/** Resource kinds supporting instruction editing; `image_edit` tasks occupy the corresponding resource slot. */
export type ImageEditResourceKind = Extract<
  ResourceKind,
  "character" | "character_derivative" | "scene" | "prop" | "product" | "storyboard"
>;

/**
 * Refresh scope: `projectName === null` means no project filter, fetching global tasks; an entire
 * `null` scope means disabled, so {@link TasksState.refreshTasks} sends no request. Only
 * `useTaskRefresh` registers scope. Other entries, such as terminal project-event SSE handlers, call refreshTasks
 * without choosing projects themselves, preventing project-filtered results from overwriting the global task list.
 */
export interface TasksRefreshScope {
  projectName: string | null;
}

/**
 * Optimistic occupancy handle for one enqueue submission. Acquire through the action layer **before sending** and settle in exactly one way afterward:
 *
 * - `settle(taskIds)`: backend tasks were created or deduplicated; wait for those task_id rows to appear.
 *   An empty array means no task rows and equals `rollback()`, otherwise marks would wait forever.
 * - `rollback()`: submission failed; remove the mark.
 *
 * Unsettled marks remain in flight and cannot be cleared by polling writes. This covers the request-roundtrip window
 * for which callers previously needed separate in-flight refs.
 */
export interface OptimisticHandle {
  settle: (taskIds: string[]) => void;
  rollback: () => void;
}

interface TasksState {
  tasks: TaskItem[];
  stats: TaskStats;
  connected: boolean;
  refreshScope: TasksRefreshScope | null;
  /** Optimistic occupancy marks; see the optimistic-occupancy section of {@link selectActiveResourceIds} below. */
  optimisticActive: Set<string>;
  /** ScriptFile-level optimistic occupancy marks; see the optimistic-occupancy section of {@link selectHasActiveTaskForScriptFile} below. */
  optimisticActiveScriptFile: Set<string>;

  // Actions
  setTasks: (tasks: TaskItem[] | ((prev: TaskItem[]) => TaskItem[])) => void;
  setStats: (stats: TaskStats) => void;
  setConnected: (connected: boolean) => void;
  setRefreshScope: (scope: TasksRefreshScope | null) => void;
  /**
   * Fetch the current scope's task list/statistics and update the store.
   *
   * Shared by polling timers and terminal project-event SSE handlers, so centralize in one action with
   * in-flight coalescing: new requests during a refresh schedule another round afterward, with each caller
   * settled by its own round. Otherwise simultaneous entries interleave writes and older late responses overwrite new data.
   */
  refreshTasks: () => Promise<void>;
  beginOptimisticActive: (
    projectName: string,
    resourceKind: ResourceKind,
    resourceId: string,
    pendingTaskType: string,
  ) => OptimisticHandle;
  beginOptimisticActiveForScriptFile: (
    projectName: string,
    taskType: string,
    scriptFile: string,
  ) => OptimisticHandle;
}

export const defaultTaskStats: TaskStats = {
  queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0,
};

// ---------------------------------------------------------------------------
// Optimistic occupancy mark encoding and handoff criteria
//
// Marks are string Set keys, ending with mark sequence and settled task_id list:
//
//   Resource granularity   project \0 resourceKind \0 resourceId \0 pendingTaskType \0 seq \0 taskIds
//   scriptFile project \0 taskType \0 scriptFile \0 seq \0 taskIds
//
// seq lets concurrent submissions for one resource roll back independently; an empty taskIds means in flight: the request was sent,
// but backend task IDs remain unknown, so no polling write may clear the mark. Field counts differ between granularities, but taskIds
// always occupies the final segment, letting {@link markTaskIds} parse handoff consistently without field-addition offsets.
//
// Handoff uses task_id rather than timestamps: marks yield only after **all** task rows from their own submission have appeared in tasks;
// remove observed IDs from keys cumulatively, without requiring one snapshot to contain them all.
// This criterion closes two ordering gaps:
//   1. Terminal-first arrivals no longer leave permanent marks: deduplication returns the existing row's task_id,
//      already or soon present in tasks, so the mark yields; timestamp comparison would never yield if updated_at
//      were not greater than the baseline when the mark began.
//   2. Late old polling snapshots cannot overwrite newer marks: they lack this submission's task_id, so the criterion fails.
// ---------------------------------------------------------------------------

/** Separator between settled task_ids, distinct from the field separator \0 for parsing. */
const TASK_ID_SEP = "\u0001";

let optimisticSeq = 0;

function encodeTaskIds(taskIds: readonly string[]): string {
  return taskIds.join(TASK_ID_SEP);
}

/** Read taskIds from the key's final segment; in-flight marks with an empty final segment return an empty array. */
function markTaskIds(key: string): string[] {
  const encoded = key.split("\0").at(-1);
  return encoded ? encoded.split(TASK_ID_SEP) : [];
}

function optimisticKey(
  projectName: string,
  resourceKind: ResourceKind,
  resourceId: string,
  pendingTaskType: string,
  seq: number,
  taskIds: readonly string[],
): string {
  return `${projectName}\0${resourceKind}\0${resourceId}\0${pendingTaskType}\0${seq}\0${encodeTaskIds(taskIds)}`;
}

function optimisticScriptFileKey(
  projectName: string,
  taskType: string,
  scriptFile: string,
  seq: number,
  taskIds: readonly string[],
): string {
  return `${projectName}\0${taskType}\0${stripScriptsPrefix(scriptFile)}\0${seq}\0${encodeTaskIds(taskIds)}`;
}

/** Replace final-segment taskIds with `taskIds`, leaving other fields unchanged. */
function withTaskIds(key: string, taskIds: readonly string[]): string {
  return key.slice(0, key.lastIndexOf("\0") + 1) + encodeTaskIds(taskIds);
}

/** Task IDs observed as persisted in this snapshot. */
function landedTaskIds(tasks: TaskItem[]): Set<string> {
  return new Set(tasks.map((t) => t.task_id));
}

/**
 * Mark state after this snapshot: return null to yield when all awaited task_ids have appeared,
 * otherwise return the reduced key, unchanged if nothing changed, avoiding unnecessary Set churn.
 *
 * In-flight marks with empty taskIds remain unchanged and never yield: during a roundtrip, nothing proves
 * the resource is idle. When one submission creates multiple task rows, such as one per grid group, wait for **all** rows.
 * Yielding after only one falsely frees the resource while the first is already terminal but others are absent from the snapshot.
 * Accumulate removals across rounds, without requiring one snapshot to contain every task_id: polling fetches only the latest
 * 200 rows, while one enqueue submission has no upper task-count limit.
 */
function keepMark(key: string, landed: ReadonlySet<string>): string | null {
  const taskIds = markTaskIds(key);
  if (taskIds.length === 0) return key; // In-flight mark
  const remaining = taskIds.filter((id) => !landed.has(id));
  if (remaining.length === 0) return null;
  return remaining.length === taskIds.length ? key : withTaskIds(key, remaining);
}

// Trim optimistic marks against current tasks, never adding marks: remove observed task_ids and clear yielded marks.
// Besides opportunistic cleanup on settlement, run after every setTasks polling write: real task rows enter
// via polling, and only then can marks discover their own persisted rows.
function pruneSupersededOptimistic(tasks: TaskItem[], marks: ReadonlySet<string>): Set<string> {
  const landed = landedTaskIds(tasks);
  const next = new Set<string>();
  for (const key of marks) {
    const kept = keepMark(key, landed);
    if (kept !== null) next.add(kept);
  }
  return next;
}

export const useTasksStore = create<TasksState>((set, get) => {
  // Refresh coalescing state, a nonreactive singleton outside store state to avoid subscription rerenders.
  // Structurally matches projects-store.refreshProject, created with the store without leaking across store instances.
  let refreshRunning = false;
  let refreshQueued = false;
  // Queued-round callers each own a resolve callback and receive only the result of the round that actually served them.
  let queuedResolvers: Array<() => void> = [];
  // Scope with an established task baseline; null means its next round initializes the baseline without terminal-transition detection.
  let baselineScopeKey: string | null = null;

  /**
   * Whether this fetch contains a newly successful task that writes back to a reference unit.
   *
   * Reference-to-video canvases use `referenceVideoUnitsRevision` to refetch video/narration paths, primarily incremented by project
   * event SSE. On disconnection, silent stalling or a lost event, polling can recover task status while unit
   * snapshots stay stale until remount. Let reference_video / tts polling success trigger the same
   * invalidation to fill that fallback gap; other modes do not mount this canvas, so an extra revision increment sends no request.
   *
   * Detect success now without success previously; previously unseen also counts as not successful. If enqueue and completion
   * both occur between polls, possible during long idle intervals or immediate provider cache hits, the task first appears
   * as succeeded. Requiring prior observation would miss it. On the next round its old snapshot already says succeeded,
   * so invalidation does not repeat.
   *
   * The scope's first round only establishes a baseline: its old snapshot is empty or belongs to another project, making
   * historical successes meaningless as new completions. Track the baseline with `baselineScopeKey`, resetting on project changes or disablement.
   */
  const hasNewlySucceededReferenceUnitAsset = (
    prev: readonly TaskItem[],
    next: readonly TaskItem[],
  ): boolean => {
    const prevStatus = new Map(prev.map((t) => [t.task_id, t.status]));
    return next.some(
      (t) =>
        (t.task_type === "reference_video" || t.task_type === "tts") &&
        t.status === "succeeded" &&
        prevStatus.get(t.task_id) !== "succeeded",
    );
  };

  // One fetch round; never throw, only set connected false and retain old data on failure.
  const runTasksRefresh = async (): Promise<void> => {
    const scope = get().refreshScope;
    if (!scope) return;
    const { projectName } = scope;

    // Project switching/disabling may change scope before resolution; discard late responses or old-project tasks would overwrite
    // new-owner data, or repopulate a store that should be cleared after disablement.
    const scopeUnchanged = () => {
      const current = get().refreshScope;
      return current !== null && current.projectName === projectName;
    };

    try {
      const [tasksRes, statsRes] = await Promise.all([
        API.listTasks({ projectName: projectName ?? undefined, pageSize: TASKS_PAGE_SIZE }),
        API.getTaskStats(projectName ?? null),
      ]);
      if (!scopeUnchanged()) return;
      const scopeKey = projectName ?? "";
      const unitsStale =
        baselineScopeKey === scopeKey &&
        hasNewlySucceededReferenceUnitAsset(get().tasks, tasksRes.items);
      baselineScopeKey = scopeKey;
      get().setTasks(tasksRes.items);
      get().setStats(statsRes.stats);
      get().setConnected(true);
      // Keep inside try deliberately: invalidation is one zustand set without a throwing path. Moving it outside would break
      // nonthrowing behavior: project-event SSE calls refreshTasks with bare void, so a throw would become an unhandled rejection
      // and coalesced queued callers would never settle.
      if (unitsStale) {
        useAppStore.getState().invalidateReferenceVideoUnits();
      }
    } catch {
      if (!scopeUnchanged()) return;
      get().setConnected(false);
    }
  };

  /**
   * Shared begin/settle/rollback skeleton: granularities differ only in key encoding and the destination Set.
   * `field` selects the Set; `keyOf(taskIds)` encodes settled IDs, with an empty array forming an in-flight key.
   */
  function beginMark(
    field: "optimisticActive" | "optimisticActiveScriptFile",
    keyOf: (taskIds: readonly string[]) => string,
  ): OptimisticHandle {
    const pendingKey = keyOf([]);
    set((s) => {
      // Also remove old yielded marks to keep the Set bounded during a session.
      const next = pruneSupersededOptimistic(s.tasks, s[field]);
      next.add(pendingKey);
      return { [field]: next };
    });

    const drop = (replacement: string | null): void => {
      if (!get()[field].has(pendingKey)) return; // Already rolled back or reset with the store
      set((s) => {
        const next = new Set(s[field]);
        next.delete(pendingKey);
        if (replacement !== null) {
          // Deduplicated existing rows are often already in the store; remove their IDs now rather than waiting for another poll;
          // yield immediately if all IDs have been removed.
          const kept = keepMark(replacement, landedTaskIds(s.tasks));
          if (kept !== null) next.add(kept);
        }
        return { [field]: next };
      });
    };

    return {
      settle: (taskIds) => drop(taskIds.length > 0 ? keyOf(taskIds) : null),
      rollback: () => drop(null),
    };
  }

  return {
    tasks: [],
    stats: defaultTaskStats,
    connected: false,
    refreshScope: null,
    optimisticActive: new Set(),
    optimisticActiveScriptFile: new Set(),

    setRefreshScope: (scope) => {
      // Project changes and disablement clear/replace task snapshots, invalidating the old baseline; rebuild it next round.
      baselineScopeKey = null;
      set({ refreshScope: scope });
    },
    refreshTasks: async () => {
      if (refreshRunning) {
        refreshQueued = true;
        return new Promise<void>((resolve) => {
          queuedResolvers.push(resolve);
        });
      }
      refreshRunning = true;
      try {
        await runTasksRefresh();
        while (refreshQueued) {
          refreshQueued = false;
          const resolvers = queuedResolvers;
          queuedResolvers = [];
          await runTasksRefresh();
          for (const resolve of resolvers) resolve();
        }
      } finally {
        refreshRunning = false;
      }
    },

    setTasks: (tasks) =>
      set((s) => {
        // Function updates evaluate the snapshot at write time; reading getState() then passing an array would overwrite
        // any SSE update arriving between those steps.
        const next = typeof tasks === "function" ? tasks(s.tasks) : tasks;
        return {
          tasks: next,
          optimisticActive: pruneSupersededOptimistic(next, s.optimisticActive),
          optimisticActiveScriptFile: pruneSupersededOptimistic(next, s.optimisticActiveScriptFile),
        };
      }),
    setStats: (stats) => set({ stats }),
    setConnected: (connected) => set({ connected }),
    beginOptimisticActive: (projectName, resourceKind, resourceId, pendingTaskType) => {
      const seq = ++optimisticSeq;
      return beginMark("optimisticActive", (taskIds) =>
        optimisticKey(projectName, resourceKind, resourceId, pendingTaskType, seq, taskIds),
      );
    },
    beginOptimisticActiveForScriptFile: (projectName, taskType, scriptFile) => {
      const seq = ++optimisticSeq;
      return beginMark("optimisticActiveScriptFile", (taskIds) =>
        optimisticScriptFileKey(projectName, taskType, scriptFile, seq, taskIds),
      );
    },
  };
});

// ---------------------------------------------------------------------------
// Derived selectors: single source for two task-queue invariants
//
// Consumers such as canvas loading and video-unit status must follow both contracts:
//   1. Active has separate occupancy/display predicates. Occupancy isOccupyingStatus matches backend
//      dedupe-index ACTIVE_TASK_STATUSES; display isActiveStatus decides whether to show in progress.
//      Both currently contain queued / running only.
//   2. Latest row wins: resources may have several tasks; retries create new task_ids.
//      The server replaces the full tasks list without guaranteed order, so select the greatest updated_at
//      instead of allowing old failed rows to hide retries (selectLatestTaskByResource).
//
// Pure selectors centralize both invariants for direct vitest coverage; hooks use useShallow on
// Set/Map contents to retain references when unchanged and avoid rerenders from new collections each render.
// ---------------------------------------------------------------------------

/** Display predicate: show queued/running tasks as in progress. */
export function isActiveStatus(status: TaskStatus): boolean {
  return status === "queued" || status === "running";
}

/**
 * Whether marks belong to the current refresh scope. Both key formats start with `projectName`,
 * so match prefixes consistently; an unfiltered scope (`projectName === null`) includes all.
 *
 * Filter scope rather than checking `size`: marks are trimmed only when real rows from the same project
 * appear. After switching projects, old marks never get trimmed and would pin fallback polling to its fast interval forever.
 */
function hasOptimisticInScope(
  marks: ReadonlySet<string>,
  scopeProjectName: string | null,
): boolean {
  if (marks.size === 0) return false;
  if (scopeProjectName === null) return true;
  const prefix = `${scopeProjectName}\0`;
  for (const key of marks) {
    if (key.startsWith(prefix)) return true;
  }
  return false;
}

/**
 * Whether fallback polling needs its fast interval. Three cases require it:
 *
 * - **Nonterminal tasks exist**. Prefer stats because it covers the whole scope while tasks includes only the first N rows.
 *   Intermediate queued→running changes emit no events, so only polling sees them. Also inspect tasks because the two responses
 *   come from concurrent requests and different snapshots. Another client/Skill can enqueue during refresh,
 *   leaving stats with zero active tasks while tasks already contains the queued row. Stats alone would mark this round idle
 *   and hide intermediate progress for a full idle interval.
 * - **Optimistic marks exist in the current scope**. After successful enqueue, a gap remains before stats includes the task;
 *   calling that idle would delay its first UI appearance by a whole idle interval.
 * - **The last fetch failed** (`connected === false`). Stale stats could incorrectly mark the scope idle and postpone
 *   recovery a full idle interval; failures should retry promptly.
 */
export function selectNeedsFastPolling(s: {
  tasks: readonly TaskItem[];
  stats: TaskStats;
  connected: boolean;
  refreshScope: TasksRefreshScope | null;
  optimisticActive: ReadonlySet<string>;
  optimisticActiveScriptFile: ReadonlySet<string>;
}): boolean {
  // Disabled scope makes refreshTasks send no requests, so no project's marks can be reconciled; exclude all.
  const scope = s.refreshScope;
  return (
    !s.connected ||
    s.stats.queued + s.stats.running > 0 ||
    s.tasks.some((t) => isOccupyingStatus(t.status)) ||
    (scope !== null &&
      (hasOptimisticInScope(s.optimisticActive, scope.projectName) ||
        hasOptimisticInScope(s.optimisticActiveScriptFile, scope.projectName)))
  );
}

/**
 * Occupancy predicate: tasks still occupy resources, disabling buttons and entering occupancy sets. Match backend dedupe
 * ACTIVE_TASK_STATUSES: duplicate submissions for one resource reuse existing tasks. Calling it idle here
 * would falsely enable buttons while submissions create no new tasks.
 */
export function isOccupyingStatus(status: TaskStatus): boolean {
  return status === "queued" || status === "running";
}

/** Script planning has three task types by project mode; each project uses only one. */
export const SCRIPT_PLAN_TASK_TYPES: ReadonlySet<string> = new Set([
  "text_drama_script_plan",
  "text_narration_script_plan",
  "text_reference_script_plan",
]);

/**
 * Task resource kind. Except image_edit, task_type is the kind; image_edit shares one type across
 * character/scene/prop/product/storyboard, identifying its real kind through resource_type.
 * Map it to that slot so editing and generation for the same resource share occupancy and exclude one another.
 * Likewise map `voice_sample`, character TTS auditions, to `character`: asset generation, editing and uploads
 * share its slot (see {@link enqueueCharacterVoiceSample}). Optimistic requests already mark
 * `character` before sending; persisted rows must use the same kind for gap-free handoff.
 * Map all three planning types ({@link SCRIPT_PLAN_TASK_TYPES}) to `text_script_plan`,
 * so entries need not choose slots by project mode.
 *
 * The two assertions are data boundaries: {@link TaskItem} values come from the backend beyond TS control.
 * Backend kinds absent from {@link ResourceKind} match no occupancy slot and degrade to no occupancy,
 * matching previous behavior. The union type prevents frontend callers' own kind typos.
 */
export function taskResourceKind(task: TaskItem): ResourceKind | "" {
  if (task.task_type === "image_edit") {
    return (task.resource_type as ResourceKind | null) ?? "";
  }
  if (task.task_type === "voice_sample") {
    return "character";
  }
  if (SCRIPT_PLAN_TASK_TYPES.has(task.task_type)) {
    return "text_script_plan";
  }
  return task.task_type as ResourceKind;
}

/**
 * Invariant 2: group by resource_id and choose greatest updated_at among rows for the same resource.
 * Optionally prefilter by projectName / taskType; store order is not guaranteed, so compare updated_at explicitly.
 */
export function selectLatestTaskByResource(
  tasks: TaskItem[],
  filter: { projectName?: string; taskType?: string } = {},
): Map<string, TaskItem> {
  const latest = new Map<string, TaskItem>();
  for (const task of tasks) {
    if (filter.projectName !== undefined && task.project_name !== filter.projectName) continue;
    if (filter.taskType !== undefined && taskResourceKind(task) !== filter.taskType) continue;
    const prev = latest.get(task.resource_id);
    if (!prev || task.updated_at > prev.updated_at) latest.set(task.resource_id, task);
  }
  return latest;
}

/**
 * Resource IDs matching taskType + projectName whose latest row is active.
 * Group latest rows by (resource_id, task.task_type): within one native task_type, new running/queued
 * retries must not be hidden by old failed rows. But image_edit and its target's generation are separate types,
 * sharing only taskResourceKind occupancy slots. The backend does not guarantee exclusion, so they can coexist.
 * One latest row across both would let a newer terminal edit (success/failure) hide running generation
 * or vice versa, falsely freeing the resource. Instead choose latest rows independently per task_type
 * and count occupancy whenever any type's latest row is active.
 *
 * Optimistic occupancy: between request dispatch and the refresh that inserts its task, the store lacks
 * a row and considers the resource idle. The gap covers request roundtrip and one polling interval; marks
 * force busy-rate polling (see {@link selectNeedsFastPolling}), bounding it by the busy rather than idle
 * interval. Other entries may submit during this gap. Shared image_edit/generation slots expose the first
 * cross-task_type concurrent submission case; same-type concurrency is already blocked by backend
 * deduplication (`idx_tasks_dedupe_active`), whose key includes task_type
 * and therefore does not block cross-type concurrency.
 *
 * The enqueue action layer marks `optimisticActive` through {@link OptimisticHandle} **before sending**,
 * rolling back on failure to cover the full gap. Filter by (projectName, resourceKind) into occupancy
 * until awaited real rows appear; status is irrelevant to handoff, presence yields to real data.
 * Marks claim their own rows by task_id. See the optimistic mark encoding/handoff section
 * above for the criterion and residual risks.
 */
export function selectActiveResourceIds(
  tasks: TaskItem[],
  taskType: ResourceKind,
  projectName: string,
  optimisticActive: ReadonlySet<string> = EMPTY_OPTIMISTIC,
): Set<string> {
  const latestByResourceAndTaskType = new Map<string, TaskItem>();
  for (const task of tasks) {
    if (task.project_name !== projectName) continue;
    if (taskResourceKind(task) !== taskType) continue;
    const key = `${task.resource_id}\0${task.task_type}`;
    const prev = latestByResourceAndTaskType.get(key);
    if (!prev || task.updated_at > prev.updated_at) latestByResourceAndTaskType.set(key, task);
  }
  const ids = new Set<string>();
  for (const task of latestByResourceAndTaskType.values()) {
    if (isOccupyingStatus(task.status)) ids.add(task.resource_id);
  }
  // Trimming runs in setTasks; selectors may see a new snapshot first, so apply the same criterion here on demand.
  const landed = landedTaskIds(tasks);
  for (const key of optimisticActive) {
    const [kProject, kResourceKind, kResourceId] = key.split("\0");
    if (kProject !== projectName || kResourceKind !== taskType) continue;
    if (keepMark(key, landed) !== null) ids.add(kResourceId);
  }
  return ids;
}

const EMPTY_OPTIMISTIC: ReadonlySet<string> = new Set();

/**
 * Unified submit-time occupancy check: fresh `getState()` read + {@link selectActiveResourceIds} +
 * key construction, letting write controls check directly at submission without duplicating these steps.
 */
export function isResourceBusy(kind: ResourceKind, projectName: string, resourceId: string): boolean {
  const { tasks, optimisticActive } = useTasksStore.getState();
  return selectActiveResourceIds(tasks, kind, projectName, optimisticActive).has(resourceId);
}

// Use task-target.ts stripScriptsPrefix normalization: episode metadata script_file
// always has a `scripts/` prefix (ProjectManager._apply_episode_sync), while task script_file
// comes from each enqueue caller: routers pass WebUI form values directly, Agent tools enforce bare names through validate_script_filename,
// so formats may differ. Normalize here before comparison instead of assuming callers already trimmed them.
function stripScriptsPrefix(path: string): string {
  return path.replace(/^scripts\//, "");
}

/**
 * Whether taskType has an active task for scriptFile. Do not apply latest-row grouping: any existing
 * active task counts for coarse episode occupancy. Grid resource_id is grid_id rather than storyboard
 * segment_id, so it cannot enter selectActiveResourceIds resource checks. Grid splitting overwrites
 * several episode storyboard files; use scriptFile to detect running grid tasks
 * and disable storyboard editing while grid assembly is enabled, avoiding concurrent edits/splits to one file.
 *
 * Optimistic occupancy covers grid request dispatch until polling inserts its task. During that gap,
 * the store has no corresponding episode grid task, so editing falsely appears free and may write
 * the same storyboard current image concurrently with splitting. Semantics match {@link selectActiveResourceIds}
 * optimistic occupancy, grouping by scriptFile because grid tasks cannot group by resource_id as explained above.
 */
export function selectHasActiveTaskForScriptFile(
  tasks: TaskItem[],
  taskType: string,
  scriptFile: string,
  projectName: string,
  optimisticActiveScriptFile: ReadonlySet<string> = EMPTY_OPTIMISTIC,
): boolean {
  const normalized = stripScriptsPrefix(scriptFile);
  const hasRealActive = tasks.some(
    (task) =>
      task.project_name === projectName &&
      task.task_type === taskType &&
      task.script_file != null &&
      stripScriptsPrefix(task.script_file) === normalized &&
      isOccupyingStatus(task.status),
  );
  if (hasRealActive) return true;

  const landed = landedTaskIds(tasks);
  for (const key of optimisticActiveScriptFile) {
    const [kProject, kTaskType, kScriptFile] = key.split("\0");
    if (kProject !== projectName || kTaskType !== taskType || kScriptFile !== normalized) continue;
    if (keepMark(key, landed) !== null) return true;
  }
  return false;
}

/**
 * ScriptFile-level submit-time check, analogous to {@link isResourceBusy} with fresh `getState()` reads.
 *
 * As for resource-level checks, render-time props reflect the last render; users can still click between store updates
 * and rerender commit. Use scriptFile because grid task resource_id is grid_id and cannot enter storyboard
 * resource_id checks (see {@link selectHasActiveTaskForScriptFile}).
 * Missing scriptFile/projectName returns false, matching the hook.
 */
export function isScriptFileBusy(
  taskType: string,
  scriptFile: string | undefined | null,
  projectName: string | undefined | null,
): boolean {
  if (!scriptFile || !projectName) return false;
  const { tasks, optimisticActiveScriptFile } = useTasksStore.getState();
  return selectHasActiveTaskForScriptFile(
    tasks,
    taskType,
    scriptFile,
    projectName,
    optimisticActiveScriptFile,
  );
}

/** Hook form of {@link selectHasActiveTaskForScriptFile}; missing scriptFile/projectName returns false. */
export function useHasActiveTaskForScriptFile(
  taskType: string,
  scriptFile: string | undefined | null,
  projectName: string | undefined | null,
): boolean {
  return useTasksStore((s) =>
    scriptFile && projectName
      ? selectHasActiveTaskForScriptFile(
          s.tasks,
          taskType,
          scriptFile,
          projectName,
          s.optimisticActiveScriptFile,
        )
      : false,
  );
}

// Reuse one empty set without projectName to retain hook-reference stability.
const EMPTY_ACTIVE_IDS: Set<string> = new Set();

/** Hook form of {@link selectActiveResourceIds}; missing projectName returns a stable empty set. */
export function useActiveResourceIds(
  taskType: ResourceKind,
  projectName: string | undefined | null,
): Set<string> {
  return useTasksStore(
    useShallow((s) =>
      projectName
        ? selectActiveResourceIds(s.tasks, taskType, projectName, s.optimisticActive)
        : EMPTY_ACTIVE_IDS,
    ),
  );
}

/** Hook form of {@link selectLatestTaskByResource}, prefiltered by project + type. */
export function useLatestTasksByResource(
  projectName: string,
  taskType: string,
): Map<string, TaskItem> {
  return useTasksStore(
    useShallow((s) => selectLatestTaskByResource(s.tasks, { projectName, taskType })),
  );
}

/**
 * Find queue rows by task_id.
 *
 * Workflow-state endpoints supply panel task observations without timestamps. The queue is authoritative for durations,
 * so look up task_ids instead of duplicating timestamps in workflow responses: two independently refreshed timestamp
 * copies for the same task inevitably contradict. Omit IDs absent from the queue Map,
 * letting callers hide the whole block. Return original store objects rather than trimmed subsets
 * so shallow comparison succeeds when task-list refreshes leave content unchanged.
 */
export function useTaskRowsByIds(taskIds: readonly string[]): Map<string, TaskItem> {
  const wanted = new Set(taskIds);
  return useTasksStore(
    useShallow((s) => {
      const rows = new Map<string, TaskItem>();
      for (const task of s.tasks) {
        if (wanted.has(task.task_id)) rows.set(task.task_id, task);
      }
      return rows;
    }),
  );
}
