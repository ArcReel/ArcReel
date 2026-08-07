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

/** 用户是否已切到别的项目——成功/失败两条路径共用同一份判断，避免各自维护出偏差。 */
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
    // 使切入前尚在途的请求作废：无论接下来走哪条分支，这次调用都取代它
    _abortController?.abort();
    // 引导演示项目在后端不存在，费用估算无从计算，界面按「未估算」显示
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
      // 请求期间又发起了新请求，这份响应已作废：loading 收尾交给取代它的那次调用
      if (controller.signal.aborted) return;
      // 用户已切到别的项目：不能用这份数据覆盖当前项目的显示（单例 costData 不分项目
      // 存储，两个项目的 segment_id 还可能撞名），但 loading 要收尾——不会再有同 key
      // 请求替它复位，放着不收会一直卡在加载态
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
    // 演示项目立即清空，不等 500ms 防抖窗口——否则切入演示项目后的这段窗口期，
    // UI 仍会读到上一个真实项目残留在 store 里的费用数据。这一支不判过期：演示项目
    // 本身就是当前项目，不存在「切走了还调它」的场景。
    if (isDemoProject(projectName)) {
      if (_debounceTimer) clearTimeout(_debounceTimer);
      _debounceTimer = null;
      _abortController?.abort();
      _abortController = null;
      get().clear();
      return;
    }
    // 已不是当前项目的调用不动共享的防抖计时器：debouncedFetch(A) 若在用户已切到
    // B 之后才落地（如某个慢请求的 .then 回调），清掉/顶替计时器会连带取消 B 刚排的、
    // 真正需要跑的那次刷新——响应落地时的过期检查只能丢弃 A 自己的结果，救不回被它
    // 顶掉的 B。
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
