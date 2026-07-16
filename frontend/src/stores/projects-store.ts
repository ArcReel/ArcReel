import { create } from "zustand";
import type { ProjectData, ProjectSummary, EpisodeScript } from "@/types";
import { API } from "@/api";
import { useAppStore } from "./app-store";

/** {@link ProjectsState.refreshProject} 的可选行为。 */
interface RefreshProjectOptions {
  /** 刷新成功后要失效的实体版本 key（沿用 StudioCanvasRouter 旧变体语义）。 */
  invalidateKeys?: string[];
  /** 每次 getProject 失败时回调（附错误对象），供调用方按需提示；不影响留旧语义。 */
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

  // Create modal
  showCreateModal: boolean;
  creatingProject: boolean;

  // Asset fingerprints (path → mtime_ns)
  assetFingerprints: Record<string, number>;

  // Actions
  setProjects: (projects: ProjectSummary[]) => void;
  setProjectsLoading: (loading: boolean) => void;
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
   * 刷新当前项目数据到 store，返回 store 是否已同步成功。
   *
   * 单一入口收敛此前各调用点分散的刷新语义，消除「两入口同时刷新时后完成者盖住
   * 先完成者」的竞态：
   * - **在途合并**：同一时刻只允许一个 getProject 在途；在途期间到达的刷新请求
   *   合并为「结束后再跑一轮」，取最新一次请求的 name / invalidateKeys。
   * - **失败留旧**：getProject 失败时不覆盖 currentProjectData，返回 false 交调用方
   *   决定是否提示（onError 亦会被调用）。
   */
  refreshProject: (name: string, options?: RefreshProjectOptions) => Promise<boolean>;
}

export const useProjectsStore = create<ProjectsState>((set, get) => {
  // 刷新的在途合并协调状态（非响应式单例，不进 store state，避免触发订阅重渲染）。
  let refreshInFlight: Promise<boolean> | null = null;
  let refreshQueued = false;
  let queuedName: string | null = null;
  let queuedKeys: string[] = [];

  // 执行刷新循环：while 排队重跑替代递归，失败路径也消费排队请求，
  // 直至无新排队为止；返回最后一轮是否成功。
  const runRefresh = async (
    name: string,
    keys: string[],
    onError?: (err: unknown) => void,
  ): Promise<boolean> => {
    let curName = name;
    let curKeys = keys;
    let ok = false;
    let again = true;
    while (again) {
      again = false;
      try {
        const res = await API.getProject(curName);
        get().setCurrentProject(curName, res.project, res.scripts ?? {}, res.asset_fingerprints);
        if (curKeys.length > 0) {
          useAppStore.getState().invalidateEntities(curKeys);
        }
        ok = true;
      } catch (err) {
        // 失败留旧：不覆盖 currentProjectData；调用方按返回值 / onError 决定提示。
        ok = false;
        onError?.(err);
      }
      if (refreshQueued) {
        refreshQueued = false;
        curName = queuedName ?? curName;
        curKeys = queuedKeys;
        queuedName = null;
        queuedKeys = [];
        again = true;
      }
    }
    return ok;
  };

  return {
    projects: [],
    projectsLoading: false,
    currentProjectName: null,
    currentProjectData: null,
    currentScripts: {},
    projectDetailLoading: false,
    showCreateModal: false,
    creatingProject: false,
    assetFingerprints: {},

    setProjects: (projects) => set({ projects }),
    setProjectsLoading: (loading) => set({ projectsLoading: loading }),
    setCurrentProject: (name, data, scripts, fingerprints) =>
      set((s) => ({
        currentProjectName: name,
        currentProjectData: data,
        currentScripts: scripts ?? {},
        assetFingerprints: fingerprints ?? s.assetFingerprints,
      })),
    setProjectDetailLoading: (loading) => set({ projectDetailLoading: loading }),
    setShowCreateModal: (show) => set({ showCreateModal: show }),
    setCreatingProject: (creating) => set({ creatingProject: creating }),
    updateAssetFingerprints: (fps) =>
      set((s) => ({ assetFingerprints: { ...s.assetFingerprints, ...fps } })),
    getAssetFingerprint: (path) => get().assetFingerprints[path] ?? null,

    refreshProject: (name, options) => {
      if (!name) return Promise.resolve(false);
      const invalidateKeys = options?.invalidateKeys ?? [];
      if (refreshInFlight) {
        // 已有刷新在途：合并为「结束后再跑一轮」，取最新 name，累积 invalidateKeys。
        refreshQueued = true;
        queuedName = name;
        queuedKeys = [...queuedKeys, ...invalidateKeys];
        return refreshInFlight;
      }
      const p = runRefresh(name, invalidateKeys, options?.onError).finally(() => {
        refreshInFlight = null;
      });
      refreshInFlight = p;
      return p;
    },
  };
});
