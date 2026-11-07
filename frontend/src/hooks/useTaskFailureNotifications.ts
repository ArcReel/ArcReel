import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useTasksStore } from "@/stores/tasks-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { isAssetSheetBatchTask } from "@/stores/asset-sheet-batch-store";
import { buildTaskFailureTarget, describeTaskFailure } from "@/utils/task-target";
import type { TaskStatus } from "@/types";

/**
 * Global background-task failure notices: watch queues and push persistent, clickable-return notifications on failed transitions;
 * also notify successful tasks whose targets were deleted at completion (outputs entered project trash). Sole background-failure notification source: users may have left failing pages, so use
 * pushNotification rather than transient toasts (callers still toast synchronous enqueue failures, whose tasks
 * never entered queues and cannot be captured here). Covers storyboard/video/character/scene/prop/grid/
 * reference_video/image_edit.
 *
 * Terminal states arrive through project-event SSE, with fallback polling (useTaskRefresh). Notify two cases:
 * 1. Observed nonfailed → failed transitions;
 * 2. New tasks first appearing after baseline creation already failed: tasks can fail rapidly between polls,
 *    never observed nonfailed, otherwise missing notices.
 * Establish baselines on first successful polls (connected): already-failed baseline tasks are historical, never notified,
 * avoiding historical-failure floods on initial loads/project switches.
 */
export function useTaskFailureNotifications(projectName?: string | null): void {
  const { t } = useTranslation("dashboard");
  // Expose latest t through refs, avoiding language changes rebuilding transition effects and losing prevStatus.
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
  // Whether first-successful-poll baselines exist. Baseline failed tasks are historical, never notified.
  const seededRef = useRef(false);
  // projectName observed during previous effects. During project-switch transition commits, tasks still hold
  // old-project data and refs retain old projectName; synchronize refs with props only after tasks/connected change
  // with new polls and effects rerun. Detect transition commits with this.
  const lastSeenProjectNameRef = useRef<string | null | undefined>(projectName);

  // Reset baselines on project switches, preventing new-project historical failures from appearing new.
  useEffect(() => {
    prevStatusRef.current = new Map();
    seededRef.current = false;
  }, [projectName]);

  useEffect(() => {
    // Transition commits: projectName changed, tasks/lastSeenProjectName have not caught up. Here
    // tasks still belong to old projects; never establish new baselines from them, or next-poll new tasks find
    // empty prev and seeded=true, renotifying all historical failures as fresh.
    const isTransitionCommit = lastSeenProjectNameRef.current !== projectName;
    // Synchronize lastSeenProjectName before connected checks, or early-return effects during disconnected
    // project switches leave refs on old projectName. The first successful poll after connected recovers
    // would then be misclassified as transition, never seed, and miss otherwise detectable failures.
    // fast failure。
    lastSeenProjectNameRef.current = projectName;
    if (!connected) return;
    const prev = prevStatusRef.current;
    const next = new Map<string, TaskStatus>();
    const seeded = seededRef.current;
    for (const tk of tasks) {
      // Track current-project tasks only; others neither notify nor enter prevStatus.
      if (tk.project_name !== projectName) continue;
      const before = prev.get(tk.task_id);
      // Status transitions, or new tasks first appearing after baselines already in that state.
      const isNew = before === undefined ? seeded : before !== tk.status;
      // Asset-image batch members are summarized together by useAssetSheetBatchNotifications.
      const notify = isNew && !isAssetSheetBatchTask(tk.task_id);
      if (notify && tk.status === "succeeded" && tk.result?.target_deleted === true) {
        // Targets deleted at completion: tasks succeed without writing back, outputs entering project trash.
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
    // Establish baselines only outside transition commits. Even empty tasks (projects with none) represent
    // real current-project responses; later tasks failing rapidly between polls and first observed as
    // failed still trigger new-task notices.
    if (!isTransitionCommit) {
      seededRef.current = true;
    }
  }, [tasks, connected, projectName]);
}
