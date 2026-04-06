import { create } from "zustand";
import { API } from "@/api";
import type { CostEstimateResponse, SegmentCost, EpisodeCost, CostByType } from "@/types";

interface CostState {
  costData: CostEstimateResponse | null;
  loading: boolean;
  error: string | null;

  fetchCost: (projectName: string) => Promise<void>;
  clear: () => void;

  getEpisodeCost: (episode: number) => EpisodeCost | undefined;
  getSegmentCost: (segmentId: string) => SegmentCost | undefined;
  getProjectTotals: () => { estimate: CostByType; actual: CostByType } | undefined;
}

export const useCostStore = create<CostState>((set, get) => ({
  costData: null,
  loading: false,
  error: null,

  fetchCost: async (projectName: string) => {
    set({ loading: true, error: null });
    try {
      const data = await API.getCostEstimate(projectName);
      set({ costData: data, loading: false });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  clear: () => set({ costData: null, loading: false, error: null }),

  getEpisodeCost: (episode: number) => {
    return get().costData?.episodes.find((e) => e.episode === episode);
  },

  getSegmentCost: (segmentId: string) => {
    const data = get().costData;
    if (!data) return undefined;
    for (const ep of data.episodes) {
      const seg = ep.segments.find((s) => s.segment_id === segmentId);
      if (seg) return seg;
    }
    return undefined;
  },

  getProjectTotals: () => {
    return get().costData
      ? {
          estimate: get().costData!.project_totals.estimate,
          actual: get().costData!.project_totals.actual,
        }
      : undefined;
  },
}));
