import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssetSheetBatchStore } from "@/stores/asset-sheet-batch-store";
import { useTasksStore } from "@/stores/tasks-store";
import { assetNotificationTarget } from "@/utils/task-target";
import type { TaskItem } from "@/types";

const TERMINAL: ReadonlySet<string> = new Set(["succeeded", "failed", "cancelled"]);

/** 衍生因同批本体没有成功而从未提交给供应商。 */
function heldBackByOwner(task: TaskItem): boolean {
  return task.error_code === "cascade_blocked_dependency" || task.cancelled_by === "cascade";
}

/**
 * 资产图批量生成的整批通知：一批的成员任务全部落到终态后推一条汇总，可点击回跳到
 * 第一张失败的资产（全部成功时回跳到第一张）。成员任务的逐条失败通知由
 * useTaskFailureNotifications 让出，卡片上也不留失败痕迹。
 */
export function useAssetSheetBatchNotifications(projectName?: string | null): void {
  const { t } = useTranslation("assets");
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const batches = useAssetSheetBatchStore((s) => s.batches);
  const tasks = useTasksStore((s) => s.tasks);
  const [missingTasks, setMissingTasks] = useState<Record<string, TaskItem>>({});

  useEffect(() => {
    const listed = new Set(tasks.map((task) => task.task_id));
    const missing = new Set(
      Object.values(batches)
        .filter((batch) => batch.projectName === projectName)
        .flatMap((batch) => batch.members)
        .map((member) => member.task_id)
        .filter((id): id is string => id !== null && !listed.has(id)),
    );
    if (missing.size === 0) return;
    let cancelled = false;
    // 任务列表有分页上限；批次成员不在列表里时按 ID 补读，每轮任务刷新重取未落定状态。
    void Promise.all([...missing].map((id) => API.getTask(id).catch(() => null))).then((rows) => {
      if (!cancelled) {
        setMissingTasks(Object.fromEntries(rows.filter((row) => row !== null).map((row) => [row.task_id, row])));
      }
    });
    return () => { cancelled = true; };
  }, [batches, tasks, projectName]);

  useEffect(() => {
    if (!projectName) return;
    const byId = new Map([...Object.values(missingTasks), ...tasks].map((task) => [task.task_id, task]));
    for (const batch of Object.values(batches)) {
      if (batch.projectName !== projectName) continue;
      const rows = batch.members.map((member) => {
        const task = member.task_id ? byId.get(member.task_id) : undefined;
        return { member, task, status: member.task_id ? task?.status : member.status };
      });
      if (rows.some(({ status }) => !status || !TERMINAL.has(status))) continue;

      const failed = rows.filter(({ status }) => status !== "succeeded");
      const ownerMissing = failed.filter(({ task, member }) =>
        task ? heldBackByOwner(task) : member.problem_code === "generation_dependency_failed",
      ).length;
      const succeeded = rows.length - failed.length;
      const tr = tRef.current;
      let text =
        failed.length === 0
          ? tr("sheet_batch_done", { count: succeeded })
          : tr("sheet_batch_done_with_failures", { succeeded, failed: failed.length });
      if (ownerMissing > 0) text = `${text}；${tr("sheet_batch_owner_missing", { count: ownerMissing })}`;
      const focus = (failed[0] ?? rows[0])?.member;
      useAppStore.getState().pushNotification(text, failed.length === 0 ? "success" : "error", {
        target: focus ? assetNotificationTarget(focus.asset_type, focus.name) : null,
      });
      useAssetSheetBatchStore.getState().settle(batch.batchId);
    }
  }, [batches, tasks, projectName, missingTasks]);
}
