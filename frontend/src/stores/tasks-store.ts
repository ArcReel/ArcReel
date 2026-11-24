import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { TaskItem, TaskStats, TaskStatus } from "@/types";
import { isTerminalStatus } from "@/types/task";

const TASKS_PAGE_SIZE = 200;
const VOICE_SAMPLE_LOOKUP_TIMEOUT_MS = 10_000;

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

export type ImageEditResourceKind = Extract<
  ResourceKind,
  "character" | "character_derivative" | "scene" | "prop" | "product" | "storyboard"
>;

export interface TasksRefreshScope {
  projectName: string | null;
}

export interface OptimisticHandle {
  settle: (taskIds: string[]) => void;
  rollback: () => void;
}

interface TasksState {
  tasks: TaskItem[];
  stats: TaskStats;
  connected: boolean;
  refreshScope: TasksRefreshScope | null;
  optimisticActive: Set<string>;
  optimisticActiveScriptFile: Set<string>;

  setTasks: (tasks: TaskItem[] | ((prev: TaskItem[]) => TaskItem[])) => void;
  setStats: (stats: TaskStats) => void;
  setConnected: (connected: boolean) => void;
  setRefreshScope: (scope: TasksRefreshScope | null) => void;
  refreshTasks: () => Promise<void>;
  settleVoiceSample: (task: TaskItem) => void;
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

const TASK_ID_SEP = "\u0001";

let optimisticSeq = 0;

function encodeTaskIds(taskIds: readonly string[]): string {
  return taskIds.join(TASK_ID_SEP);
}

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

function withTaskIds(key: string, taskIds: readonly string[]): string {
  return key.slice(0, key.lastIndexOf("\0") + 1) + encodeTaskIds(taskIds);
}

type OptimisticField = "optimisticActive" | "optimisticActiveScriptFile";

function supersedingTaskIds(tasks: TaskItem[], field: OptimisticField): Set<string> {
  const ids = new Set<string>();
  for (const task of tasks) {
    if (field === "optimisticActive" && task.task_type === "voice_sample" && !isTerminalStatus(task.status)) {
      continue;
    }
    ids.add(task.task_id);
  }
  return ids;
}

function keepMark(key: string, landed: ReadonlySet<string>): string | null {
  const taskIds = markTaskIds(key);
  if (taskIds.length === 0) return key;
  const remaining = taskIds.filter((id) => !landed.has(id));
  if (remaining.length === 0) return null;
  return remaining.length === taskIds.length ? key : withTaskIds(key, remaining);
}

function pruneSupersededOptimistic(
  landed: ReadonlySet<string>,
  marks: ReadonlySet<string>,
): Set<string> {
  const next = new Set<string>();
  for (const key of marks) {
    const kept = keepMark(key, landed);
    if (kept !== null) next.add(kept);
  }
  return next;
}

export const useTasksStore = create<TasksState>((set, get) => {
  let refreshRunning = false;
  let refreshQueued = false;
  let queuedResolvers: Array<() => void> = [];
  let baselineScopeKey: string | null = null;

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

  let reconciliationController: AbortController | undefined;
  let scopeGeneration = 0;
  let voiceSamplesSettledDuringList: Map<string, TaskItem> | undefined;

  const reconcileMissingVoiceSamples = async (projectName: string | null): Promise<void> => {
    const listed = new Set(get().tasks.map((task) => task.task_id));
    const taskIds = new Set(
      [...get().optimisticActive].flatMap((key) => {
        const [markProject, , , taskType] = key.split("\0");
        return taskType === "voice_sample" && (projectName === null || projectName === markProject)
          ? markTaskIds(key).filter((taskId) => !listed.has(taskId))
          : [];
      }),
    );
    if (taskIds.size === 0) return;

    const controller = new AbortController();
    reconciliationController = controller;
    const { signal } = controller;
    const aborted = new Promise<void>((resolve) => {
      signal.addEventListener("abort", () => resolve(), { once: true });
    });
    const deadline = setTimeout(() => controller.abort(), VOICE_SAMPLE_LOOKUP_TIMEOUT_MS);
    try {
      const lookups = Promise.all(
        [...taskIds].map(async (taskId) => {
          const task = await API.getTask(taskId, { signal }).catch(() => null);
          if (!signal.aborted && task && isTerminalStatus(task.status)) {
            get().settleVoiceSample(task);
          }
        }),
      );
      await Promise.race([lookups, aborted]);
    } finally {
      clearTimeout(deadline);
      if (reconciliationController === controller) reconciliationController = undefined;
    }
  };

  const runTasksRefresh = async (): Promise<void> => {
    const scope = get().refreshScope;
    if (!scope) return;
    const { projectName } = scope;

    const generation = scopeGeneration;
    const scopeUnchanged = () => scopeGeneration === generation;
    const settledDuringList = new Map<string, TaskItem>();
    voiceSamplesSettledDuringList = settledDuringList;

    try {
      const [tasksRes, statsRes] = await Promise.all([
        API.listTasks({ projectName: projectName ?? undefined, pageSize: TASKS_PAGE_SIZE }),
        API.getTaskStats(projectName ?? null),
      ]);
      voiceSamplesSettledDuringList = undefined;
      if (!scopeUnchanged()) return;
      const items = tasksRes.items.map((task) =>
        isTerminalStatus(task.status) ? task : (settledDuringList.get(task.task_id) ?? task),
      );
      const scopeKey = projectName ?? "";
      const unitsStale =
        baselineScopeKey === scopeKey &&
        hasNewlySucceededReferenceUnitAsset(get().tasks, items);
      baselineScopeKey = scopeKey;
      get().setTasks(items);
      get().setStats(statsRes.stats);
      get().setConnected(true);
      if (unitsStale) {
        useAppStore.getState().invalidateReferenceVideoUnits();
      }
      if (scopeUnchanged()) await reconcileMissingVoiceSamples(projectName);
    } catch {
      if (!scopeUnchanged()) return;
      get().setConnected(false);
    } finally {
      voiceSamplesSettledDuringList = undefined;
    }
  };

  function beginMark(
    field: OptimisticField,
    keyOf: (taskIds: readonly string[]) => string,
  ): OptimisticHandle {
    const pendingKey = keyOf([]);
    set((s) => {
      const next = pruneSupersededOptimistic(supersedingTaskIds(s.tasks, field), s[field]);
      next.add(pendingKey);
      return { [field]: next };
    });

    const drop = (replacement: string | null): void => {
      if (!get()[field].has(pendingKey)) return;
      set((s) => {
        const next = new Set(s[field]);
        next.delete(pendingKey);
        if (replacement !== null) {
          const kept = keepMark(replacement, supersedingTaskIds(s.tasks, field));
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
      if (get().refreshScope?.projectName !== scope?.projectName) {
        scopeGeneration++;
        const previous = reconciliationController;
        reconciliationController = undefined;
        previous?.abort();
      }
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
        const next = typeof tasks === "function" ? tasks(s.tasks) : tasks;
        return {
          tasks: next,
          optimisticActive: pruneSupersededOptimistic(
            supersedingTaskIds(next, "optimisticActive"),
            s.optimisticActive,
          ),
          optimisticActiveScriptFile: pruneSupersededOptimistic(
            supersedingTaskIds(next, "optimisticActiveScriptFile"),
            s.optimisticActiveScriptFile,
          ),
        };
      }),
    setStats: (stats) => set({ stats }),
    setConnected: (connected) => set({ connected }),
    settleVoiceSample: (task) => {
      voiceSamplesSettledDuringList?.set(task.task_id, task);
      set((s) => ({
        tasks: s.tasks.map((row) =>
          row.task_id === task.task_id && !isTerminalStatus(row.status) ? task : row,
        ),
        optimisticActive: pruneSupersededOptimistic(new Set([task.task_id]), s.optimisticActive),
      }));
    },
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

export function isActiveStatus(status: TaskStatus): boolean {
  return status === "queued" || status === "running";
}

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

export function selectNeedsFastPolling(s: {
  tasks: readonly TaskItem[];
  stats: TaskStats;
  connected: boolean;
  refreshScope: TasksRefreshScope | null;
  optimisticActive: ReadonlySet<string>;
  optimisticActiveScriptFile: ReadonlySet<string>;
}): boolean {
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

export function isOccupyingStatus(status: TaskStatus): boolean {
  return status === "queued" || status === "running";
}

export const SCRIPT_PLAN_TASK_TYPES: ReadonlySet<string> = new Set([
  "text_drama_script_plan",
  "text_narration_script_plan",
  "text_reference_script_plan",
]);

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
  const landed = supersedingTaskIds(tasks, "optimisticActive");
  for (const key of optimisticActive) {
    const [kProject, kResourceKind, kResourceId] = key.split("\0");
    if (kProject !== projectName || kResourceKind !== taskType) continue;
    if (keepMark(key, landed) !== null) ids.add(kResourceId);
  }
  return ids;
}

const EMPTY_OPTIMISTIC: ReadonlySet<string> = new Set();

export function isResourceBusy(kind: ResourceKind, projectName: string, resourceId: string): boolean {
  const { tasks, optimisticActive } = useTasksStore.getState();
  return selectActiveResourceIds(tasks, kind, projectName, optimisticActive).has(resourceId);
}

function stripScriptsPrefix(path: string): string {
  return path.replace(/^scripts\//, "");
}

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

  const landed = supersedingTaskIds(tasks, "optimisticActiveScriptFile");
  for (const key of optimisticActiveScriptFile) {
    const [kProject, kTaskType, kScriptFile] = key.split("\0");
    if (kProject !== projectName || kTaskType !== taskType || kScriptFile !== normalized) continue;
    if (keepMark(key, landed) !== null) return true;
  }
  return false;
}

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

const EMPTY_ACTIVE_IDS: Set<string> = new Set();

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

export function useLatestTasksByResource(
  projectName: string,
  taskType: string,
): Map<string, TaskItem> {
  return useTasksStore(
    useShallow((s) => selectLatestTaskByResource(s.tasks, { projectName, taskType })),
  );
}

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
