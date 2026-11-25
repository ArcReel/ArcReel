import { useEffect, useState } from "react";
import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import { parseItemIdEpisode } from "@/utils/episode-display";
import { useAppStore } from "@/stores/app-store";
import type { TaskItem, TaskProgress, TaskStats, TaskStatus } from "@/types";
import { isTerminalStatus } from "@/types/task";

const TASKS_PAGE_SIZE = 200;
const LOOKUP_LIMIT = 200;
const TASK_READ_TIMEOUT_MS = 10_000;

export type ResourceKind =
  | "character"
  | "character_derivative"
  | "voice_sample"
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
  | "text_script_plan";

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

type Target =
  | { kind: "resource"; resourceKind: ResourceKind; resourceId: string }
  | { kind: "script"; scriptFile: string };

type Mark = {
  projectName: string;
  taskType: string;
  target: Target;
} & (
  | { phase: "inflight" }
  | { phase: "awaiting_server"; pendingIds: string[] }
);

interface Lease {
  retainCount: number;
  projectName: string;
  status: "pending" | "resolved" | "missing";
}

interface TaskRows {
  progressById: Map<string, TaskProgress>;
  rowsById: Map<string, TaskItem>;
  recentTaskIds: string[];
  activeTaskIds: Set<string>;
  activeTruncated: boolean;
  optimisticMarks: Map<number, Mark>;
  tracked: Map<string, Lease>;
}

function emptyRows(): TaskRows {
  return {
    progressById: new Map(), rowsById: new Map(), recentTaskIds: [], activeTaskIds: new Set(), activeTruncated: false,
    optimisticMarks: new Map(), tracked: new Map(),
  };
}

interface TasksState extends TaskRows {
  stats: TaskStats;
  connected: boolean;
  refreshScope: TasksRefreshScope | null;
  setRefreshScope: (scope: TasksRefreshScope | null) => void;
  receiveProgress: (progress: TaskProgress) => void;
  refreshTasks: () => Promise<void>;
  retainTask: (projectName: string, taskId: string) => () => void;
  cancelTask: (id: string) => Promise<void>;
  retryDownload: (id: string) => Promise<void>;
  beginOptimisticActive: (project: string, kind: ResourceKind, id: string, taskType: string) => OptimisticHandle;
  beginOptimisticActiveForScriptFile: (project: string, taskType: string, file: string) => OptimisticHandle;
}

export const defaultTaskStats: TaskStats = {
  queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0,
};

export function selectRecentTasks(s: TaskRows): TaskItem[] {
  return s.recentTaskIds.map((id) => s.rowsById.get(id)!);
}

export function selectActiveTasks(s: TaskRows): TaskItem[] {
  return Array.from(s.activeTaskIds, (id) => s.rowsById.get(id)!);
}

export function useActiveEpisodePlan(projectName: string): TaskItem | undefined {
  return useTasksStore((s) => {
    const plans = selectActiveTasks(s).filter((row) => row.project_name === projectName && row.task_type === "text_episode_plan");
    return plans.find((row) => row.status === "running") ?? plans[0];
  });
}

const ASSET_TASK_KINDS: ReadonlySet<string> = new Set([
  "character",
  "character_derivative",
  "scene",
  "prop",
  "product",
  "voice_sample",
]);

const EPISODE_RESOURCE_PATTERN = /^episode-(\d+)$/;
const CANONICAL_SCRIPT_PATTERN = /^episode_(\d+)\.json$/;

function taskEpisodeNumber(task: TaskItem, targetScriptFile?: string | null): number | null {
  if (typeof task.episode === "number") return task.episode;
  const epMatch = EPISODE_RESOURCE_PATTERN.exec(task.resource_id);
  if (epMatch) return Number(epMatch[1]);
  const itemEp = parseItemIdEpisode(task.item_id ?? task.resource_id);
  if (itemEp !== null) return itemEp;
  if (task.script_file) {
    const normalized = stripScriptsPrefix(task.script_file);
    if (targetScriptFile && normalized === stripScriptsPrefix(targetScriptFile)) {
      const match = CANONICAL_SCRIPT_PATTERN.exec(normalized);
      if (match) return Number(match[1]);
    }
    const fileMatch = CANONICAL_SCRIPT_PATTERN.exec(normalized);
    if (fileMatch) return Number(fileMatch[1]);
  }
  return null;
}

function taskVisibilityPhase(status: TaskStatus): "active" | TaskStatus {
  return isActiveStatus(status) ? "active" : status;
}

function assetMatchesReferenced(resourceId: string, referencedAssets: ReadonlySet<string>): boolean {
  if (referencedAssets.has(resourceId)) return true;
  const owner = resourceId.split("/", 1)[0];
  return owner !== resourceId && referencedAssets.has(owner);
}

export function selectWorkflowEpisodeTaskSignal(
  s: TaskRows,
  projectName: string,
  episode: number | null,
  options?: {
    referencedAssets?: ReadonlySet<string> | null;
    scriptFile?: string | null;
  },
): string {
  const seen = new Set<string>();
  const parts: string[] = [];
  const referencedAssets = options?.referencedAssets ?? null;
  const targetScriptFile = options?.scriptFile ? stripScriptsPrefix(options.scriptFile) : null;
  for (const task of [...selectRecentTasks(s), ...selectActiveTasks(s)]) {
    if (task.project_name !== projectName || seen.has(task.task_id)) continue;
    seen.add(task.task_id);
    if (episode !== null) {
      const kind = taskResourceKind(task);
      if (kind !== "text_episode_plan") {
        if (ASSET_TASK_KINDS.has(kind)) {
          if (referencedAssets && !assetMatchesReferenced(task.resource_id, referencedAssets)) continue;
        } else {
          const taskEp = taskEpisodeNumber(task, targetScriptFile);
          const matchesScript =
            targetScriptFile !== null &&
            task.script_file !== null &&
            stripScriptsPrefix(task.script_file) === targetScriptFile;
          if (!matchesScript && taskEp !== null && taskEp !== episode) continue;
        }
      }
    }
    parts.push(`${task.task_id}:${taskVisibilityPhase(task.status)}`);
  }
  parts.sort();
  return parts.join("|");
}

export function selectStatusBarTaskSignal(
  s: TaskRows,
  projectName: string,
  targetEpisode: number | null,
  yieldToPanel: boolean,
): string {
  if (yieldToPanel) return "";
  const seen = new Set<string>();
  const parts: string[] = [];
  for (const task of [...selectRecentTasks(s), ...selectActiveTasks(s)]) {
    if (task.project_name !== projectName || seen.has(task.task_id)) continue;
    seen.add(task.task_id);
    if (targetEpisode !== null) {
      const kind = taskResourceKind(task);
      if (kind !== "text_episode_plan" && !ASSET_TASK_KINDS.has(kind)) {
        const taskEp = taskEpisodeNumber(task);
        if (taskEp !== null && taskEp !== targetEpisode) continue;
      }
    }
    parts.push(`${task.task_id}:${taskVisibilityPhase(task.status)}`);
  }
  parts.sort();
  return parts.join("|");
}

function versionOrder(a: TaskItem, b: TaskItem): number {
  const millis = Date.parse(a.updated_at) - Date.parse(b.updated_at);
  if (millis) return millis;
  const fraction = (stamp: string) => /\.(\d+)/.exec(stamp)?.[1].replace(/0+$/, "") ?? "";
  const left = fraction(a.updated_at);
  const right = fraction(b.updated_at);
  if (left !== right) return left > right ? 1 : -1;
  const rank = (status: TaskStatus) => status === "queued" ? 0 : status === "running" ? 1 : 2;
  return rank(a.status) - rank(b.status);
}

function pruneUnreferencedRows(s: TaskRows) {
  const referenced = new Set([...s.activeTaskIds, ...s.recentTaskIds, ...s.tracked.keys()]);
  for (const id of s.rowsById.keys()) if (!referenced.has(id)) s.rowsById.delete(id);
  for (const id of s.progressById.keys()) {
    if (!s.activeTaskIds.has(id) || s.rowsById.get(id)?.status !== "running") s.progressById.delete(id);
  }
  return s;
}

function reconcile(s: TaskRows, landed: Set<string>, missing = new Set<string>()) {
  for (const [id, lease] of s.tracked) {
    const row = s.rowsById.get(id);
    if (missing.has(id)) {
      s.tracked.set(id, { ...lease, status: "missing" });
    } else if (lease.status !== "missing" && row?.project_name === lease.projectName) {
      const status = isTerminalStatus(row.status) ? "resolved" : "pending";
      if (status !== lease.status) s.tracked.set(id, { ...lease, status });
    }
  }
  for (const [key, mark] of s.optimisticMarks) {
    if (mark.phase === "inflight") continue;
    const pendingIds = mark.pendingIds.filter((id) => {
      const row = s.rowsById.get(id);
      return !landed.has(id) && !missing.has(id) && !(row && isTerminalStatus(row.status));
    });
    if (!pendingIds.length) s.optimisticMarks.delete(key);
    else if (pendingIds.length !== mark.pendingIds.length) s.optimisticMarks.set(key, { ...mark, pendingIds });
  }
  return pruneUnreferencedRows(s);
}

function copyRows(s: TaskRows): TaskRows {
  return {
    progressById: new Map(s.progressById), rowsById: new Map(s.rowsById), activeTaskIds: new Set(s.activeTaskIds),
    recentTaskIds: s.recentTaskIds, activeTruncated: s.activeTruncated,
    optimisticMarks: new Map(s.optimisticMarks), tracked: new Map(s.tracked),
  };
}

function mergeRow(s: TaskRows, row: TaskItem) {
  const prev = s.rowsById.get(row.task_id);
  if (prev) {
    const order = versionOrder(row, prev);
    if (order < 0 || (order === 0 && prev.status !== row.status)) return false;
    if (order === 0 && prev.model && !row.model) row = { ...row, model: prev.model };
  }
  s.rowsById.set(row.task_id, row);
  if (isTerminalStatus(row.status)) s.activeTaskIds.delete(row.task_id);
  return true;
}

function hasNewReferenceOutput(row: TaskItem, previous: TaskItem | undefined): boolean {
  return (
    (row.task_type === "reference_video" ||
      row.task_type === "tts" ||
      row.task_type === "text_episode_script") &&
    row.status === "succeeded" &&
    previous?.status !== "succeeded"
  );
}

function acceptsProgress(s: TaskRows, progress: TaskProgress): boolean {
  const id = progress.task_id;
  return s.activeTaskIds.has(id) && s.rowsById.get(id)?.status === "running" &&
    progress.seq > (s.progressById.get(id)?.seq ?? 0);
}

export function mergeTaskSnapshot(
  prev: TaskRows,
  active: { items: TaskItem[]; truncated: boolean },
  recent: TaskItem[],
  protectedIds = new Set<string>(),
): TaskRows {
  const next = copyRows(prev);
  next.activeTruncated = active.truncated;
  if (!active.truncated) next.activeTaskIds = protectedIds;
  const landed = new Set<string>();
  for (const { progress, ...row } of active.items) {
    mergeRow(next, row);
    if (isActiveStatus(next.rowsById.get(row.task_id)!.status)) {
      next.activeTaskIds.add(row.task_id);
      if (progress?.task_id === row.task_id && acceptsProgress(next, progress)) next.progressById.set(row.task_id, progress);
      if (!active.truncated) landed.add(row.task_id);
    }
  }
  next.recentTaskIds = recent.map((row) => row.task_id);
  for (const row of recent) mergeRow(next, row);
  return reconcile(next, landed);
}

export const useTasksStore = create<TasksState>((set, get) => {
  let generation = 0;
  let revision = 0;
  let markSeq = 0;
  let connectedOnce = false;
  let writeRevisions = new Map<string, number>();
  let snapshotEvents: Map<string, TaskProgress> | null = null;
  let flight: Promise<void> | null = null;
  let waiters: Array<() => void> = [];
  let requestAbort: AbortController | undefined;

  function applyRows(items: TaskItem[], missing = new Set<string>()) {
    const next = copyRows(get());
    const landed = new Set<string>();
    let invalidateReferenceUnits = false;
    for (const row of items) {
      const previous = next.rowsById.get(row.task_id);
      if (!mergeRow(next, row)) continue;
      if (isActiveStatus(row.status)) next.activeTaskIds.add(row.task_id);
      writeRevisions.set(row.task_id, ++revision);
      landed.add(row.task_id);
      invalidateReferenceUnits ||= hasNewReferenceOutput(row, previous);
    }
    for (const id of missing) next.activeTaskIds.delete(id);
    set(reconcile(next, landed, missing));
    if (invalidateReferenceUnits) useAppStore.getState().invalidateReferenceVideoUnits();
  }

  async function lookupPending(projectName: string | null, seenRows: TaskItem[], epoch: number) {
    const seen = new Set(seenRows.map((row) => row.task_id));
    const ids = new Set<string>();
    const inScope = (project: string) => projectName === null || project === projectName;
    const { tracked, optimisticMarks } = get();
    for (const [id, lease] of tracked) {
      if (lease.status === "pending" && inScope(lease.projectName) && !seen.has(id)) ids.add(id);
    }
    for (const mark of optimisticMarks.values()) {
      if (mark.phase === "awaiting_server" && inScope(mark.projectName)) {
        for (const id of mark.pendingIds) ids.add(id);
      }
    }
    if (!ids.size) return;

    const controller = new AbortController();
    requestAbort = controller;
    const timeout = setTimeout(() => controller.abort(), TASK_READ_TIMEOUT_MS);
    const start = revision;
    const lookup = async (chunk: string[]) => {
      const result = await API.lookupTasks(chunk, projectName, controller.signal);
      if (generation !== epoch || controller.signal.aborted) return;
      const untouched = (id: string) => (writeRevisions.get(id) ?? 0) <= start;
      const items = result.items.filter((row) => untouched(row.task_id) || get().rowsById.has(row.task_id));
      applyRows(items, new Set(result.missing_ids.filter(untouched)));
    };
    try {
      const pending = [...ids];
      const batches = [];
      for (let i = 0; i < pending.length; i += LOOKUP_LIMIT) {
        batches.push(lookup(pending.slice(i, i + LOOKUP_LIMIT)));
      }
      await Promise.allSettled(batches);
    } finally {
      clearTimeout(timeout);
      if (requestAbort === controller) requestAbort = undefined;
    }
  }

  async function refresh() {
    const scope = get().refreshScope;
    if (!scope) return;
    const epoch = generation;
    const { projectName } = scope;
    const start = ++revision;
    writeRevisions.clear();
    const controller = new AbortController();
    requestAbort = controller;
    const { signal } = controller;
    const timeout = setTimeout(() => controller.abort(), TASK_READ_TIMEOUT_MS);
    const events = new Map<string, TaskProgress>();
    snapshotEvents = events;
    let observedActive: TaskItem[] = [];
    try {
      const [active, recent, stats] = await Promise.all([
        API.getActiveTasks(projectName, { signal }),
        API.listTasks({ projectName: projectName ?? undefined, pageSize: TASKS_PAGE_SIZE }, { signal }),
        API.getTaskStats(projectName, { signal }),
      ]);
      if (generation !== epoch) return;
      signal.throwIfAborted();
      const prev = get();
      const protectedIds = new Set([...prev.activeTaskIds].filter((id) => (writeRevisions.get(id) ?? 0) > start));
      const notDeletedLocally = (row: TaskItem) => !writeRevisions.has(row.task_id) || prev.rowsById.has(row.task_id);
      const next = mergeTaskSnapshot(
        prev,
        { ...active, items: active.items.filter(notDeletedLocally) },
        recent.items.filter(notDeletedLocally),
        protectedIds,
      );
      for (const progress of events.values()) {
        if (acceptsProgress(next, progress)) next.progressById.set(progress.task_id, progress);
      }
      const invalidate = connectedOnce && selectRecentTasks(next).some((row) =>
        hasNewReferenceOutput(row, prev.rowsById.get(row.task_id)),
      );
      connectedOnce = true;
      set({ ...next, stats: stats.stats, connected: true });
      if (invalidate) useAppStore.getState().invalidateReferenceVideoUnits();
      observedActive = active.items;
    } catch {
      controller.abort();
      if (generation === epoch) set({ connected: false });
    } finally {
      clearTimeout(timeout);
      if (requestAbort === controller) requestAbort = undefined;
    }
    if (snapshotEvents === events) snapshotEvents = null;
    if (generation === epoch) await lookupPending(projectName, observedActive, epoch);
  }

  function startRefresh(): Promise<void> {
    const epoch = generation;
    flight = refresh().finally(() => {
      if (generation !== epoch) return;
      flight = null;
      const next = waiters;
      waiters = [];
      if (next.length) void startRefresh().then(() => next.forEach((resolve) => resolve()));
    });
    return flight;
  }

  function begin(projectName: string, taskType: string, target: Target): OptimisticHandle {
    const key = ++markSeq;
    const mark: Mark = { projectName, taskType, target, phase: "inflight" };
    set({ optimisticMarks: new Map(get().optimisticMarks).set(key, mark) });
    function settle(ids: string[]) {
      if (get().optimisticMarks.get(key) !== mark) return;
      const next = copyRows(get());
      next.optimisticMarks.set(key, { ...mark, phase: "awaiting_server", pendingIds: [...new Set(ids)] });
      const landed = next.activeTruncated ? new Set<string>() : next.activeTaskIds;
      for (const id of ids) if (landed.has(id)) writeRevisions.set(id, ++revision);
      set(reconcile(next, landed));
    }
    return { settle, rollback: () => settle([]) };
  }

  return {
    ...emptyRows(), stats: defaultTaskStats, connected: false, refreshScope: null,
    setRefreshScope(scope) {
      if (scope && get().refreshScope?.projectName === scope.projectName) return;
      generation++;
      snapshotEvents = null;
      connectedOnce = false;
      waiters.forEach((resolve) => resolve());
      waiters = [];
      flight = null;
      requestAbort?.abort();
      writeRevisions = new Map();
      set({ ...emptyRows(), refreshScope: scope, stats: defaultTaskStats, connected: false });
    },
    receiveProgress(progress) {
      const state = get();
      if (!acceptsProgress(state, progress)) {
        const row = state.rowsById.get(progress.task_id);
        if (snapshotEvents && (!row || row.status === "queued") &&
            progress.seq > (snapshotEvents.get(progress.task_id)?.seq ?? 0)) {
          snapshotEvents.set(progress.task_id, progress);
        }
        return;
      }
      writeRevisions.set(progress.task_id, ++revision);
      set({ progressById: new Map(state.progressById).set(progress.task_id, progress) });
    },
    refreshTasks() {
      if (!flight) return startRefresh();
      return new Promise<void>((resolve) => waiters.push(resolve));
    },
    retainTask(projectName, id) {
      const epoch = generation;
      const { tracked, rowsById } = get();
      const existing = tracked.get(id);
      const row = rowsById.get(id);
      const lease: Lease = {
        projectName, retainCount: (existing?.retainCount ?? 0) + 1,
        status: existing?.status ?? (row?.project_name === projectName && isTerminalStatus(row.status) ? "resolved" : "pending"),
      };
      set({ tracked: new Map(tracked).set(id, lease) });
      let released = false;
      return () => {
        if (released || epoch !== generation) return;
        released = true;
        const state = copyRows(get());
        const lease = state.tracked.get(id);
        if (!lease) return;
        if (lease.retainCount === 1) state.tracked.delete(id);
        else state.tracked.set(id, { ...lease, retainCount: lease.retainCount - 1 });
        set(pruneUnreferencedRows(state));
      };
    },
    async cancelTask(id) {
      const epoch = generation;
      const { cancelled, skipped_terminal } = await API.cancelTask(id);
      if (epoch === generation) applyRows([...cancelled, ...skipped_terminal]);
    },
    async retryDownload(id) {
      const epoch = generation;
      const { task } = await API.retryTaskDownload(id);
      if (epoch === generation) applyRows([task]);
    },
    beginOptimisticActive: (project, resourceKind, resourceId, type) =>
      begin(project, type, { kind: "resource", resourceKind, resourceId }),
    beginOptimisticActiveForScriptFile: (project, type, file) =>
      begin(project, type, { kind: "script", scriptFile: stripScriptsPrefix(file) }),
  };
});

export function isActiveStatus(status: TaskStatus): boolean {
  return status === "queued" || status === "running";
}

export function selectNeedsFastPolling(s: TasksState): boolean {
  const inScope = (project: string) => s.refreshScope && (!s.refreshScope.projectName || s.refreshScope.projectName === project);
  return !s.connected || s.stats.running + s.stats.queued > 0 || s.activeTaskIds.size > 0 ||
    [...s.tracked.values()].some((lease) => lease.status === "pending" && inScope(lease.projectName)) ||
    [...s.optimisticMarks.values()].some((mark) => inScope(mark.projectName));
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
    const kind = task.resource_type ?? task.payload.resource_type;
    return typeof kind === "string" ? kind as ResourceKind : "";
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
    if (!prev || versionOrder(task, prev) > 0) latest.set(task.resource_id, task);
  }
  return latest;
}

export function selectActiveResourceIds(
  s: TaskRows,
  taskType: ResourceKind,
  projectName: string,
): Set<string> {
  const ids = new Set(selectActiveTasks(s).filter((row) =>
    row.project_name === projectName && taskResourceKind(row) === taskType,
  ).map((row) => row.resource_id));
  for (const { target, projectName: project } of s.optimisticMarks.values()) {
    if (project === projectName && target.kind === "resource" && target.resourceKind === taskType) ids.add(target.resourceId);
  }
  return ids;
}

export function isResourceBusy(kind: ResourceKind, projectName: string, resourceId: string): boolean {
  return selectActiveResourceIds(useTasksStore.getState(), kind, projectName).has(resourceId);
}

function stripScriptsPrefix(path: string): string {
  return path.replace(/^scripts\//, "");
}

export function selectHasActiveTaskForScriptFile(
  s: TaskRows, taskType: string, scriptFile: string, projectName: string,
): boolean {
  const normalized = stripScriptsPrefix(scriptFile);
  return selectActiveTasks(s).some((row) => row.project_name === projectName && row.task_type === taskType &&
    row.script_file && stripScriptsPrefix(row.script_file) === normalized) ||
    [...s.optimisticMarks.values()].some((mark) => mark.projectName === projectName && mark.taskType === taskType &&
      mark.target.kind === "script" && mark.target.scriptFile === normalized);
}

export function isScriptFileBusy(
  taskType: string,
  scriptFile: string | undefined | null,
  projectName: string | undefined | null,
): boolean {
  if (!scriptFile || !projectName) return false;
  return selectHasActiveTaskForScriptFile(useTasksStore.getState(), taskType, scriptFile, projectName);
}

export function useHasActiveTaskForScriptFile(
  taskType: string,
  scriptFile: string | undefined | null,
  projectName: string | undefined | null,
): boolean {
  return useTasksStore((s) =>
    scriptFile && projectName
      ? selectHasActiveTaskForScriptFile(
          s,
          taskType,
          scriptFile,
          projectName,
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
        ? selectActiveResourceIds(s, taskType, projectName)
        : EMPTY_ACTIVE_IDS,
    ),
  );
}

export function useLatestTasksByResource(
  projectName: string,
  taskType: string,
): Map<string, TaskItem> {
  return useTasksStore(
    useShallow((s) => selectLatestTaskByResource([...selectRecentTasks(s), ...selectActiveTasks(s)], { projectName, taskType })),
  );
}

export function useLatestEpisodeAuthoringTask(projectName: string, resourceId: string, enabled: boolean) {
  const recent = useLatestTasksByResource(projectName, "text_episode_script").get(resourceId);
  const fullPage = useTasksStore((s) => s.recentTaskIds.length >= TASKS_PAGE_SIZE);
  const connected = useTasksStore((s) => s.connected);
  const [archived, setArchived] = useState<TaskItem>();
  if (recent && archived) {
    setArchived(undefined);
  }
  useEffect(() => {
    if (!enabled || !connected || recent || !fullPage) return;
    const controller = new AbortController();
    void API.listTasks(
      { projectName, resourceId, taskType: "text_episode_script", pageSize: 1 },
      { signal: controller.signal },
    ).then(({ items }) => {
      if (!controller.signal.aborted) setArchived(items[0]);
    }).catch((err: unknown) => {
      if (!controller.signal.aborted) useAppStore.getState().pushToast(errMsg(err), "error");
    });
    return () => controller.abort();
  }, [enabled, connected, fullPage, recent, projectName, resourceId]);
  return recent ?? (fullPage && archived?.project_name === projectName && archived.resource_id === resourceId ? archived : undefined);
}

export function useTaskRowsByIds(taskIds: readonly string[]): Map<string, TaskItem> {
  return useTasksStore(
    useShallow((s) => {
      const rows = new Map<string, TaskItem>();
      for (const id of taskIds) {
        const task = s.rowsById.get(id);
        if (task) rows.set(id, task);
      }
      return rows;
    }),
  );
}
