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
   * Debounced unit save. Coalesces rapid prompt+references edits into a single
   * PATCH per (project, episode, unitId) with a 500ms delay. Stale responses
   * from superseded in-flight requests are discarded via a per-key fetch id.
   */
  updatePromptDebounced: (
    projectName: string,
    episode: number,
    unitId: string,
    prompt: string,
    references: ReferenceResource[],
  ) => void;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// Composite key isolates debounce state per (project, episode, unit) — unit_id
// format `E{episode}U{n}` is not globally unique, so two projects editing the
// same-named unit must not share timers or pending payloads.
function _debounceKey(projectName: string, episode: number, unitId: string): string {
  return `${projectName}::${episode}::${unitId}`;
}

// Module-scoped so zustand state stays serializable.
const _timers = new Map<string, ReturnType<typeof setTimeout>>();
const _fetchIds = new Map<string, number>();
const _pendingPayload = new Map<string, { prompt: string; references: ReferenceResource[] }>();

const DEBOUNCE_MS = 500;

function _clearUnitDebounce(key: string): void {
  const t = _timers.get(key);
  if (t) clearTimeout(t);
  _timers.delete(key);
  _fetchIds.delete(key);
  _pendingPayload.delete(key);
}

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
    _clearUnitDebounce(_debounceKey(projectName, episode, unitId));
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

  updatePromptDebounced: (projectName, episode, unitId, prompt, references) => {
    const dkey = _debounceKey(projectName, episode, unitId);
    _pendingPayload.set(dkey, { prompt, references });
    const existing = _timers.get(dkey);
    if (existing) clearTimeout(existing);
    const timer = setTimeout(() => {
      _timers.delete(dkey);
      const payload = _pendingPayload.get(dkey);
      _pendingPayload.delete(dkey);
      if (!payload) return;

      const myFetchId = (_fetchIds.get(dkey) ?? 0) + 1;
      _fetchIds.set(dkey, myFetchId);

      void API.patchReferenceVideoUnit(projectName, episode, unitId, {
        prompt: payload.prompt,
        references: payload.references,
      })
        .then(({ unit }) => {
          if (_fetchIds.get(dkey) !== myFetchId) return; // stale
          set((s) => {
            const ekey = referenceVideoCacheKey(projectName, episode);
            const list = s.unitsByEpisode[ekey] ?? [];
            return {
              unitsByEpisode: {
                ...s.unitsByEpisode,
                [ekey]: list.map((u) => (u.unit_id === unitId ? unit : u)),
              },
            };
          });
        })
        .catch((e) => {
          if (_fetchIds.get(dkey) !== myFetchId) return;
          set({ error: errMsg(e) });
        });
    }, DEBOUNCE_MS);
    _timers.set(dkey, timer);
  },
}));
