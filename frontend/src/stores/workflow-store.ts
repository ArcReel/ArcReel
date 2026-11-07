import { create } from "zustand";
import { API } from "@/api";
import type { WorkflowPlan } from "@/types/workflow";

/**
 * One plan-refresh settlement result. `cancelled` is not failure: target projects/episodes changed ownership midflight,
 * late responses were discarded and callers can handle silently.
 */
export type RefreshPlanResult = "success" | "failed" | "cancelled";

/** Plan targets. Merge same-target requests; ownership changes invalidate in-flight requests. Plan readers use this to check `planKey`. */
export function workflowPlanKey(projectName: string, episode: number | null): string {
  return `${projectName}::${episode ?? ""}`;
}

interface WorkflowState {
  /** Latest successful plans, retained on refresh failures. */
  plan: WorkflowPlan | null;
  /** Targets owning `plan`; UI must never describe mismatched current targets from these results. */
  planKey: string | null;
  loading: boolean;
  /** Latest refresh errors, with `plan` retaining previous results. */
  error: string | null;
  /** User-confirmed requested tiers per unit, included in subsequent solves. */
  confirmedDurations: Record<string, number>;

  confirmDurations: (durations: Record<string, number>) => void;
  /**
   * Fetch target plans.
   *
   * - **In-flight merging**: one request per target; overlapping calls merge into one follow-up round.
   * - **Retain on failure**: preserve previous `plan`, writing `error` only. Paid outputs
   *   must not disappear from UI after failed refreshes.
   * - **Target cancellation domains**: project/episode switches rotate domains, preventing old late writes.
   */
  refreshPlan: (projectName: string, episode: number | null) => Promise<RefreshPlanResult>;
  /** Clear plans when targets change; previous-target facts cannot describe new targets. */
  resetTarget: () => void;
}

export const useWorkflowStore = create<WorkflowState>((set, get) => {
  // Nonreactive coordination outside store state, avoiding subscriber rerenders for internal bookkeeping.
  let running = false;
  let queued = false;
  let queuedTarget: { projectName: string; episode: number | null } | null = null;
  let queuedResolvers: Array<(result: RefreshPlanResult) => void> = [];
  // Current request targets register at dispatch, unlike success-only `planKey`.
  // Detect target switches during first loads with null `planKey`:
  // comparing `planKey` alone sees null twice and misses switches, allowing old
  // late responses to write stores.
  let currentTarget: { projectName: string; episode: number | null } | null = null;

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
    // Bind each iteration's actual cancellation domain: `resetTarget` already reset
    // `running` when rotating. Unconditional cleanup without ownership checks
    // clears successors' in-flight markers, causing same-target concurrency/
    // inverted writes. Rebind every merge iteration so instances taking queued targets
    // correctly clear domains they actually own, including newly rotated ones.
    let ownScope = scope;
    while (again) {
      again = false;
      ownScope = scope;
      let result: RefreshPlanResult;
      const signal = ownScope.signal;
      const key = workflowPlanKey(curProject, curEpisode);
      try {
        const plan = await API.getWorkflowPlan(
          curProject,
          {
            episode_id: curEpisode,
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
          // Retain: write error only, preserving plan / planKey.
          set({ error: err instanceof Error ? err.message : String(err), loading: false });
          result = "failed";
        }
      }
      for (const resolve of curResolvers) resolve(result);
      // Old instances invalidated by `resetTarget` must not consume queues: global `queued`
      // without domain checks lets late predecessors steal requests owned by actual
      // successor instances. Rebinding current `scope` then issues second same-target
      // real requests alongside ongoing successor work, losing guaranteed write
      // ordering. Successors consume their own queues.
      if (scope !== ownScope) return;
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
    // Only instances still owning final-iteration domains clear running; `resetTarget`-invalidated predecessors
    // see new `scope` and skip, never taking ownership from newer instances.
    if (scope === ownScope) running = false;
  };

  return {
    plan: null,
    planKey: null,
    loading: false,
    error: null,
    confirmedDurations: {},

    confirmDurations: (durations) =>
      set((s) => ({ confirmedDurations: { ...s.confirmedDurations, ...durations } })),

    resetTarget: () => {
      rotateScope();
      running = false;
      queued = false;
      queuedTarget = null;
      currentTarget = null;
      const resolvers = queuedResolvers;
      queuedResolvers = [];
      for (const resolve of resolvers) resolve("cancelled");
      set({
        plan: null,
        planKey: null,
        error: null,
        loading: false,
        confirmedDurations: {},
      });
    },

    refreshPlan: (projectName, episode) => {
      // Detect ownership changes from dispatch-registered targets, never `planKey`, whose
      // updates follow success only. During first loads, null values on both reads
      // would bypass resetTarget for midload switches, letting old
      // responses persist.
      if (
        currentTarget !== null
        && (currentTarget.projectName !== projectName || currentTarget.episode !== episode)
      ) {
        get().resetTarget();
      }
      currentTarget = { projectName, episode };
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
