import { create } from "zustand";
import { API } from "@/api";
import type { NarrationDelivery, WorkflowPlan } from "@/types/workflow";

/**
 * 一次计划刷新的结算结果。`cancelled` 不是失败：目标项目/剧集在途中易主，
 * 迟到响应被丢弃，调用方静默处理即可。
 */
export type RefreshPlanResult = "success" | "failed" | "cancelled";

/** 计划的作用目标。同一目标的请求合并，目标易主则作废在途请求。 */
function planKey(projectName: string, episode: number | null): string {
  return `${projectName}::${episode ?? ""}`;
}

interface WorkflowState {
  /** 最近一次成功取回的计划。刷新失败不清空它。 */
  plan: WorkflowPlan | null;
  /** `plan` 所属的目标；与当前目标不符时界面不得据此陈述状态。 */
  planKey: string | null;
  loading: boolean;
  /** 最近一次刷新的错误文案；`plan` 仍保留上一次的结果。 */
  error: string | null;
  /**
   * 本次请求的旁白交付方式。后端把它标为 `persisted: false`——它不写回项目，
   * 只决定这一次求解按哪条路径评估，因此存在前端会话态里而不是项目数据里。
   */
  narrationDelivery: NarrationDelivery | null;
  /** 用户已确认的申请档位，按 unit 给；确认后重求解会带上。 */
  confirmedDurations: Record<string, number>;

  setNarrationDelivery: (delivery: NarrationDelivery | null) => void;
  confirmDurations: (durations: Record<string, number>) => void;
  /**
   * 取回目标的计划。
   *
   * - **在途合并**：同一目标同时只有一个请求在途，期间到达的请求合并为「结束后再跑一轮」。
   * - **失败留旧**：请求失败时保留上一次的 `plan`，只写 `error`。已经付费产出的结果
   *   不因为一次刷新失败而从界面上消失。
   * - **目标取消域**：切项目或切集时轮换取消域，旧目标的迟到响应写不回 store。
   */
  refreshPlan: (projectName: string, episode: number | null) => Promise<RefreshPlanResult>;
  /** 目标易主时清空计划：上一个目标的事实不能拿来陈述这一个目标。 */
  resetTarget: () => void;
}

export const useWorkflowStore = create<WorkflowState>((set, get) => {
  // 非响应式协调状态：不进 store state，避免订阅方为内部记账重渲染。
  let running = false;
  let queued = false;
  let queuedTarget: { projectName: string; episode: number | null } | null = null;
  let queuedResolvers: Array<(result: RefreshPlanResult) => void> = [];

  let scope = new AbortController();
  const rotateScope = () => {
    scope.abort();
    scope = new AbortController();
  };

  const runRefresh = async (
    projectName: string,
    episode: number | null,
    resolvers: Array<(result: RefreshPlanResult) => void>,
  ): Promise<void> => {
    let curProject = projectName;
    let curEpisode = episode;
    let curResolvers = resolvers;
    let again = true;
    // 绑定本轮迭代实际使用的取消域：`resetTarget` 轮换取消域时已经显式复位过
    // `running` 一次，若此实例结束时不核对自己仍持有当前取消域就无条件清标志，
    // 会把接管者（新目标下正在跑的实例）的在途标志顶掉，引发同目标并发请求、
    // 写回顺序倒挂。合并循环每轮重新绑定，让接手了排队目标的实例在自然完结时
    // 仍能正确清掉它实际持有的（可能是轮换后的）取消域。
    let ownScope = scope;
    while (again) {
      again = false;
      ownScope = scope;
      let result: RefreshPlanResult;
      const signal = ownScope.signal;
      const key = planKey(curProject, curEpisode);
      try {
        const plan = await API.getWorkflowPlan(
          curProject,
          {
            episode: curEpisode,
            narration_delivery: get().narrationDelivery,
            confirmed_request_durations: get().confirmedDurations,
          },
          { signal },
        );
        if (signal.aborted) {
          result = "cancelled";
        } else {
          set({ plan, planKey: key, error: null, loading: false });
          result = "success";
        }
      } catch (err) {
        if (signal.aborted) {
          result = "cancelled";
        } else {
          // 留旧：只写 error，不动 plan / planKey。
          set({ error: err instanceof Error ? err.message : String(err), loading: false });
          result = "failed";
        }
      }
      for (const resolve of curResolvers) resolve(result);
      if (queued && queuedTarget) {
        again = true;
        curProject = queuedTarget.projectName;
        curEpisode = queuedTarget.episode;
        curResolvers = queuedResolvers;
        queued = false;
        queuedTarget = null;
        queuedResolvers = [];
      }
    }
    // 只有仍持有最后一轮取消域的实例才清 running；被 `resetTarget` 作废的旧实例
    // 在这里读到的 `scope` 已经是新取消域，跳过——它不是新实例的接管者。
    if (scope === ownScope) running = false;
  };

  return {
    plan: null,
    planKey: null,
    loading: false,
    error: null,
    narrationDelivery: null,
    confirmedDurations: {},

    setNarrationDelivery: (delivery) => set({ narrationDelivery: delivery }),
    confirmDurations: (durations) =>
      set((s) => ({ confirmedDurations: { ...s.confirmedDurations, ...durations } })),

    resetTarget: () => {
      rotateScope();
      running = false;
      queued = false;
      queuedTarget = null;
      const resolvers = queuedResolvers;
      queuedResolvers = [];
      for (const resolve of resolvers) resolve("cancelled");
      set({
        plan: null,
        planKey: null,
        error: null,
        loading: false,
        narrationDelivery: null,
        confirmedDurations: {},
      });
    },

    refreshPlan: (projectName, episode) => {
      const key = planKey(projectName, episode);
      // 目标易主：作废旧目标的在途请求并清掉它的计划，新目标从空白开始求解。
      if (get().planKey !== null && get().planKey !== key) {
        get().resetTarget();
      }
      set({ loading: true });
      return new Promise<RefreshPlanResult>((resolve) => {
        if (running) {
          queued = true;
          queuedTarget = { projectName, episode };
          queuedResolvers.push(resolve);
          return;
        }
        running = true;
        void runRefresh(projectName, episode, [resolve]);
      });
    },
  };
});
