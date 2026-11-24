import { create } from "zustand";
import type { AssetSheetBatchMember } from "@/types";

export interface TrackedAssetSheetBatch {
  batchId: string;
  projectName: string;
  members: AssetSheetBatchMember[];
}

interface AssetSheetBatchState {
  batches: Record<string, TrackedAssetSheetBatch>;
  settledTaskProjects: Map<string, string>;
  track: (batch: TrackedAssetSheetBatch) => void;
  settle: (batch: TrackedAssetSheetBatch, recentTaskIds: readonly string[]) => void;
  pruneSuppression: (projectName: string | null | undefined, recentTaskIds: readonly string[]) => void;
}

export const useAssetSheetBatchStore = create<AssetSheetBatchState>((set) => ({
  batches: {},
  settledTaskProjects: new Map(),
  track: (batch) => set((s) => ({ batches: { ...s.batches, [batch.batchId]: batch } })),
  settle: (batch, recentTaskIds) =>
    set((s) => {
      const { [batch.batchId]: _settled, ...batches } = s.batches;
      const recent = new Set(recentTaskIds);
      const settledTaskProjects = new Map(s.settledTaskProjects);
      for (const { task_id } of batch.members) {
        if (task_id && recent.has(task_id)) settledTaskProjects.set(task_id, batch.projectName);
      }
      return { batches, settledTaskProjects };
    }),
  pruneSuppression: (projectName, recentTaskIds) =>
    set((s) => {
      if (!s.settledTaskProjects.size) return s;
      const recent = new Set(recentTaskIds);
      const keep = [...s.settledTaskProjects].filter(([id, project]) => project === projectName && recent.has(id));
      return keep.length === s.settledTaskProjects.size ? s : { settledTaskProjects: new Map(keep) };
    }),
}));

export function suppressedAssetSheetTaskIds(projectName: string | null | undefined): Set<string> {
  const { batches, settledTaskProjects } = useAssetSheetBatchStore.getState();
  const ids = new Set<string>();
  for (const [id, project] of settledTaskProjects) {
    if (project === projectName) ids.add(id);
  }
  for (const batch of Object.values(batches)) {
    if (batch.projectName !== projectName) continue;
    for (const { task_id } of batch.members) {
      if (task_id) ids.add(task_id);
    }
  }
  return ids;
}
