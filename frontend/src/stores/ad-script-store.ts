import { create } from "zustand";

export interface AdScriptOpenRequest {
  projectName: string;
  episode: number;
  regenerate: boolean;
}

interface AdScriptState {
  request: AdScriptOpenRequest | null;
  open: (request: AdScriptOpenRequest) => void;
  close: () => void;
}

export const useAdScriptStore = create<AdScriptState>((set) => ({
  request: null,
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));
