import { create } from "zustand";

/**
 * Existing content replaced by replanning (initial planning without episode content uses episode startup sections, bypassing this):
 * - `formal_script`: no script plan but formal scripts exist (for example started blank); new plans await confirmation, then formal-script overwrite confirmation;
 * - `confirmed_plan`: confirmed plans; new plans await confirmation, then formal-script overwrite confirmation;
 * - `pending_plan`: unconfirmed plans, replaced entirely; content-confirmation-page edits are lost;
 * - `draft`: drafts needing repairs, replaced entirely; draft edits are lost.
 */
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

/**
 * Plan script dialog visibility: episode pages, content-confirmation pages and production-state next actions only register target episodes/replaced content;
 * the episode page's sole host renders dialogs.
 */
export const useScriptPlanStore = create<ScriptPlanState>((set) => ({
  request: null,
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));
