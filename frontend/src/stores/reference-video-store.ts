// frontend/src/stores/reference-video-store.ts
import { create } from "zustand";
import { API } from "@/api";
import type { ReferenceResource, ReferenceVideoUnit, TransitionType } from "@/types";

interface AddUnitPayload {
  prompt: string;
  references: ReferenceResource[];
  duration_seconds?: number;
  transition_to_next?: TransitionType;
  note?: string | null;
}

interface PatchUnitPayload {
  prompt?: string;
  references?: ReferenceResource[];
  duration_seconds?: number;
  transition_to_next?: TransitionType;
  note?: string | null;
}

/** Cache key isolating units per (project, episode) — switching projects with
 * the same episode number must not surface the previous project's units. */
export function referenceVideoCacheKey(projectName: string, episode: number): string {
  return `${projectName}::${episode}`;
}

interface ReferenceVideoStore {
  /** Keyed by `${projectName}::${episode}`. */
  unitsByEpisode: Record<string, ReferenceVideoUnit[]>;
  selectedUnitId: string | null;
  loading: boolean;
  error: string | null;

  loadUnits: (projectName: string, episode: number) => Promise<void>;
  addUnit: (projectName: string, episode: number, payload: AddUnitPayload) => Promise<ReferenceVideoUnit>;
  patchUnit: (projectName: string, episode: number, unitId: string, patch: PatchUnitPayload) => Promise<ReferenceVideoUnit>;
  deleteUnit: (projectName: string, episode: number, unitId: string) => Promise<void>;
  reorderUnits: (projectName: string, episode: number, unitIds: string[]) => Promise<void>;
  generate: (projectName: string, episode: number, unitId: string) => Promise<{ task_id: string; deduped: boolean }>;
  select: (unitId: string | null) => void;
  /**
   * Debounced prompt save. Coalesces rapid edits into a single PATCH per unitId
   * with a 500ms delay. Stale responses (from a superseded in-flight request)
   * are discarded based on a per-unit fetch id counter.
   */
  updatePromptDebounced: (
    projectName: string,
    episode: number,
    unitId: string,
    prompt: string,
  ) => void;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// Per-unit debounce timers — module-scoped so zustand state stays serializable.
const _timers = new Map<string, ReturnType<typeof setTimeout>>();
// Per-unit fetch id; latest wins.
const _fetchIds = new Map<string, number>();
// Last pending payload keyed by unitId.
const _pendingPayload = new Map<string, { prompt: string }>();

const DEBOUNCE_MS = 500;

/** Internal: reset debounce state; only call from tests. */
export function _resetDebounceState(): void {
  _timers.forEach((t) => clearTimeout(t));
  _timers.clear();
  _fetchIds.clear();
  _pendingPayload.clear();
}

export const useReferenceVideoStore = create<ReferenceVideoStore>((set) => ({
  unitsByEpisode: {},
  selectedUnitId: null,
  loading: false,
  error: null,

  loadUnits: async (projectName, episode) => {
    set({ loading: true, error: null });
    try {
      const { units } = await API.listReferenceVideoUnits(projectName, episode);
      set((s) => ({
        unitsByEpisode: { ...s.unitsByEpisode, [referenceVideoCacheKey(projectName, episode)]: units },
        loading: false,
      }));
    } catch (e) {
      set({ loading: false, error: errMsg(e) });
    }
  },

  addUnit: async (projectName, episode, payload) => {
    const { unit } = await API.addReferenceVideoUnit(projectName, episode, payload);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: { ...s.unitsByEpisode, [key]: [...list, unit] },
        selectedUnitId: unit.unit_id,
      };
    });
    return unit;
  },

  patchUnit: async (projectName, episode, unitId, patch) => {
    const { unit } = await API.patchReferenceVideoUnit(projectName, episode, unitId, patch);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: {
          ...s.unitsByEpisode,
          [key]: list.map((u) => (u.unit_id === unitId ? unit : u)),
        },
      };
    });
    return unit;
  },

  deleteUnit: async (projectName, episode, unitId) => {
    await API.deleteReferenceVideoUnit(projectName, episode, unitId);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: { ...s.unitsByEpisode, [key]: list.filter((u) => u.unit_id !== unitId) },
        selectedUnitId: s.selectedUnitId === unitId ? null : s.selectedUnitId,
      };
    });
  },

  reorderUnits: async (projectName, episode, unitIds) => {
    const { units } = await API.reorderReferenceVideoUnits(projectName, episode, unitIds);
    set((s) => ({
      unitsByEpisode: { ...s.unitsByEpisode, [referenceVideoCacheKey(projectName, episode)]: units },
    }));
  },

  generate: async (projectName, episode, unitId) => {
    return API.generateReferenceVideoUnit(projectName, episode, unitId);
  },

  select: (unitId) => set({ selectedUnitId: unitId }),

  updatePromptDebounced: (projectName, episode, unitId, prompt) => {
    _pendingPayload.set(unitId, { prompt });
    const existing = _timers.get(unitId);
    if (existing) clearTimeout(existing);
    const timer = setTimeout(() => {
      _timers.delete(unitId);
      const payload = _pendingPayload.get(unitId);
      _pendingPayload.delete(unitId);
      if (!payload) return;

      const myFetchId = (_fetchIds.get(unitId) ?? 0) + 1;
      _fetchIds.set(unitId, myFetchId);

      void API.patchReferenceVideoUnit(projectName, episode, unitId, {
        prompt: payload.prompt,
      })
        .then(({ unit }) => {
          if (_fetchIds.get(unitId) !== myFetchId) return; // stale
          set((s) => {
            const key = referenceVideoCacheKey(projectName, episode);
            const list = s.unitsByEpisode[key] ?? [];
            return {
              unitsByEpisode: {
                ...s.unitsByEpisode,
                [key]: list.map((u) => (u.unit_id === unitId ? unit : u)),
              },
            };
          });
        })
        .catch((e) => {
          if (_fetchIds.get(unitId) !== myFetchId) return;
          set({ error: errMsg(e) });
        });
    }, DEBOUNCE_MS);
    _timers.set(unitId, timer);
  },
}));
