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
    if (isStaleProject(projectName)) return;
    _abortController?.abort();
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
      if (controller.signal.aborted) return;
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
    if (isDemoProject(projectName)) {
      if (_debounceTimer) clearTimeout(_debounceTimer);
      _debounceTimer = null;
      _abortController?.abort();
      _abortController = null;
      get().clear();
      return;
    }
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
