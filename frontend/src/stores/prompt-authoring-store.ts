import { create } from "zustand";

export type PromptAuthoringScope = "current" | "pending" | "custom";

export interface PromptAuthoringOpenRequest {
  projectName: string;
  episode: number;
  scope: PromptAuthoringScope;
  currentEntryId?: string | null;
}

interface PromptAuthoringState {
  request: PromptAuthoringOpenRequest | null;
  open: (request: PromptAuthoringOpenRequest) => void;
  close: () => void;
}

export const usePromptAuthoringStore = create<PromptAuthoringState>((set) => ({
  request: null,
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));
