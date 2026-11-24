import { create } from "zustand";
import { API } from "@/api";
import type { WorkflowPlan } from "@/types/workflow";

export type RefreshPlanResult = "success" | "failed" | "cancelled";

export function workflowPlanKey(projectName: string, episode: number | null): string {
  return `${projectName}::${episode ?? ""}`;
}

interface WorkflowState {
  plan: WorkflowPlan | null;
  planKey: string | null;
  loading: boolean;
  error: string | null;
  confirmedDurations: Record<string, number>;

  confirmDurations: (durations: Record<string, number>) => void;
  refreshPlan: (projectName: string, episode: number | null) => Promise<RefreshPlanResult>;
  resetTarget: () => void;
}

export const useWorkflowStore = create<WorkflowState>((set, get) => {
  let running = false;
  let queued = false;
  let queuedTarget: { projectName: string; episode: number | null } | null = null;
  let queuedResolvers: Array<(result: RefreshPlanResult) => void> = [];
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
          set({ error: err instanceof Error ? err.message : String(err), loading: false });
          result = "failed";
        }
      }
      for (const resolve of curResolvers) resolve(result);
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
