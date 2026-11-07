import { create } from "zustand";

// ---------------------------------------------------------------------------
// CapabilitiesStore: model-capability invalidation signals.
//
// Backends resolve effective capabilities (including user overrides) through /video-capabilities; frontend never caches contents,
// only whether the current revision remains valid. Project models/video backends live in project fields, whose changes return
// through project data to re-resolve capabilities. Other writes affecting results change no project fields: capability overrides/custom
// providers live in provider configuration, global default backends in system settings. No props change after such writes,
// requiring explicit signals to invalidate all active capability queries.
//
// revision increases monotonically; consumers include it in request dependencies, so +1 refetches.
// ---------------------------------------------------------------------------

interface CapabilitiesState {
  /** Capability-data revision, incremented on every invalidate. */
  revision: number;
  /** Invalidate current capabilities, refetching all active queries. Call after any successful write category above. */
  invalidate: () => void;
}

export const useCapabilitiesStore = create<CapabilitiesState>((set) => ({
  revision: 0,
  invalidate: () => set((s) => ({ revision: s.revision + 1 })),
}));
