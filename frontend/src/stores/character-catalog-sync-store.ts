import { create } from "zustand";
import { API } from "@/api";
import type { CharacterCatalogSyncJob } from "@/types";

interface CharacterCatalogSyncState {
  job: CharacterCatalogSyncJob | null;
  requestPending: boolean;
  setJob: (job: CharacterCatalogSyncJob | null) => void;
  refresh: () => Promise<void>;
  start: () => Promise<CharacterCatalogSyncJob>;
}

export function isCharacterCatalogJobActive(
  job: CharacterCatalogSyncJob | null,
): job is CharacterCatalogSyncJob & { status: "queued" | "running" } {
  return job?.status === "queued" || job?.status === "running";
}

export const useCharacterCatalogSyncStore = create<CharacterCatalogSyncState>((set) => ({
  job: null,
  requestPending: false,
  setJob: (job) => set({ job }),
  refresh: async () => {
    const response = await API.getCharacterCatalogSyncStatus();
    set((state) => {
      const incoming = response.job;
      const current = state.job;
      if (!incoming || !current || incoming.job_id !== current.job_id) return { job: incoming };
      const currentTerminal = current.status === "succeeded" || current.status === "failed";
      if (currentTerminal && isCharacterCatalogJobActive(incoming)) return state;
      if (Date.parse(incoming.updated_at) < Date.parse(current.updated_at)) return state;
      return { job: incoming };
    });
  },
  start: async () => {
    set({ requestPending: true });
    try {
      const response = await API.syncCharacterCatalog();
      set({ job: response.job });
      return response.job;
    } finally {
      set({ requestPending: false });
    }
  },
}));
