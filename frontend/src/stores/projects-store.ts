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
  let refreshRunning = false;
  let refreshQueued = false;
  let queuedName: string | null = null;
  let queuedKeys: string[] = [];
  let queuedOnErrors: Array<(err: unknown) => void> = [];
  // 排队轮调用方各自的 resolve——与 curResolvers 分开累积，避免"排队进同一批"
  // 被误解为"共享同一个最终结果"：每个调用方只对自己实际服务的那一轮结果负责。
  let queuedResolvers: Array<(ok: boolean) => void> = [];

  // 执行刷新循环：while 排队重跑替代递归，失败路径也消费排队请求，直至无新排队为止。
  // 每轮结束立刻 resolve 该轮的调用方（curResolvers），不等后续排队轮跑完——否则先到
  // 的调用方会被后到、且与自己无关的轮次结果覆盖返回值（如已成功写入的重排刷新，被
  // 随后排队、失败的 SSE 刷新拖累成 false，UI 无法据此推进选中态）。
  const runRefresh = async (
    name: string,
    keys: string[],
    onError: ((err: unknown) => void) | undefined,
    resolvers: Array<(ok: boolean) => void>,
  ): Promise<void> => {
    let curName = name;
    let curKeys = keys;
    let curOnErrors = onError ? [onError] : [];
    let curResolvers = resolvers;
    let again = true;
    while (again) {
      again = false;
      let ok = false;
      try {
        const res = await API.getProject(curName);
        // 在途期间若已排队到不同项目的刷新请求，本轮响应针对的是即将切走的旧项目——
        // 跳过写入，避免用它覆盖排队轮即将加载的新项目（数据 / 名称均不提交）。
        const supersededByOtherProject = refreshQueued && queuedName !== null && queuedName !== curName;
        if (!supersededByOtherProject) {
          get().setCurrentProject(curName, res.project, res.scripts ?? {}, res.asset_fingerprints);
          if (curKeys.length > 0) {
            useAppStore.getState().invalidateEntities(curKeys);
          }
          ok = true;
        }
      } catch (err) {
        // 失败留旧：不覆盖 currentProjectData；调用方按返回值 / onError 决定提示。
        ok = false;
        curOnErrors.forEach((cb) => cb(err));
      }
      curResolvers.forEach((resolve) => resolve(ok));
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
      return new Promise<boolean>((resolve) => {
        if (refreshRunning) {
          // 已有刷新在途：合并为「结束后再跑一轮」，取最新 name，累积 invalidateKeys /
          // onError / resolve——排队轮次结束时需各自通知全部合并进来的调用方，
          // 而非共享首轮或末轮的单一结果。
          //
          // 排队期间到达的请求若指向和当前排队目标不同的项目，说明上一批排队请求
          // 即将被本次覆盖——设计上只保留「结束后再跑一轮」这一个名额，取最新
          // name，不会再为被覆盖的项目单独多跑一轮。因此上一批排队的调用方注定不会
          // 被接下来实际执行的那一轮命中；不提前结算就会被并入并共享本次这个新项目
          // 的结果，即便它们请求的从来不是这个项目（例如 B 排队后 C 覆盖，B 的调用方
          // 会在 C 成功后错误地收到 true，但 store 从未同步过 B 的数据）。按「未同步」
          // 立即结算为 false，不视为请求出错，因此不触发 onError。
          if (refreshQueued && queuedName !== null && queuedName !== name) {
            const supersededResolvers = queuedResolvers;
            queuedResolvers = [];
            queuedKeys = [];
            queuedOnErrors = [];
            supersededResolvers.forEach((r) => r(false));
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
