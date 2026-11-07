import { create } from "zustand";

import { useAssistantStore } from "@/stores/assistant-store";

/**
 * One-shot inline notice after story-setting extraction completes.
 *
 * Pending projects/prior empty-state observations remain in memory: view-unmount/return retains notices,
 * background completion while absent counts as empty→populated too; page refresh does not reshow.
 * Acknowledging or sending Agent messages in this project (even before notices appear) records dismissal in localStorage per project,
 * suppressing future notices for that project.
 */

const DISMISSED_KEY_PREFIX = "arcreel_handoff_tip_dismissed:";

function readDismissed(projectName: string): boolean {
  try {
    return localStorage.getItem(DISMISSED_KEY_PREFIX + projectName) === "1";
  } catch {
    return false;
  }
}

interface HandoffTipState {
  /** Projects awaiting notices in this session. */
  pending: ReadonlySet<string>;
  /** Last-observed story-setting emptiness per project in this session. */
  seenEmpty: Readonly<Record<string, boolean>>;
  /** Record observed emptiness; show on empty→populated if never dismissed for this project. */
  observe: (projectName: string, empty: boolean) => void;
  /** Dismiss/remember per project, even before notices appear, suppressing later appearance. */
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
      // Without localStorage, dismiss only for this session.
    }
    if (!get().pending.has(projectName)) return;
    set((state) => {
      const pending = new Set(state.pending);
      pending.delete(projectName);
      return { pending };
    });
  },
}));

// Agent messages in any project view dismiss/remember its notice, including messages before notice appearance:
// store subscriptions survive overview unmounts. Sends/rewrites both set sending true; recognize current-project sessions only.
useAssistantStore.subscribe((state, prev) => {
  if (!state.sending || prev.sending || !state.currentProject) return;
  if (!readDismissed(state.currentProject)) useHandoffTipStore.getState().dismiss(state.currentProject);
});
