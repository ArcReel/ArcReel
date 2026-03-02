import { create } from "zustand";
import type {
  DeferredWorkspaceFocus,
  WorkspaceFocusTarget,
  WorkspaceFocusTargetInput,
} from "@/types";

interface Toast {
  id: string;
  text: string;
  tone: "info" | "success" | "error" | "warning";
}

interface FocusedContext {
  type: "character" | "clue" | "segment";
  id: string;
}

interface AppState {
  // Context focus (design doc "Context-Aware" feature)
  focusedContext: FocusedContext | null;
  setFocusedContext: (ctx: FocusedContext | null) => void;

  // Scroll targeting (Agent-triggered)
  scrollTarget: WorkspaceFocusTarget | null;
  triggerScrollTo: (target: WorkspaceFocusTargetInput) => void;
  clearScrollTarget: (requestId?: string) => void;
  deferredWorkspaceFocus: DeferredWorkspaceFocus | null;
  setDeferredWorkspaceFocus: (focus: DeferredWorkspaceFocus | null) => void;
  clearDeferredWorkspaceFocus: () => void;
  assistantToolActivitySuppressed: boolean;
  setAssistantToolActivitySuppressed: (suppressed: boolean) => void;

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

  // Media invalidation signal for cache-busted asset URLs
  mediaRevision: number;
  invalidateMediaAssets: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  focusedContext: null,
  setFocusedContext: (ctx) => set({ focusedContext: ctx }),

  scrollTarget: null,
  triggerScrollTo: (target) =>
    set({
      scrollTarget: {
        request_id: target.request_id ?? `${Date.now()}-${Math.random()}`,
        type: target.type,
        id: target.id,
        route: target.route ?? "",
        highlight: true,
        highlight_style: target.highlight_style ?? "flash",
        expires_at: target.expires_at ?? Date.now() + 3000,
      },
    }),
  clearScrollTarget: (requestId) =>
    set((s) => {
      if (!requestId || s.scrollTarget?.request_id === requestId) {
        return { scrollTarget: null };
      }
      return s;
    }),
  deferredWorkspaceFocus: null,
  setDeferredWorkspaceFocus: (focus) => set({ deferredWorkspaceFocus: focus }),
  clearDeferredWorkspaceFocus: () => set({ deferredWorkspaceFocus: null }),
  assistantToolActivitySuppressed: false,
  setAssistantToolActivitySuppressed: (suppressed) =>
    set({ assistantToolActivitySuppressed: suppressed }),

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

  mediaRevision: 0,
  invalidateMediaAssets: () => set((s) => ({ mediaRevision: s.mediaRevision + 1 })),
}));
