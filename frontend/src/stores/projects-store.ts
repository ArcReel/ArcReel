import { create } from "zustand";
import type { ProjectData, ProjectSummary, EpisodeScript } from "@/types";
import { API } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import { useAppStore } from "./app-store";

export type RefreshProjectResult = "success" | "failed" | "cancelled";

interface RefreshProjectOptions {
  /** 刷新成功后要失效的实体版本 key（沿用 StudioCanvasRouter 旧变体语义）。 */
  invalidateKeys?: string[];
  /** 每次 getProject 失败时回调（附错误对象），供调用方按需提示；不影响留旧语义。 */
  onError?: (err: unknown) => void;
}

interface ProjectsState {
  projects: ProjectSummary[];
  projectsLoading: boolean;

  currentProjectName: string | null;
  currentProjectData: ProjectData | null;
  currentScripts: Record<string, EpisodeScript>;
  projectDetailLoading: boolean;

  // project.json + 剧本规范快照按项目递增的修订号。
  projectSnapshotRevisions: Record<string, number>;

  showCreateModal: boolean;
  creatingProject: boolean;

  // Asset fingerprints (path → mtime_ns)
  assetFingerprints: Record<string, number>;

  /**
   * 内部记账：store 生命周期内是否已经建立过至少一个真实项目。仅供
   * {@link refreshProject} 判断 `currentProjectName` 为 `null` 时是「store 从未加载
   * 过任何项目」还是「路由切换途中的过渡态」，不供 UI 消费。
   */
  hasLoadedAnyProject: boolean;

  setProjects: (projects: ProjectSummary[]) => void;
  setProjectsLoading: (loading: boolean) => void;
  /**
   * 写入当前项目。名称易主时同时轮换项目级取消域：前一个项目挂在旧域上的在途请求当场
   * 取消，其迟到响应写不回 store（见 refreshProject 的「项目级取消域」）。这不是纯 setter，
   * 全部切换路径（路由 effect 及其 cleanup、演示工作台接管）经此收口。
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
  refreshProject: (name: string, options?: RefreshProjectOptions) => Promise<RefreshProjectResult>;
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
  let queuedResolvers: Array<(result: RefreshProjectResult) => void> = [];

  // 项目级取消域：当前项目的数据请求都挂在这个 controller 上。切换项目时轮换（abort 旧的、
  // 换上新的），使前一个项目的在途请求当场取消、迟到响应无法写回 store。轮换后现役
  // controller 恒为未 abort 状态，因此不会长期作废后续刷新。
  // 刷新自己写入成功时（名称由 null 变为该项目）也会走一次轮换，abort 的是本轮刚用完的域：
  // 后续每一轮都重新取现役域，故无影响；日后若有别的项目级请求挂上来，需要复核这一点。
  let projectScope = new AbortController();
  const rotateProjectScope = () => {
    projectScope.abort();
    projectScope = new AbortController();
  };

  // 执行刷新循环：while 排队重跑替代递归，失败路径也消费排队请求，直至无新排队为止。
  // 每轮结束立刻 resolve 该轮的调用方（curResolvers），不等后续排队轮跑完——否则先到
  // 的调用方会被后到、且与自己无关的轮次结果覆盖返回值（如已成功写入的重排刷新，被
  // 随后排队、失败的 SSE 刷新拖累成 false，UI 无法据此推进选中态）。
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
      // 每轮取一次现役取消域：排队轮要挂在它自己发起时刻的域上，不复用上一轮的。
      const signal = projectScope.signal;
      try {
        const res = await API.getProject(curName, { signal });
        // currentProjectName 为 null 时，只有 store 整个生命周期内还从未建立过任何真实
        // 项目才放行——那是 refreshProject 承担的「把首个项目数据建立进 store」职责，
        // 届时没有「当前项目」可比较。一旦建立过任意项目，后续任何 null 都只会是路由
        // cleanup 清空旧项目、异步加载新项目之间的过渡态：这段窗口里不放行任何名字的
        // 写入（包括比刚清空的项目更早、直到现在才落定的旧项目，见 hasLoadedAnyProject
        // 的注释），否则旧数据会在新项目自己的数据落地前抢先写回。
        const { currentProjectName, hasLoadedAnyProject } = get();
        // 在途期间若已排队到「更有资格写入」的另一个项目，本轮响应针对的就是即将被取代
        // 的旧项目——跳过写入，避免覆盖排队轮即将加载的新项目（数据 / 名称均不提交）。
        // 「更有资格」有两种：排队的那个项目已经是当前项目（路由切换先落名再发起刷新，
        // 切换此刻已经完成），或 store 还没有当前项目（尚未建立任何项目，排队里的最新
        // 意图就是目标）。反过来，排队者若是持切走前旧项目名的调用方（写操作完成后的
        // 刷新、SSE），它注定过不了下面的当前项目核对、一个字节也写不进 store，本轮
        // 不能为它让位——本轮才是当前项目自己的数据，丢掉后没有任何人会再加载它。
        const supersededByOtherProject =
          refreshQueued &&
          queuedName !== null &&
          queuedName !== curName &&
          (currentProjectName === null || queuedName === currentProjectName);
        // 取消域已轮换：当前项目在请求在途期间被换掉了（路由切到别的项目、演示工作台接管、
        // 离开工作台清空）。这些路径都不经过 refreshProject，排队去重看不到它们，只能靠
        // 取消域识别。abort 与响应落定同为异步，响应先到、abort 后到时网络层不会 reject，
        // 因此写入前还要复核一次 aborted。
        // 上述两项都只能拦住「请求发起时项目已切走」的情形：若调用方（如写操作完成后
        // 的刷新）捕获的是切走前的旧项目名，且这次调用是在切换已经完成之后才发起的，
        // 拿到的会是新项目现役、未 abort 的域——因此还要在写入前核对 curName 是否仍是
        // 当前项目，防止旧项目的数据覆盖已经切入的新项目。
        const isCurrentProject =
          currentProjectName === curName || (currentProjectName === null && !hasLoadedAnyProject);
        if (!supersededByOtherProject && !signal.aborted && isCurrentProject) {
          get().setCurrentProject(curName, res.project, res.scripts ?? {}, res.asset_fingerprints);
          if (curKeys.length > 0) {
            useAppStore.getState().invalidateEntities(curKeys);
          }
          result = "success";
        }
      } catch (err) {
        // 取消域轮换导致的 abort 是项目切换的正常结果，不是刷新失败：结算为
        // "cancelled"，不触发 onError，否则每次切项目都会弹一条同步失败提示。
        // 失败留旧：两种结果都不覆盖 currentProjectData。
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
      // 当前项目易主即轮换取消域。这是全部切换路径（路由 effect 及其 cleanup、演示工作台
      // 接管）的必经之处，因此不必让各调用方各自传播取消——它们保持原样即受此保护。
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
      // 引导演示项目的数据来自前端常量，没有可刷新的服务端状态
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
