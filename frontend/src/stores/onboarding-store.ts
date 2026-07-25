import { create } from "zustand";
import { API } from "@/api";

interface OnboardingState {
  /** 后端「已看过」标记；null = 尚未查询 */
  seen: boolean | null;
  /** 引导是否正在运行 */
  active: boolean;
  /** 拉取「是否已看过」。失败时按已看过处置，宁可不弹也不误弹。 */
  loadStatus: (options?: { signal?: AbortSignal }) => Promise<void>;
  /** 打开引导。自动首弹与「重看引导」共用这一条路径。 */
  start: () => void;
  /** 任一退出路径（跳过 / 关闭 / 走完）。首次退出时标记已看；重看不重复写。 */
  exit: () => void;
}

export const useOnboardingStore = create<OnboardingState>((set, get) => ({
  seen: null,
  active: false,

  loadStatus: async (options = {}) => {
    try {
      const { seen } = await API.getOnboardingStatus({ signal: options.signal });
      if (options.signal?.aborted) return;
      set({ seen });
    } catch (err) {
      if (options.signal?.aborted) return;
      // 后端抖动时不该给老用户重复弹引导。仅抑制本次会话，刷新后重新判定。
      console.warn("[onboarding] status fetch failed; suppressing auto-start", err);
      set({ seen: true });
    }
  },

  start: () => {
    if (get().active) return;
    set({ active: true });
  },

  exit: () => {
    const { active, seen } = get();
    if (!active) return;
    set({ active: false, seen: true });
    // 重看路径上 flag 已是「已看过」，再写一次没有意义 —— 引导全程只允许这一个写操作，
    // 能省则省。
    if (seen === true) return;
    void API.markOnboardingSeen().catch((err) => {
      console.warn("[onboarding] failed to mark tour as seen", err);
    });
  },
}));
