import { create } from "zustand";

export interface AdScriptOpenRequest {
  projectName: string;
  episode: number;
  /** Full regeneration: replace the existing formal script, confirming the loss list before submission. */
  regenerate: boolean;
}

interface AdScriptState {
  request: AdScriptOpenRequest | null;
  open: (request: AdScriptOpenRequest) => void;
  close: () => void;
}

/**
 * Ad/short-film AI script-generation dialog state: empty states, toolbars and production status only register the episode to open and whether to regenerate the whole script.
 * The dialog is rendered by a single host on the episode page.
 */
export const useAdScriptStore = create<AdScriptState>((set) => ({
  request: null,
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));
