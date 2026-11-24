import { create } from "zustand";

export type ScriptPlanReplacement = "formal_script" | "confirmed_plan" | "pending_plan" | "draft";

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

export const useScriptPlanStore = create<ScriptPlanState>((set) => ({
  request: null,
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));
