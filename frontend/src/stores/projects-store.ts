import { create } from "zustand";
import type {
  EpisodeScript,
  ProjectData,
  ProjectStatus,
  ProjectSummary,
  WorkspaceResponse,
} from "@/types";
import { API } from "@/api";
import { ApiRequestError } from "@/api/errors";
import { isDemoProject } from "@/onboarding/demo-project";
import { useAppStore } from "./app-store";
import { useItemLocationsStore } from "./item-locations-store";

export type RefreshProjectResult = "success" | "failed" | "cancelled";

export type ActiveEpisodeScriptStatus = "idle" | "loading" | "ready" | "missing" | "error";

export interface ActiveEpisodeScriptState {
  projectName: string | null;
  episode: number | null;
  scriptFile: string | null;
  status: ActiveEpisodeScriptStatus;
  revision: string | null;
  error: string | null;
  generation: number;
}

export type ProjectRefreshScope = {
  episodes?: number[];
  assets?: boolean;
  projectWide?: boolean;
};

interface RefreshProjectOptions {
  invalidateKeys?: string[];
  onError?: (err: unknown) => void;
  scope?: ProjectRefreshScope;
  reloadActiveScript?: boolean;
}

export interface LoadEpisodeScriptOptions {
  force?: boolean;
  speculative?: boolean;
  onError?: (err: unknown) => void;
}

export function normalizeScriptFile(scriptFile: string | null | undefined): string | null {
  if (!scriptFile) return null;
  const normalized = scriptFile.replace(/^scripts\//, "").trim();
  return normalized || null;
}

export function episodeScriptRevisionKey(projectName: string, episode: number): string {
  return `${projectName}::${episode}`;
}

export function episodeHasFormalScript(
  episode: { script_file?: string; script_status?: string } | undefined,
): boolean {
  if (!episode) return false;
  const bound = normalizeScriptFile(episode.script_file);
  if (!bound) return false;
  return episode.script_status !== "none" && episode.script_status !== "segmented";
}

export function mergeWorkspaceProject(res: WorkspaceResponse): ProjectData {
  const summary = res.summary;
  const status: ProjectStatus = {
    currency: summary.currency,
    needs_repair: summary.needs_repair,
    repair_reason: summary.repair_reason,
    assets: summary.assets,
    episodes_summary: summary.episodes_summary,
    source_remaining: summary.source_remaining,
  };
  const byEpisode = new Map(summary.episodes.map((row) => [row.episode, row]));
  const episodes = (res.project.episodes ?? []).map((ep) => {
    const row = byEpisode.get(ep.episode);
    if (!row) return ep;
    return {
      ...ep,
      script_status: row.script_status,
      status: row.status,
      item_count: row.item_count,
      duration_seconds: row.duration_seconds,
      needs_replan: row.needs_replan,
      storyboards: row.storyboards,
      videos: row.videos,
    };
  });
  return {
    ...res.project,
    status,
    episodes,
  };
}

const IDLE_ACTIVE_SCRIPT: ActiveEpisodeScriptState = {
  projectName: null,
  episode: null,
  scriptFile: null,
  status: "idle",
  revision: null,
  error: null,
  generation: 0,
};

function mergeScopes(
  left: ProjectRefreshScope | undefined,
  right: ProjectRefreshScope | undefined,
): ProjectRefreshScope | undefined {
  if (!left || left.projectWide || !right || right.projectWide) return undefined;
  const episodes = new Set([...(left.episodes ?? []), ...(right.episodes ?? [])]);
  return {
    episodes: episodes.size > 0 ? [...episodes] : undefined,
    assets: Boolean(left.assets || right.assets),
    projectWide: false,
  };
}

function scopeTouchesEpisode(scope: ProjectRefreshScope | undefined, episode: number): boolean {
  if (!scope || scope.projectWide) return true;
  if (!scope.episodes) return false;
  return scope.episodes.includes(episode);
}

function scopeTouchesAssets(scope: ProjectRefreshScope | undefined): boolean {
  if (!scope || scope.projectWide) return true;
  return Boolean(scope.assets);
}

function isNotFoundError(err: unknown): boolean {
  return err instanceof ApiRequestError && err.status === 404;
}

interface ProjectsState {
  projects: ProjectSummary[];
  projectsLoading: boolean;

  currentProjectName: string | null;
  currentProjectData: ProjectData | null;
  currentScripts: Record<string, EpisodeScript>;
  activeEpisodeScript: ActiveEpisodeScriptState;
  projectDetailLoading: boolean;

  projectSnapshotRevisions: Record<string, number>;
  projectWideRevisions: Record<string, number>;
  episodeRevisions: Record<string, number>;
  assetRevisions: Record<string, number>;

  showCreateModal: boolean;
  creatingProject: boolean;

  assetFingerprints: Record<string, number>;

  hasLoadedAnyProject: boolean;

  setProjects: (projects: ProjectSummary[]) => void;
  setProjectsLoading: (loading: boolean) => void;
  setCurrentProject: (
    name: string | null,
    data: ProjectData | null,
    scripts?: Record<string, EpisodeScript>,
    fingerprints?: Record<string, number>,
    scope?: ProjectRefreshScope,
  ) => void;
  setProjectDetailLoading: (loading: boolean) => void;
  setShowCreateModal: (show: boolean) => void;
  setCreatingProject: (creating: boolean) => void;
  updateAssetFingerprints: (fps: Record<string, number>) => void;
  getAssetFingerprint: (path: string) => number | null;
  loadEpisodeScript: (
    projectName: string,
    episode: number,
    scriptFile: string,
    options?: LoadEpisodeScriptOptions,
  ) => Promise<RefreshProjectResult>;
  clearActiveEpisodeScript: () => void;
  refreshProject: (name: string, options?: RefreshProjectOptions) => Promise<RefreshProjectResult>;
}

export const useProjectsStore = create<ProjectsState>((set, get) => {
  let refreshRunning = false;
  let refreshQueued = false;
  let queuedName: string | null = null;
  let queuedKeys: string[] = [];
  let queuedScope: ProjectRefreshScope | undefined = undefined;
  let queuedHasScope = false;
  let queuedReloadActiveScript: boolean | undefined = undefined;
  let queuedOnErrors: Array<(err: unknown) => void> = [];
  let queuedResolvers: Array<(result: RefreshProjectResult) => void> = [];

  let projectScope = new AbortController();
  let scriptScope = new AbortController();
  let scriptGeneration = 0;

  const rotateProjectScope = () => {
    projectScope.abort();
    projectScope = new AbortController();
  };

  const rotateScriptScope = () => {
    scriptScope.abort();
    scriptScope = new AbortController();
    scriptGeneration += 1;
    return { signal: scriptScope.signal, generation: scriptGeneration };
  };

  const loadEpisodeScriptInternal = async (
    projectName: string,
    episode: number,
    rawScriptFile: string,
    options: LoadEpisodeScriptOptions = {},
  ): Promise<RefreshProjectResult> => {
    const scriptFile = normalizeScriptFile(rawScriptFile);
    if (!projectName || !scriptFile || isDemoProject(projectName)) return "cancelled";

    const current = get();
    const active = current.activeEpisodeScript;
    const sameTarget =
      active.projectName === projectName &&
      active.episode === episode &&
      active.scriptFile === scriptFile;

    if (
      !options.force &&
      sameTarget &&
      (active.status === "loading" || (active.status === "ready" && scriptFile in current.currentScripts))
    ) {
      return "success";
    }

    const { signal, generation } = rotateScriptScope();
    const keepExisting = sameTarget && scriptFile in current.currentScripts;
    set((s) => ({
      currentScripts: keepExisting ? s.currentScripts : {},
      activeEpisodeScript: {
        projectName,
        episode,
        scriptFile,
        status: "loading",
        revision: keepExisting ? s.activeEpisodeScript.revision : null,
        error: null,
        generation,
      },
    }));

    try {
      const snapshot = await API.getScript(projectName, scriptFile, { signal });
      const latest = get();
      if (signal.aborted || generation !== scriptGeneration) return "cancelled";
      const stillTarget =
        latest.currentProjectName === projectName &&
        latest.activeEpisodeScript.projectName === projectName &&
        latest.activeEpisodeScript.episode === episode &&
        latest.activeEpisodeScript.scriptFile === scriptFile &&
        latest.activeEpisodeScript.generation === generation;
      if (!stillTarget) return "cancelled";

      if (latest.currentProjectData) {
        const epMeta = latest.currentProjectData.episodes.find((entry) => entry.episode === episode);
        const bound = normalizeScriptFile(epMeta?.script_file);
        if (!epMeta || !episodeHasFormalScript(epMeta) || bound !== scriptFile) {
          return "cancelled";
        }
      }

      set({
        currentScripts: { [scriptFile]: snapshot.script },
        activeEpisodeScript: {
          projectName,
          episode,
          scriptFile,
          status: "ready",
          revision: snapshot.revision,
          error: null,
          generation,
        },
      });
      return "success";
    } catch (err) {
      if (signal.aborted || generation !== scriptGeneration) return "cancelled";
      const latest = get();
      if (
        latest.currentProjectName !== projectName ||
        latest.activeEpisodeScript.generation !== generation
      ) {
        return "cancelled";
      }
      if (isNotFoundError(err)) {
        set({
          currentScripts: {},
          activeEpisodeScript: {
            projectName,
            episode,
            scriptFile,
            status: "missing",
            revision: null,
            error: null,
            generation,
          },
        });
        return "success";
      }
      const message = err instanceof Error ? err.message : String(err);
      set((s) => ({
        activeEpisodeScript: {
          ...s.activeEpisodeScript,
          status: "error",
          error: message,
        },
      }));
      if (!options.speculative) {
        options.onError?.(err);
      }
      return "failed";
    }
  };

  const reconcileActiveEpisodeAfterWorkspace = async (
    projectName: string,
    project: ProjectData,
    wasInitialLoad: boolean,
    scope: ProjectRefreshScope | undefined,
    reloadActiveScript: boolean | undefined,
  ): Promise<void> => {
    const active = get().activeEpisodeScript;
    if (active.projectName !== projectName || active.episode === null) return;

    const epMeta = project.episodes.find((entry) => entry.episode === active.episode);
    const boundFile = normalizeScriptFile(epMeta?.script_file);
    const hasFormal = episodeHasFormalScript(epMeta);

    if (!hasFormal || !boundFile) {
      rotateScriptScope();
      set({
        currentScripts: {},
        activeEpisodeScript: {
          projectName,
          episode: active.episode,
          scriptFile: boundFile,
          status: "missing",
          revision: null,
          error: null,
          generation: scriptGeneration,
        },
      });
      return;
    }

    if (boundFile !== active.scriptFile) {
      await loadEpisodeScriptInternal(projectName, active.episode, boundFile, { force: true });
      return;
    }

    if (wasInitialLoad && (active.status === "loading" || active.status === "ready")) {
      return;
    }

    const shouldReload =
      reloadActiveScript !== undefined
        ? reloadActiveScript
        : scopeTouchesEpisode(scope, active.episode);
    if (shouldReload) {
      await loadEpisodeScriptInternal(projectName, active.episode, boundFile, { force: true });
    }
  };

  const commitCurrentProject = (
    name: string | null,
    data: ProjectData | null,
    scripts: Record<string, EpisodeScript> | undefined,
    fingerprints: Record<string, number> | undefined,
    scope: ProjectRefreshScope | undefined,
    rotateOnChange: boolean,
  ) => {
    const prevName = get().currentProjectName;
    const projectChanged = name !== prevName;
    if (projectChanged && rotateOnChange) {
      rotateProjectScope();
      rotateScriptScope();
      useItemLocationsStore.getState().reset(name);
    } else if (projectChanged) {
      if (get().activeEpisodeScript.projectName !== null && get().activeEpisodeScript.projectName !== name) {
        rotateScriptScope();
      }
      if (useItemLocationsStore.getState().projectName !== name) {
        useItemLocationsStore.getState().reset(name);
      }
    }
    set((s) => {
      const nextScripts =
        scripts !== undefined
          ? scripts
          : projectChanged || name === null || data === null
            ? {}
            : s.currentScripts;
      const nextActiveScript =
        projectChanged || name === null
          ? { ...IDLE_ACTIVE_SCRIPT, projectName: name, generation: scriptGeneration }
          : s.activeEpisodeScript;

      if (name === null || data === null) {
        return {
          currentProjectName: name,
          currentProjectData: data,
          currentScripts: nextScripts,
          activeEpisodeScript: nextActiveScript,
          assetFingerprints: fingerprints ?? s.assetFingerprints,
          hasLoadedAnyProject: s.hasLoadedAnyProject || name !== null,
        };
      }

      const isProjectWide = !scope || Boolean(scope.projectWide);
      const nextProjectSnapshotRevisions = {
        ...s.projectSnapshotRevisions,
        [name]: (s.projectSnapshotRevisions[name] ?? 0) + 1,
      };
      const nextProjectWideRevisions = isProjectWide
        ? {
            ...s.projectWideRevisions,
            [name]: (s.projectWideRevisions[name] ?? 0) + 1,
          }
        : s.projectWideRevisions;
      const nextAssetRevisions = scopeTouchesAssets(scope)
        ? {
            ...s.assetRevisions,
            [name]: (s.assetRevisions[name] ?? 0) + 1,
          }
        : s.assetRevisions;
      let nextEpisodeRevisions = s.episodeRevisions;
      if (scope?.episodes && scope.episodes.length > 0) {
        nextEpisodeRevisions = { ...s.episodeRevisions };
        for (const ep of scope.episodes) {
          const key = episodeScriptRevisionKey(name, ep);
          nextEpisodeRevisions[key] = (nextEpisodeRevisions[key] ?? 0) + 1;
        }
      }

      return {
        currentProjectName: name,
        currentProjectData: data,
        currentScripts: nextScripts,
        activeEpisodeScript: nextActiveScript,
        projectSnapshotRevisions: nextProjectSnapshotRevisions,
        projectWideRevisions: nextProjectWideRevisions,
        episodeRevisions: nextEpisodeRevisions,
        assetRevisions: nextAssetRevisions,
        assetFingerprints: fingerprints ?? s.assetFingerprints,
        hasLoadedAnyProject: s.hasLoadedAnyProject || name !== null,
      };
    });
  };

  const runRefresh = async (
    name: string,
    keys: string[],
    scope: ProjectRefreshScope | undefined,
    reloadActiveScript: boolean | undefined,
    onError: ((err: unknown) => void) | undefined,
    resolvers: Array<(result: RefreshProjectResult) => void>,
  ): Promise<void> => {
    let curName = name;
    let curKeys = keys;
    let curScope = scope;
    let curReloadActiveScript = reloadActiveScript;
    let curOnErrors = onError ? [onError] : [];
    let curResolvers = resolvers;
    let again = true;
    while (again) {
      again = false;
      let result: RefreshProjectResult = "cancelled";
      const signal = projectScope.signal;
      try {
        const res = await API.getWorkspace(curName, { signal });
        const { currentProjectName, currentProjectData, hasLoadedAnyProject } = get();
        const supersededByOtherProject =
          refreshQueued &&
          queuedName !== null &&
          queuedName !== curName &&
          (currentProjectName === null || queuedName === currentProjectName);
        const isCurrentProject =
          currentProjectName === curName || (currentProjectName === null && !hasLoadedAnyProject);
        if (!supersededByOtherProject && !signal.aborted && isCurrentProject) {
          const wasInitialLoad = currentProjectData === null;
          const merged = mergeWorkspaceProject(res);
          commitCurrentProject(curName, merged, undefined, res.asset_fingerprints, curScope, false);
          await reconcileActiveEpisodeAfterWorkspace(
            curName,
            merged,
            wasInitialLoad,
            curScope,
            curReloadActiveScript,
          );
          if (curKeys.length > 0 && !signal.aborted) {
            useAppStore.getState().invalidateEntities(curKeys);
          }
          result = signal.aborted ? "cancelled" : "success";
        }
      } catch (err) {
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
        curScope = queuedHasScope ? queuedScope : undefined;
        curReloadActiveScript = queuedReloadActiveScript;
        curOnErrors = queuedOnErrors;
        curResolvers = queuedResolvers;
        queuedName = null;
        queuedKeys = [];
        queuedScope = undefined;
        queuedHasScope = false;
        queuedReloadActiveScript = undefined;
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
    activeEpisodeScript: IDLE_ACTIVE_SCRIPT,
    projectDetailLoading: false,
    projectSnapshotRevisions: {},
    projectWideRevisions: {},
    episodeRevisions: {},
    assetRevisions: {},
    showCreateModal: false,
    creatingProject: false,
    assetFingerprints: {},
    hasLoadedAnyProject: false,

    setProjects: (projects) => set({ projects }),
    setProjectsLoading: (loading) => set({ projectsLoading: loading }),
    setCurrentProject: (name, data, scripts, fingerprints, scope) => {
      commitCurrentProject(name, data, scripts, fingerprints, scope, true);
    },
    setProjectDetailLoading: (loading) => set({ projectDetailLoading: loading }),
    setShowCreateModal: (show) => set({ showCreateModal: show }),
    setCreatingProject: (creating) => set({ creatingProject: creating }),
    updateAssetFingerprints: (fps) =>
      set((s) => ({ assetFingerprints: { ...s.assetFingerprints, ...fps } })),
    getAssetFingerprint: (path) => get().assetFingerprints[path] ?? null,

    loadEpisodeScript: loadEpisodeScriptInternal,
    clearActiveEpisodeScript: () => {
      const active = get().activeEpisodeScript;
      if (active.projectName && isDemoProject(active.projectName)) return;
      if (isDemoProject(get().currentProjectName)) return;
      rotateScriptScope();
      set((s) => ({
        currentScripts: {},
        activeEpisodeScript: {
          ...IDLE_ACTIVE_SCRIPT,
          projectName: s.currentProjectName,
          generation: scriptGeneration,
        },
      }));
    },

    refreshProject: (name, options) => {
      if (!name) return Promise.resolve("cancelled");
      if (isDemoProject(name)) return Promise.resolve("success");
      const invalidateKeys = options?.invalidateKeys ?? [];
      return new Promise<RefreshProjectResult>((resolve) => {
        if (refreshRunning) {
          if (refreshQueued && queuedName !== null && queuedName !== name) {
            const { currentProjectName } = get();
            if (currentProjectName !== null && queuedName === currentProjectName && name !== currentProjectName) {
              resolve("cancelled");
              return;
            }
            const supersededResolvers = queuedResolvers;
            queuedResolvers = [];
            queuedKeys = [];
            queuedScope = undefined;
            queuedHasScope = false;
            queuedReloadActiveScript = undefined;
            queuedOnErrors = [];
            supersededResolvers.forEach((r) => r("cancelled"));
          }
          refreshQueued = true;
          queuedName = name;
          queuedKeys = [...queuedKeys, ...invalidateKeys];
          queuedScope = queuedHasScope ? mergeScopes(queuedScope, options?.scope) : options?.scope;
          queuedHasScope = true;
          if (options?.reloadActiveScript === true) {
            queuedReloadActiveScript = true;
          } else if (queuedReloadActiveScript === undefined && options?.reloadActiveScript !== undefined) {
            queuedReloadActiveScript = options.reloadActiveScript;
          }
          if (options?.onError) {
            queuedOnErrors = [...queuedOnErrors, options.onError];
          }
          queuedResolvers = [...queuedResolvers, resolve];
          return;
        }
        refreshRunning = true;
        void runRefresh(
          name,
          invalidateKeys,
          options?.scope,
          options?.reloadActiveScript,
          options?.onError,
          [resolve],
        );
      });
    },
  };
});
