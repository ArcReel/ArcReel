import { create } from "zustand";
import type { ProjectData, ProjectSummary, EpisodeScript } from "@/types";
import { API } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import { useAppStore } from "./app-store";

/**
 * {@link ProjectsState.refreshProject} settlement results:
 * - `success` — stores synchronized latest data
 * - `failed` — requests failed (network / server errors), invoking `onError`
 * - `cancelled` — unsynchronized without errors: project-switch cancellation-domain rotation, superseded queued requests, etc.
 *   Callers handle silently without error notices
 */
export type RefreshProjectResult = "success" | "failed" | "cancelled";

/** Optional {@link ProjectsState.refreshProject} behavior. */
interface RefreshProjectOptions {
  /** Entity revision keys to invalidate after successful refreshes (retaining old StudioCanvasRouter variant semantics). */
  invalidateKeys?: string[];
  /** Callback with errors on every failed getProject, for optional notices, preserving old-data retention. */
  onError?: (err: unknown) => void;
}

interface ProjectsState {
  // List
  projects: ProjectSummary[];
  projectsLoading: boolean;

  // Current project detail
  currentProjectName: string | null;
  currentProjectData: ProjectData | null;
  currentScripts: Record<string, EpisodeScript>;
  projectDetailLoading: boolean;

  // Per-project increasing revisions for project.json + canonical script snapshots.
  projectSnapshotRevisions: Record<string, number>;

  // Create modal
  showCreateModal: boolean;
  creatingProject: boolean;

  // Asset fingerprints (path → mtime_ns)
  assetFingerprints: Record<string, number>;

  /**
   * Internal accounting: whether at least one real project was ever established during store lifetimes. Only
   * {@link refreshProject} uses this to distinguish `currentProjectName` `null` as never-loaded
   * stores versus route-switch transitions, never UI consumption.
   */
  hasLoadedAnyProject: boolean;

  // Actions
  setProjects: (projects: ProjectSummary[]) => void;
  setProjectsLoading: (loading: boolean) => void;
  /**
   * Write current projects. Name ownership changes also rotate project cancellation domains: previous-project requests on old domains cancel
   * immediately and late responses cannot write stores (refreshProject project cancellation domains). Beyond a plain setter,
   * every switching path (route effects/cleanup, demo-workbench takeover) converges here.
   */
  setCurrentProject: (
    name: string | null,
    data: ProjectData | null,
    scripts?: Record<string, EpisodeScript>,
    fingerprints?: Record<string, number>,
  ) => void;
  setProjectDetailLoading: (loading: boolean) => void;
  setShowCreateModal: (show: boolean) => void;
  setCreatingProject: (creating: boolean) => void;
  updateAssetFingerprints: (fps: Record<string, number>) => void;
  getAssetFingerprint: (path: string) => number | null;
  /**
   * Refresh current project data into stores, returning discriminated unions (see {@link RefreshProjectResult}).
   *
   * One entry consolidates previously scattered caller semantics, eliminating concurrent refresh races where later completions
   * overwrite earlier ones:
   * - **In-flight merging**: at most one getProject in flight; refreshes arriving during it merge
   *   into one follow-up round using latest name / invalidateKeys.
   * - **Retain on failure**: failed getProject never overwrites currentProjectData, returning `"failed"` for
   *   caller notices (onError also invoked).
   * - **Project cancellation domains**: requests use AbortSignal rotating with current projects; switching invalidates previous
   *   in-flight/late refreshes, returning `"cancelled"`, never failure (no onError).
   */
  refreshProject: (name: string, options?: RefreshProjectOptions) => Promise<RefreshProjectResult>;
}

export const useProjectsStore = create<ProjectsState>((set, get) => {
  // In-flight refresh merging coordination, nonreactive singletons outside store state to avoid subscription rerenders.
  let refreshRunning = false;
  let refreshQueued = false;
  let queuedName: string | null = null;
  let queuedKeys: string[] = [];
  let queuedOnErrors: Array<(err: unknown) => void> = [];
  // Queue-round caller resolvers accumulate separately from curResolvers; sharing batches must not imply
  // sharing final results. Each caller receives only outcomes from rounds actually serving it.
  let queuedResolvers: Array<(result: RefreshProjectResult) => void> = [];

  // Project cancellation domains: current-project data requests attach to this controller. Project switches rotate (abort old,
  // replace new), immediately cancelling old-project requests and preventing late writes. Active controllers after rotation
  // are always un-aborted, never permanently invalidating subsequent refreshes.
  // Successful refresh writes changing null names to projects also rotate, aborting the domain this round just finished using.
  // Every later round reads active domains afresh, so unaffected; revisit if other project-level requests attach in future.
  let projectScope = new AbortController();
  const rotateProjectScope = () => {
    projectScope.abort();
    projectScope = new AbortController();
  };

  // Execute refresh loops: queued while reruns replace recursion; failures also consume queues until no new work remains.
  // Resolve each round's curResolvers immediately, without waiting for later queued rounds; otherwise earlier callers'
  // return outcomes are overwritten by unrelated later rounds (for example successful reorder refreshes become false after
  // failed queued SSE refreshes, preventing UI selection progression).
  const runRefresh = async (
    name: string,
    keys: string[],
    onError: ((err: unknown) => void) | undefined,
    resolvers: Array<(result: RefreshProjectResult) => void>,
  ): Promise<void> => {
    let curName = name;
    let curKeys = keys;
    let curOnErrors = onError ? [onError] : [];
    let curResolvers = resolvers;
    let again = true;
    while (again) {
      again = false;
      let result: RefreshProjectResult = "cancelled";
      // Read active cancellation domains each round: queued rounds use domains active at their own start, never prior rounds'.
      const signal = projectScope.signal;
      try {
        const res = await API.getProject(curName, { signal });
        // With null currentProjectName, permit only stores that never established any real project during their entire lifetimes:
        // refreshProject then owns initial project-data establishment, with no current project to compare.
        // After any project existed, later null values are route transitions between cleanup clearing old projects
        // and asynchronous new-project loads. Permit no project-name writes in this window,
        // including even older projects only now settling (see hasLoadedAnyProject comments),
        // or old data writes before new projects' own data arrives.
        const { currentProjectName, hasLoadedAnyProject } = get();
        // If another more-eligible project queued while requests were in flight, current responses belong to soon-replaced
        // old projects. Skip data/name writes instead of overwriting projects the queue will load.
        // More-eligible means queued projects are already current (routes persisted names before refresh, completing switches)
        // or no current projects exist (none ever established; latest queued intent defines targets). Conversely, callers holding
        // departed-project names (postwrite refreshes, SSE) necessarily fail current-project checks below and write no bytes;
        // never yield to them: this round owns current-project data, and discarding it leaves nobody
        // to load it again.
        const supersededByOtherProject =
          refreshQueued &&
          queuedName !== null &&
          queuedName !== curName &&
          (currentProjectName === null || queuedName === currentProjectName);
        // Rotated cancellation domains mean current projects changed midflight (other routes, demo takeovers,
        // or clearing on workbench exit). These bypass refreshProject, invisible to queue deduplication, requiring
        // cancellation domains. Abort/response settlement are asynchronous; response-before-abort does not reject network
        // requests, so recheck aborted before writes.
        // Both above checks only block projects departed when requests began. Callers such as postwrite refreshes
        // may capture old names but invoke only after switching completed,
        // receiving new-project active un-aborted domains. Also recheck curName remains
        // current before writes, preventing old data overwriting newly selected projects.
        const isCurrentProject =
          currentProjectName === curName || (currentProjectName === null && !hasLoadedAnyProject);
        if (!supersededByOtherProject && !signal.aborted && isCurrentProject) {
          get().setCurrentProject(curName, res.project, res.scripts ?? {}, res.asset_fingerprints);
          if (curKeys.length > 0) {
            useAppStore.getState().invalidateEntities(curKeys);
          }
          result = "success";
        }
        // No writes without errors (superseded / aborted / noncurrent) settle "cancelled",
        // allowing silent callers, distinct from actual request failures.
      } catch (err) {
        // Domain-rotation aborts are normal project switches, never refresh failures: settle
        // "cancelled" without onError, avoiding synchronization errors on every switch.
        // Retain on failure: neither outcome overwrites currentProjectData.
        if (signal.aborted) {
          result = "cancelled";
        } else {
          result = "failed";
          curOnErrors.forEach((cb) => {
            try {
              cb(err);
            } catch (callbackErr) {
              console.error("refreshProject onError 回调失败", callbackErr);
            }
          });
        }
      }
      curResolvers.forEach((resolve) => resolve(result));
      if (refreshQueued) {
        refreshQueued = false;
        curName = queuedName ?? curName;
        curKeys = queuedKeys;
        curOnErrors = queuedOnErrors;
        curResolvers = queuedResolvers;
        queuedName = null;
        queuedKeys = [];
        queuedOnErrors = [];
        queuedResolvers = [];
        again = true;
      }
    }
    refreshRunning = false;
  };

  return {
    projects: [],
    projectsLoading: false,
    currentProjectName: null,
    currentProjectData: null,
    currentScripts: {},
    projectDetailLoading: false,
    projectSnapshotRevisions: {},
    showCreateModal: false,
    creatingProject: false,
    assetFingerprints: {},
    hasLoadedAnyProject: false,

    setProjects: (projects) => set({ projects }),
    setProjectsLoading: (loading) => set({ projectsLoading: loading }),
    setCurrentProject: (name, data, scripts, fingerprints) => {
      // Rotate cancellation domains whenever current-project ownership changes. Every switch (route effects/cleanup, demo-workbench
      // takeover) passes here, so callers need not propagate cancellation individually; unchanged callers receive protection.
      if (name !== get().currentProjectName) rotateProjectScope();
      set((s) => ({
        currentProjectName: name,
        currentProjectData: data,
        currentScripts: scripts ?? {},
        projectSnapshotRevisions:
          name === null || data === null
            ? s.projectSnapshotRevisions
            : {
                ...s.projectSnapshotRevisions,
                [name]: (s.projectSnapshotRevisions[name] ?? 0) + 1,
              },
        assetFingerprints: fingerprints ?? s.assetFingerprints,
        hasLoadedAnyProject: s.hasLoadedAnyProject || name !== null,
      }));
    },
    setProjectDetailLoading: (loading) => set({ projectDetailLoading: loading }),
    setShowCreateModal: (show) => set({ showCreateModal: show }),
    setCreatingProject: (creating) => set({ creatingProject: creating }),
    updateAssetFingerprints: (fps) =>
      set((s) => ({ assetFingerprints: { ...s.assetFingerprints, ...fps } })),
    getAssetFingerprint: (path) => get().assetFingerprints[path] ?? null,

    refreshProject: (name, options) => {
      if (!name) return Promise.resolve("cancelled");
      // Onboarding demos use frontend constants, without refreshable server state.
      if (isDemoProject(name)) return Promise.resolve("success");
      const invalidateKeys = options?.invalidateKeys ?? [];
      return new Promise<RefreshProjectResult>((resolve) => {
        if (refreshRunning) {
          // Existing refreshes merge into one follow-up round with latest name and accumulated invalidateKeys /
          // onError / resolve, independently notifying every merged caller when queued rounds settle,
          // never sharing one initial/final outcome.
          //
          // Requests arriving during queueing for different projects supersede the old queued batch;
          // design retains exactly one follow-up slot with latest
          // name, never extra rounds for superseded projects. Old queued callers cannot
          // be served by the next actual round; without early settlement they merge and receive new-project
          // results despite requesting different projects (B queued then replaced by C would
          // wrongly receive true after C success, without B ever synchronized). Settle unsynchronized callers
          // immediately as "cancelled", never request errors, without onError.
          if (refreshQueued && queuedName !== null && queuedName !== name) {
            // Slots already serving current projects (typically initial route loads) must not be replaced by
            // different project names from departed callers. Such rounds cannot pass current-project
            // checks or write anything: settle "cancelled" immediately without taking slots. Otherwise
            // current-project refreshes disappear, with single-slot design providing no follow-up, so current
            // project data never fetches at all (initial loads stuck forever).
            const { currentProjectName } = get();
            if (currentProjectName !== null && queuedName === currentProjectName && name !== currentProjectName) {
              resolve("cancelled");
              return;
            }
            const supersededResolvers = queuedResolvers;
            queuedResolvers = [];
            queuedKeys = [];
            queuedOnErrors = [];
            supersededResolvers.forEach((r) => r("cancelled"));
          }
          refreshQueued = true;
          queuedName = name;
          queuedKeys = [...queuedKeys, ...invalidateKeys];
          if (options?.onError) {
            queuedOnErrors = [...queuedOnErrors, options.onError];
          }
          queuedResolvers = [...queuedResolvers, resolve];
          return;
        }
        refreshRunning = true;
        void runRefresh(name, invalidateKeys, options?.onError, [resolve]);
      });
    },
  };
});
