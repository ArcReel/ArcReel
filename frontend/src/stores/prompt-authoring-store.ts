import { create } from "zustand";

/** 「编写提示词」弹窗的范围：当前单条、全部待编写、自选多条。 */
export type PromptAuthoringScope = "current" | "pending" | "custom";

export interface PromptAuthoringOpenRequest {
  projectName: string;
  episode: number;
  scope: PromptAuthoringScope;
  /** 页面上当前选中的条目；给了才提供「当前单条」。 */
  currentEntryId?: string | null;
}

interface PromptAuthoringState {
  request: PromptAuthoringOpenRequest | null;
  open: (request: PromptAuthoringOpenRequest) => void;
  close: () => void;
}

/**
 * 「编写提示词」弹窗的开关：分镜详情、时间线工具栏、参考生视频画布与制作状态的下一步
 * 都只登记要打开的范围，弹窗由集页上唯一的宿主渲染。
 */
export const usePromptAuthoringStore = create<PromptAuthoringState>((set) => ({
  request: null,
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));
