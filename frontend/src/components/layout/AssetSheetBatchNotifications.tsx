import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import { useAppStore } from "@/stores/app-store";
import { useAssetSheetBatchStore, type TrackedAssetSheetBatch } from "@/stores/asset-sheet-batch-store";
import { useTasksStore, type TasksRefreshScope } from "@/stores/tasks-store";
import { assetNotificationTarget } from "@/utils/task-target";
import type { TaskItem } from "@/types";

const TERMINAL_RESULTS = new Set(["succeeded", "failed", "cancelled", "missing"]);

function heldBackByOwner(task: TaskItem): boolean {
  return task.error_code === "cascade_blocked_dependency" || task.cancelled_by === "cascade";
}

export function AssetSheetBatchNotifications({ projectName }: { projectName?: string | null }) {
  const batches = useAssetSheetBatchStore((s) => s.batches);
  const scope = useTasksStore((s) => s.refreshScope);
  if (!projectName || scope?.projectName !== projectName) return null;
  return Object.values(batches)
    .filter((batch) => batch.projectName === projectName)
    .map((batch) => <BatchNotification key={batch.batchId} batch={batch} scope={scope} />);
}

function BatchNotification({ batch, scope }: { batch: TrackedAssetSheetBatch; scope: TasksRefreshScope }) {
  const { t } = useTranslation("assets");
  const rows = useTasksStore(useShallow((s) =>
    batch.members.map(({ task_id }) => task_id === null ? undefined : s.rowsById.get(task_id)),
  ));
  const tracked = useTasksStore(useShallow((s) =>
    batch.members.map(({ task_id }) => task_id === null ? undefined : s.tracked.get(task_id)?.status),
  ));

  useEffect(() => {
    if (useTasksStore.getState().refreshScope !== scope) return;
    const taskIds = new Set(batch.members.flatMap(({ task_id }) => task_id ? [task_id] : []));
    const store = useTasksStore.getState();
    const releases = [...taskIds].map((id) => store.retainTask(batch.projectName, id));
    return () => releases.forEach((release) => release());
  }, [batch, scope]);

  useEffect(() => {
    const batches = useAssetSheetBatchStore.getState();
    const tasks = useTasksStore.getState();
    if (batches.batches[batch.batchId] !== batch || tasks.refreshScope !== scope) return;

    const results = batch.members.map((member, index) => {
      const task = rows[index];
      const status = member.task_id === null ? member.status : task?.status;
      return { member, task, status: tracked[index] === "missing" ? "missing" : status };
    });
    if (results.some(({ status }) => !status || !TERMINAL_RESULTS.has(status))) return;

    const failed = results.filter(({ status }) => status === "failed" || status === "cancelled");
    const unavailable = results.filter(({ status }) => status === "missing");
    const ownerMissing = failed.filter(({ task, member }) =>
      task ? heldBackByOwner(task) : member.problem_code === "generation_dependency_failed",
    ).length;
    const targetDeleted = results.filter(
      ({ status, task }) => status === "succeeded" && task?.result?.target_deleted === true,
    ).length;
    const succeeded = results.length - failed.length - unavailable.length - targetDeleted;
    let text = unavailable.length > 0
      ? t("sheet_batch_done_with_missing", { succeeded, failed: failed.length, missing: unavailable.length })
      : failed.length > 0
        ? t("sheet_batch_done_with_failures", { succeeded, failed: failed.length })
        : t("sheet_batch_done", { count: succeeded });
    const clauseSeparator = t("sheet_batch_clause_separator");
    if (ownerMissing > 0) text = `${text}${clauseSeparator}${t("sheet_batch_owner_missing", { count: ownerMissing })}`;
    if (targetDeleted > 0) text = `${text}${clauseSeparator}${t("sheet_batch_target_deleted", { count: targetDeleted })}`;
    const focus = (failed[0] ?? unavailable[0] ?? results[0])?.member;
    const tone = failed.length > 0 ? "error" : targetDeleted > 0 || unavailable.length > 0 ? "warning" : "success";
    const retainOffPageIds = new Set(
      results
        .filter(({ member, status, task }) =>
          member.task_id !== null && (status === "failed" || (status === "succeeded" && task?.result?.target_deleted === true)),
        )
        .map(({ member }) => member.task_id!),
    );

    batches.settle(batch, tasks.recentTaskIds, retainOffPageIds);
    useAppStore.getState().pushNotification(text, tone, {
      target: focus ? assetNotificationTarget(focus.asset_type, focus.name) : null,
    });
  }, [batch, rows, scope, t, tracked]);

  return null;
}
