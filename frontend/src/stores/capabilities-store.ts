import { create } from "zustand";

interface CapabilitiesState {
  revision: number;
  invalidate: () => void;
}

export const useCapabilitiesStore = create<CapabilitiesState>((set) => ({
  revision: 0,
  invalidate: () => set((s) => ({ revision: s.revision + 1 })),
}));
