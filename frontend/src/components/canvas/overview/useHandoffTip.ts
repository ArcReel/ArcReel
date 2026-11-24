import { create } from "zustand";

import { useAssistantStore } from "@/stores/assistant-store";


const DISMISSED_KEY_PREFIX = "arcreel_handoff_tip_dismissed:";

function readDismissed(projectName: string): boolean {
  try {
    return localStorage.getItem(DISMISSED_KEY_PREFIX + projectName) === "1";
  } catch {
    return false;
  }
}

interface HandoffTipState {
  pending: ReadonlySet<string>;
  seenEmpty: Readonly<Record<string, boolean>>;
  observe: (projectName: string, empty: boolean) => void;
  dismiss: (projectName: string) => void;
}

export const useHandoffTipStore = create<HandoffTipState>((set, get) => ({
  pending: new Set(),
  seenEmpty: {},
  observe: (projectName, empty) => {
    const wasEmpty = get().seenEmpty[projectName];
    if (wasEmpty === empty) return;
    set((state) => ({ seenEmpty: { ...state.seenEmpty, [projectName]: empty } }));
    if (!wasEmpty || empty || readDismissed(projectName)) return;
    set((state) => ({ pending: new Set(state.pending).add(projectName) }));
  },
  dismiss: (projectName) => {
    try {
      localStorage.setItem(DISMISSED_KEY_PREFIX + projectName, "1");
    } catch {
      // localStorage 不可用时只在本次会话内关闭
    }
    if (!get().pending.has(projectName)) return;
    set((state) => {
      const pending = new Set(state.pending);
      pending.delete(projectName);
      return { pending };
    });
  },
}));

useAssistantStore.subscribe((state, prev) => {
  if (!state.sending || prev.sending || !state.currentProject) return;
  if (!readDismissed(state.currentProject)) useHandoffTipStore.getState().dismiss(state.currentProject);
});
