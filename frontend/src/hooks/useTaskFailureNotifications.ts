import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useTasksStore } from "@/stores/tasks-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { isAssetSheetBatchTask } from "@/stores/asset-sheet-batch-store";
import { buildTaskFailureTarget, describeTaskFailure } from "@/utils/task-target";
import type { TaskStatus } from "@/types";

export function useTaskFailureNotifications(projectName?: string | null): void {
  const { t } = useTranslation("dashboard");
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const tasks = useTasksStore((s) => s.tasks);
  const connected = useTasksStore((s) => s.connected);
  const projectData = useProjectsStore((s) => s.currentProjectData);
  const projectDataRef = useRef(projectData);
  useEffect(() => {
    projectDataRef.current = projectData;
  }, [projectData]);

  const prevStatusRef = useRef<Map<string, TaskStatus>>(new Map());
  const seededRef = useRef(false);
  const lastSeenProjectNameRef = useRef<string | null | undefined>(projectName);

  useEffect(() => {
    prevStatusRef.current = new Map();
    seededRef.current = false;
  }, [projectName]);

  useEffect(() => {
    const isTransitionCommit = lastSeenProjectNameRef.current !== projectName;
    lastSeenProjectNameRef.current = projectName;
    if (!connected) return;
    const prev = prevStatusRef.current;
    const next = new Map<string, TaskStatus>();
    const seeded = seededRef.current;
    for (const tk of tasks) {
      if (tk.project_name !== projectName) continue;
      const before = prev.get(tk.task_id);
      const isNew = before === undefined ? seeded : before !== tk.status;
      const notify = isNew && !isAssetSheetBatchTask(tk.task_id);
      if (notify && tk.status === "succeeded" && tk.result?.target_deleted === true) {
        useAppStore.getState().pushNotification(tRef.current("task_target_deleted"), "warning");
      }
      if (notify && tk.status === "failed") {
        const text = describeTaskFailure(
          tRef.current,
          tk,
          projectDataRef.current,
          useProjectsStore.getState().currentScripts,
        );
        if (text) {
          useAppStore.getState().pushNotification(text, "error", {
            target: buildTaskFailureTarget(tk, projectDataRef.current),
          });
        }
      }
      next.set(tk.task_id, tk.status);
    }
    prevStatusRef.current = next;
    if (!isTransitionCommit) {
      seededRef.current = true;
    }
  }, [tasks, connected, projectName]);
}
