import { create } from "zustand";
import { API } from "@/api";

interface OnboardingState {
  seen: boolean | null;
  seenConfirmed: boolean;
  active: boolean;
  loadStatus: (options?: { signal?: AbortSignal }) => Promise<void>;
  start: () => void;
  exit: () => void;
}

export const useOnboardingStore = create<OnboardingState>((set, get) => ({
  seen: null,
  seenConfirmed: false,
  active: false,

  loadStatus: async (options = {}) => {
    try {
      const { seen } = await API.getOnboardingStatus({ signal: options.signal });
      if (options.signal?.aborted) return;
      if (get().seen === true) return;
      set({ seen, seenConfirmed: seen });
    } catch (err) {
      if (options.signal?.aborted) return;
      console.warn("[onboarding] status fetch failed; suppressing auto-start", err);
      set({ seen: true });
    }
  },

  start: () => {
    if (get().active) return;
    set({ active: true });
  },

  exit: () => {
    const { active, seenConfirmed } = get();
    if (!active) return;
    set({ active: false, seen: true });
    if (seenConfirmed) return;
    void API.markOnboardingSeen()
      .then(() => set({ seenConfirmed: true }))
      .catch((err) => {
        console.warn("[onboarding] failed to mark tour as seen", err);
      });
  },
}));
