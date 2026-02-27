import { create } from "zustand";

interface Toast {
  id: string;
  text: string;
  tone: "info" | "success" | "error" | "warning";
}

interface FocusedContext {
  type: "character" | "clue" | "segment";
  id: string;
}

interface ScrollTarget {
  type: string;
  id: string;
  highlight?: boolean;
}

interface AppState {
  // Context focus (design doc "Context-Aware" feature)
  focusedContext: FocusedContext | null;
  setFocusedContext: (ctx: FocusedContext | null) => void;

  // Scroll targeting (Agent-triggered)
  scrollTarget: ScrollTarget | null;
  triggerScrollTo: (target: ScrollTarget) => void;
  clearScrollTarget: () => void;

  // Toast
  toast: Toast | null;
  pushToast: (text: string, tone?: Toast["tone"]) => void;
  clearToast: () => void;

  // Panels
  assistantPanelOpen: boolean;
  toggleAssistantPanel: () => void;
  setAssistantPanelOpen: (open: boolean) => void;
  taskHudOpen: boolean;
  setTaskHudOpen: (open: boolean) => void;

  // Source files invalidation signal
  sourceFilesVersion: number;
  invalidateSourceFiles: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  focusedContext: null,
  setFocusedContext: (ctx) => set({ focusedContext: ctx }),

  scrollTarget: null,
  triggerScrollTo: (target) => set({ scrollTarget: target }),
  clearScrollTarget: () => set({ scrollTarget: null }),

  toast: null,
  pushToast: (text, tone = "info") =>
    set({ toast: { id: `${Date.now()}-${Math.random()}`, text, tone } }),
  clearToast: () => set({ toast: null }),

  assistantPanelOpen: true,
  toggleAssistantPanel: () =>
    set((s) => ({ assistantPanelOpen: !s.assistantPanelOpen })),
  setAssistantPanelOpen: (open) => set({ assistantPanelOpen: open }),
  taskHudOpen: false,
  setTaskHudOpen: (open) => set({ taskHudOpen: open }),

  sourceFilesVersion: 0,
  invalidateSourceFiles: () => set((s) => ({ sourceFilesVersion: s.sourceFilesVersion + 1 })),
}));
