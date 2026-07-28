import { create } from "zustand";
import { API } from "@/api";

interface OnboardingState {
  /** 后端「已看过」标记；null = 尚未查询 */
  seen: boolean | null;
  /**
   * `seen: true` 是否已被后端确认过(GET 返回过 true，或 POST 成功过)。
   * 查询失败时也会把 `seen` 置真用于本次抑制自动弹出，但那不算确认——
   * 靠这个字段把两种「true」的含义分开，避免 `exit()` 把前者误判成不必再写。
   */
  seenConfirmed: boolean;
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
  seenConfirmed: false,
  active: false,

  loadStatus: async (options = {}) => {
    try {
      const { seen } = await API.getOnboardingStatus({ signal: options.signal });
      if (options.signal?.aborted) return;
      // 迟到的查询结果不得回退已确立的「已看过」——用户可能在这次查询飞行途中
      // 已经跑完并退出过一轮引导(本地 exit() 早已写入 seen: true)。
      if (get().seen === true) return;
      set({ seen, seenConfirmed: seen });
    } catch (err) {
      if (options.signal?.aborted) return;
      // 后端抖动时不该给老用户重复弹引导。仅抑制本次会话，刷新后重新判定；
      // 不算后端确认，后续 exit() 仍要真正写一次。
      console.warn("[onboarding] status fetch failed; suppressing auto-start", err);
      set({ seen: true });
    }
  },

  start: () => {
    if (get().active) return;
    set({ active: true });
  },

  exit: () => {
    const { active, seenConfirmed } = get();
    if (!active) return;
    set({ active: false, seen: true });
    // 后端已确认过「已看过」时再写一次没有意义 —— 引导全程只允许这一个写操作，
    // 能省则省。查询失败导致的本地临时抑制不算确认，仍要写。
    if (seenConfirmed) return;
    void API.markOnboardingSeen()
      .then(() => set({ seenConfirmed: true }))
      .catch((err) => {
        console.warn("[onboarding] failed to mark tour as seen", err);
      });
  },
}));
