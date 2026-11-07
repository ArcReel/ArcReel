import { create } from "zustand";
import { API } from "@/api";

interface OnboardingState {
  /** Backend seen marker; null means not queried yet. */
  seen: boolean | null;
  /**
   * Whether backends confirmed `seen: true` (GET returned true or POST succeeded).
   * Failed queries also set `seen` true to suppress auto-opening this session, but never count as confirmation;
   * distinguish both true meanings here so `exit()` does not mistakenly skip persistence for the former.
   */
  seenConfirmed: boolean;
  /** Whether onboarding is running. */
  active: boolean;
  /** Fetch seen status; failures treat it as seen, preferring no display over mistaken display. */
  loadStatus: (options?: { signal?: AbortSignal }) => Promise<void>;
  /** Open onboarding; initial automatic display and replay share this path. */
  start: () => void;
  /** Any exit path (skip / close / complete). Mark seen on first exit; replay never writes repeatedly. */
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
      // Late query results must not revert established seen state: users may have completed/exited onboarding while
      // these queries were in flight (local exit() already wrote seen: true).
      if (get().seen === true) return;
      set({ seen, seenConfirmed: seen });
    } catch (err) {
      if (options.signal?.aborted) return;
      // Backend instability must not repeat onboarding for existing users. Suppress this session only, reevaluating after refresh;
      // never count as backend confirmation, so later exit() still persists once.
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
    // Rewriting backend-confirmed seen states is pointless; onboarding permits only this one write,
    // skipping it when possible. Query-failure local suppression is not confirmation and still requires writes.
    if (seenConfirmed) return;
    void API.markOnboardingSeen()
      .then(() => set({ seenConfirmed: true }))
      .catch((err) => {
        console.warn("[onboarding] failed to mark tour as seen", err);
      });
  },
}));
