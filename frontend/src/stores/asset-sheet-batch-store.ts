import { create } from "zustand";
import type { AssetSheetBatchMember } from "@/types";

export interface TrackedAssetSheetBatch {
  batchId: string;
  projectName: string;
  members: AssetSheetBatchMember[];
}

interface AssetSheetBatchState {
  batches: Record<string, TrackedAssetSheetBatch>;
  trackedTaskIds: Set<string>;
  track: (batch: TrackedAssetSheetBatch) => void;
  settle: (batchId: string) => void;
}

export const useAssetSheetBatchStore = create<AssetSheetBatchState>((set) => ({
  batches: {},
  trackedTaskIds: new Set(),
  track: (batch) =>
    set((s) => {
      const trackedTaskIds = new Set(s.trackedTaskIds);
      for (const member of batch.members) {
        if (member.task_id) trackedTaskIds.add(member.task_id);
      }
      return { batches: { ...s.batches, [batch.batchId]: batch }, trackedTaskIds };
    }),
  settle: (batchId) =>
    set((s) => {
      const { [batchId]: _settled, ...batches } = s.batches;
      return { batches };
    }),
}));

export function isAssetSheetBatchTask(taskId: string): boolean {
  return useAssetSheetBatchStore.getState().trackedTaskIds.has(taskId);
}
