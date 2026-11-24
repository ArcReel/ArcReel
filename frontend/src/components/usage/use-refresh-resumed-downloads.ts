import { useEffect } from "react";
import { useShallow } from "zustand/react/shallow";

import { useTasksStore } from "@/stores/tasks-store";
import type { UsageRecord } from "@/types";

export const DOWNLOAD_FAILED_CODE = "download_failed";

function isDownloadFailure(record: UsageRecord): record is UsageRecord & { task_id: string } {
  return record.status === "failed" && record.error_code === DOWNLOAD_FAILED_CODE && record.task_id !== null;
}

export function useRefreshResumedDownloads(
  records: readonly UsageRecord[],
  refresh: () => Promise<void>,
  localRetries?: ReadonlySet<string>,
): readonly number[] {
  const scope = useTasksStore((state) => state.refreshScope);
  const failedTasks = useTasksStore(useShallow(() => new Map(
    records.filter(isDownloadFailure).map(({ task_id, project_name }) => [task_id, project_name]),
  )));

  useEffect(() => {
    if (!scope) return;
    const { retainTask } = useTasksStore.getState();
    const releases = Array.from(failedTasks, ([taskId, projectName]) => retainTask(projectName, taskId));
    return () => releases.forEach((release) => release());
  }, [failedTasks, scope]);

  const resumedRecordIds = useTasksStore(
    useShallow((state) =>
      records
        .filter(isDownloadFailure)
        .filter(({ task_id }) => {
          if (localRetries?.has(task_id)) return false;
          const task = state.rowsById.get(task_id);
          return task?.status === "succeeded" ||
            (task?.status === "running" && state.activeTaskIds.has(task_id));
        })
        .map((record) => record.id)
        .sort((a, b) => a - b),
    ),
  );

  useEffect(() => {
    if (resumedRecordIds.length > 0) void refresh();
  }, [resumedRecordIds, refresh]);
  return resumedRecordIds;
}
