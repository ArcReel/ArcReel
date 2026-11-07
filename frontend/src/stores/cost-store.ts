import { create } from "zustand";
import { API } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import type { CostEstimateResponse, SegmentCost, EpisodeCost } from "@/types";

interface CostState {
  costData: CostEstimateResponse | null;
  loading: boolean;
  error: string | null;

  /** Internal indexes — rebuilt on each fetchCost success */
  _segmentIndex: Map<string, SegmentCost>;
  _episodeIndex: Map<number, EpisodeCost>;

  fetchCost: (projectName: string) => Promise<void>;
  debouncedFetch: (projectName: string) => void;
  clear: () => void;

  getEpisodeCost: (episode: number) => EpisodeCost | undefined;
  getSegmentCost: (segmentId: string) => SegmentCost | undefined;
}

function buildIndexes(data: CostEstimateResponse): {
  _segmentIndex: Map<string, SegmentCost>;
  _episodeIndex: Map<number, EpisodeCost>;
} {
  const segmentIndex = new Map<string, SegmentCost>();
  const episodeIndex = new Map<number, EpisodeCost>();
  for (const ep of data.episodes) {
    episodeIndex.set(ep.episode, ep);
    for (const seg of ep.segments) {
      segmentIndex.set(seg.segment_id, seg);
    }
  }
  return { _segmentIndex: segmentIndex, _episodeIndex: episodeIndex };
}

let _debounceTimer: ReturnType<typeof setTimeout> | null = null;
let _abortController: AbortController | null = null;

/** Whether users switched projects, shared by success/failure paths to prevent divergent predicates. */
function isStaleProject(projectName: string): boolean {
  return useProjectsStore.getState().currentProjectName !== projectName;
}

export const useCostStore = create<CostState>((set, get) => ({
  costData: null,
  loading: false,
  error: null,
  _segmentIndex: new Map(),
  _episodeIndex: new Map(),

  fetchCost: async (projectName: string) => {
    // Stale calls must not abort current-project requests: debouncedFetch timers never recheck staleness on firing
    // (only once at scheduling), and public methods may be called directly. By late execution, projects
    // may have switched; unconditional abort would wrongly interrupt unrelated, genuinely necessary
    // current-project requests. Return directly without touching loading, which this call neither started nor owns cleanup for.
    if (isStaleProject(projectName)) return;
    // Invalidate requests still in flight before entry: this call replaces them regardless of following branches.
    _abortController?.abort();
    // Onboarding demos do not exist in backends; costs cannot estimate, so display unestimated.
    if (isDemoProject(projectName)) {
      _abortController = null;
      get().clear();
      return;
    }
    const controller = new AbortController();
    _abortController = controller;
    set({ loading: true, error: null });
    try {
      const data = await API.getCostEstimate(projectName, { signal: controller.signal });
      // New requests invalidated this response; successor calls own loading cleanup.
      if (controller.signal.aborted) return;
      // Users switched projects: never overwrite current displays (singleton costData is not project-partitioned,
      // and segment_id may collide), but settle loading: no same-key successor will
      // reset it, otherwise loading sticks forever.
      if (isStaleProject(projectName)) {
        set({ loading: false });
        return;
      }
      set({ costData: data, loading: false, ...buildIndexes(data) });
    } catch (err) {
      if (controller.signal.aborted) return;
      if (isStaleProject(projectName)) {
        set({ loading: false });
        return;
      }
      set({ error: errMsg(err), loading: false });
    }
  },

  debouncedFetch: (projectName: string) => {
    // Clear demos immediately, before 500ms debounce; otherwise UI reads old real-project cost data
    // remaining in stores throughout that window after demo entry. No staleness check here: demos
    // are already current, with no departed-project calls.
    if (isDemoProject(projectName)) {
      if (_debounceTimer) clearTimeout(_debounceTimer);
      _debounceTimer = null;
      _abortController?.abort();
      _abortController = null;
      get().clear();
      return;
    }
    // Departed-project calls never touch shared debounce timers: debouncedFetch(A) arriving after users switch
    // to B (for example slow-request .then callbacks) must not clear/replace B's newly scheduled,
    // necessary refresh. Response-time staleness checks discard A results only, never restore B timers
    // it replaced.
    //
    // No follow-up fetch after discarding here relies on guaranteed currentProjectName-dependent calls
    // after project switches: AssetSidebar always mounts in workbench layouts, and refresh effects depend on
    // currentProjectName, necessarily refetching on project-name persistence. Canvas effects depend on
    // route projectName and run before route-level currentProjectName writes (React child effects precede
    // parent effects); those calls are necessarily discarded here. New cost-refresh entries must preserve this chain:
    // depend on currentProjectName or guarantee later effect reruns.
    if (isStaleProject(projectName)) return;
    if (_debounceTimer) clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(() => {
      _debounceTimer = null;
      void get().fetchCost(projectName);
    }, 500);
  },

  clear: () => {
    if (_debounceTimer) clearTimeout(_debounceTimer);
    _debounceTimer = null;
    _abortController?.abort();
    _abortController = null;
    set({
      costData: null,
      loading: false,
      error: null,
      _segmentIndex: new Map(),
      _episodeIndex: new Map(),
    });
  },

  getEpisodeCost: (episode: number) => {
    return get()._episodeIndex.get(episode);
  },

  getSegmentCost: (segmentId: string) => {
    return get()._segmentIndex.get(segmentId);
  },
}));
