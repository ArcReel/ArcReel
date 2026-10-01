import { create } from "zustand";

/**
 * 重新规划会替换掉的现有内容：
 * - `none`：本集还没有脚本规划；
 * - `formal_script`：没有脚本规划，但已有正式脚本（如从空白开始），新规划待确认，确认时再走正式脚本的覆盖确认；
 * - `confirmed_plan`：规划已确认，新规划待确认，确认时再走正式脚本的覆盖确认；
 * - `pending_plan`：未确认的规划，整份替换，内容确认页上的修改会丢失；
 * - `draft`：待修复草稿，整份替换，草稿上的修改会丢失。
 */
export type ScriptPlanReplacement = "none" | "formal_script" | "confirmed_plan" | "pending_plan" | "draft";

export interface ScriptPlanOpenRequest {
  projectName: string;
  episode: number;
  replaces: ScriptPlanReplacement;
}

interface ScriptPlanState {
  request: ScriptPlanOpenRequest | null;
  open: (request: ScriptPlanOpenRequest) => void;
  close: () => void;
}

/**
 * 「AI 规划脚本」弹窗的开关：集页、内容确认页与制作状态的下一步只登记要打开的集和会替换的内容，
 * 弹窗由集页上唯一的宿主渲染。
 */
export const useScriptPlanStore = create<ScriptPlanState>((set) => ({
  request: null,
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));
