import { create } from "zustand";

export interface AdScriptOpenRequest {
  projectName: string;
  episode: number;
  /** 整份重做：替换已有的正式脚本，提交前先确认丢失清单。 */
  regenerate: boolean;
}

interface AdScriptState {
  request: AdScriptOpenRequest | null;
  open: (request: AdScriptOpenRequest) => void;
  close: () => void;
}

/**
 * 广告/短片「AI 生成脚本」弹窗的开关：空状态、工具栏与制作状态只登记要打开的集和是否整份重做，
 * 弹窗由集页上唯一的宿主渲染。
 */
export const useAdScriptStore = create<AdScriptState>((set) => ({
  request: null,
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));
