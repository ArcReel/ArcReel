import { create } from "zustand";
import { API } from "@/api";
import type { CostEstimateResponse, SegmentCost, EpisodeCost, CostByType } from "@/types";

interface CostState {
  costData: CostEstimateResponse | null;
  loading: boolean;
  error: string | null;

  /** Internal indexes — rebuilt on each fetchCost success */
  _segmentIndex: Map<string, SegmentCost>;
  _episodeIndex: Map<number, EpisodeCost>;
  _debounceTimer: ReturnType<typeof setTimeout> | null;

  fetchCost: (projectName: string) => Promise<void>;
  debouncedFetch: (projectName: string) => void;
  clear: () => void;

  getEpisodeCost: (episode: number) => EpisodeCost | undefined;
  getSegmentCost: (segmentId: string) => SegmentCost | undefined;
  getProjectTotals: () => { estimate: CostByType; actual: CostByType } | undefined;
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

export const useCostStore = create<CostState>((set, get) => ({
  costData: null,
  loading: false,
  error: null,
  _segmentIndex: new Map(),
  _episodeIndex: new Map(),
  _debounceTimer: null,

  fetchCost: async (projectName: string) => {
    set({ loading: true, error: null });
    try {
      const data = await API.getCostEstimate(projectName);
      set({ costData: data, loading: false, ...buildIndexes(data) });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  debouncedFetch: (projectName: string) => {
    const prev = get()._debounceTimer;
    if (prev) clearTimeout(prev);
    const timer = setTimeout(() => {
      set({ _debounceTimer: null });
      void get().fetchCost(projectName);
    }, 500);
    set({ _debounceTimer: timer });
  },

  clear: () => {
    const prev = get()._debounceTimer;
    if (prev) clearTimeout(prev);
    set({
      costData: null,
      loading: false,
      error: null,
      _segmentIndex: new Map(),
      _episodeIndex: new Map(),
      _debounceTimer: null,
    });
  },

  getEpisodeCost: (episode: number) => {
    return get()._episodeIndex.get(episode);
  },

  getSegmentCost: (segmentId: string) => {
    return get()._segmentIndex.get(segmentId);
  },

  getProjectTotals: () => {
    const data = get().costData;
    if (!data) return undefined;
    return {
      estimate: data.project_totals.estimate,
      actual: data.project_totals.actual,
    };
  },
}));
