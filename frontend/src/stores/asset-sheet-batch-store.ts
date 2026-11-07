import { create } from "zustand";
import type { AssetSheetBatchMember } from "@/types";

/** Asset-image batch still tracked after submission: consolidate into one notification when all member tasks reach terminal states. */
export interface TrackedAssetSheetBatch {
  batchId: string;
  projectName: string;
  /** Includes submission failures never enqueued; do not track members blocked by preflight, such as missing descriptions. */
  members: AssetSheetBatchMember[];
}

interface AssetSheetBatchState {
  batches: Record<string, TrackedAssetSheetBatch>;
  /** All previously tracked tasks remain after batch notification so per-task failure notices do not repeat them. */
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

/** Whether the task belongs to an asset-image batch; if so, batch aggregation reports its failure. */
export function isAssetSheetBatchTask(taskId: string): boolean {
  return useAssetSheetBatchStore.getState().trackedTaskIds.has(taskId);
}
