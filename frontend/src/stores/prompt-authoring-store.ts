import { create } from "zustand";

/** Prompt-authoring dialog scope: current item, all pending items or a selected subset. */
export type PromptAuthoringScope = "current" | "pending" | "custom";

export interface PromptAuthoringOpenRequest {
  projectName: string;
  episode: number;
  scope: PromptAuthoringScope;
  /** Currently selected page item; offer Current item only when supplied. */
  currentEntryId?: string | null;
}

interface PromptAuthoringState {
  request: PromptAuthoringOpenRequest | null;
  open: (request: PromptAuthoringOpenRequest) => void;
  close: () => void;
}

/**
 * Prompt-authoring dialog state: storyboard details, timeline toolbar, reference-to-video canvas and production-status next steps
 * only register the scope to open; a single host on the episode page renders the dialog.
 */
export const usePromptAuthoringStore = create<PromptAuthoringState>((set) => ({
  request: null,
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));
