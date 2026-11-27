import { create } from "zustand";

/** Counts are scoped to projects so switching projects cannot reuse another library's total. */
export const useDraftCountStore = create<{
  counts: Record<string, number>;
  revisions: Record<string, number>;
  setCount: (projectName: string, count: number) => void;
  invalidate: (projectName: string) => void;
}>((set) => ({
  counts: {},
  revisions: {},
  setCount: (projectName, count) => set((state) => ({ counts: { ...state.counts, [projectName]: count } })),
  invalidate: (projectName) => set((state) => ({
    revisions: { ...state.revisions, [projectName]: (state.revisions[projectName] ?? 0) + 1 },
  })),
}));
