import { create } from "zustand";

/**
 * 故事设定提炼完成后的一次性就地提示。
 *
 * 待显示的项目记在内存里：概览因切换视图卸载再回来时提示还在，刷新页面后不再出现。
 * 点「知道了」或向 Agent 发出消息后按项目记入 localStorage，之后这个项目不再提示。
 */

const DISMISSED_KEY_PREFIX = "arcreel_handoff_tip_dismissed:";

function readDismissed(projectName: string): boolean {
  try {
    return localStorage.getItem(DISMISSED_KEY_PREFIX + projectName) === "1";
  } catch {
    return false;
  }
}

interface HandoffTipState {
  /** 本次会话内等待显示提示的项目。 */
  pending: ReadonlySet<string>;
  /** 故事设定由空变为有内容：项目此前没有关闭过提示时显示。 */
  trigger: (projectName: string) => void;
  /** 关闭提示并按项目记住。 */
  dismiss: (projectName: string) => void;
}

export const useHandoffTipStore = create<HandoffTipState>((set) => ({
  pending: new Set(),
  trigger: (projectName) => {
    if (readDismissed(projectName)) return;
    set((state) => ({ pending: new Set(state.pending).add(projectName) }));
  },
  dismiss: (projectName) => {
    try {
      localStorage.setItem(DISMISSED_KEY_PREFIX + projectName, "1");
    } catch {
      // localStorage 不可用时只在本次会话内关闭
    }
    set((state) => {
      const pending = new Set(state.pending);
      pending.delete(projectName);
      return { pending };
    });
  },
}));
