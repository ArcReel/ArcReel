import { create } from "zustand";
import type { AssetSheetBatchMember } from "@/types";

export interface TrackedAssetSheetBatch {
  batchId: string;
  projectName: string;
  members: AssetSheetBatchMember[];
}

interface SettledTaskEntry {
  projectName: string;
  pendingRecentRef: readonly string[] | null;
}

interface AssetSheetBatchState {
  batches: Record<string, TrackedAssetSheetBatch>;
  settledTaskProjects: Map<string, SettledTaskEntry>;
  track: (batch: TrackedAssetSheetBatch) => void;
  settle: (
    batch: TrackedAssetSheetBatch,
    recentTaskIds: readonly string[],
    retainOffPageIds?: ReadonlySet<string>,
  ) => void;
  pruneSuppression: (projectName: string | null | undefined, recentTaskIds: readonly string[]) => void;
}

export const useAssetSheetBatchStore = create<AssetSheetBatchState>((set) => ({
  batches: {},
  settledTaskProjects: new Map(),
  track: (batch) => set((s) => ({ batches: { ...s.batches, [batch.batchId]: batch } })),
  settle: (batch, recentTaskIds, retainOffPageIds) =>
    set((s) => {
      const { [batch.batchId]: _settled, ...batches } = s.batches;
      const recent = new Set(recentTaskIds);
      const settledTaskProjects = new Map(s.settledTaskProjects);
      for (const { task_id } of batch.members) {
        if (!task_id) continue;
        if (recent.has(task_id)) {
          settledTaskProjects.set(task_id, { projectName: batch.projectName, pendingRecentRef: null });
        } else if (retainOffPageIds?.has(task_id)) {
          settledTaskProjects.set(task_id, { projectName: batch.projectName, pendingRecentRef: recentTaskIds });
        }
      }
      return { batches, settledTaskProjects };
    }),
  pruneSuppression: (projectName, recentTaskIds) =>
    set((s) => {
      if (!s.settledTaskProjects.size) return s;
      const recent = new Set(recentTaskIds);
      const next = new Map<string, SettledTaskEntry>();
      let changed = false;
      for (const [id, entry] of s.settledTaskProjects) {
        if (entry.projectName !== projectName) {
          changed = true;
        } else if (recent.has(id)) {
          if (entry.pendingRecentRef !== null) changed = true;
          next.set(id, { projectName, pendingRecentRef: null });
        } else if (entry.pendingRecentRef === recentTaskIds) {
          next.set(id, entry);
        } else {
          changed = true;
        }
      }
      return changed ? { settledTaskProjects: next } : s;
    }),
}));

export function suppressedAssetSheetTaskIds(projectName: string | null | undefined): Set<string> {
  const { batches, settledTaskProjects } = useAssetSheetBatchStore.getState();
  const ids = new Set<string>();
  for (const [id, entry] of settledTaskProjects) {
    if (entry.projectName === projectName) ids.add(id);
  }
  for (const batch of Object.values(batches)) {
    if (batch.projectName !== projectName) continue;
    for (const { task_id } of batch.members) {
      if (task_id) ids.add(task_id);
    }
  }
  return ids;
}
