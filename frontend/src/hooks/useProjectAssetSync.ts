import { useCallback, useEffect, useRef } from "react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";

const SYNCED_TASK_TYPES = new Set(["storyboard", "video", "character", "clue"]);

export function useProjectAssetSync(projectName?: string | null): void {
  const tasks = useTasksStore((s) => s.tasks);
  const previousStatusRef = useRef(new Map<string, string>());
  const processedTransitionsRef = useRef(new Set<string>());
  const initializedRef = useRef(false);
  const refreshingRef = useRef(false);
  const needsRefreshRef = useRef(false);

  const refreshProject = useCallback(async () => {
    if (!projectName) return;
    if (refreshingRef.current) {
      needsRefreshRef.current = true;
      return;
    }

    refreshingRef.current = true;
    try {
      const res = await API.getProject(projectName);
      useProjectsStore.getState().setCurrentProject(
        projectName,
        res.project,
        res.scripts ?? {},
        res.asset_fingerprints,
      );
      useAppStore.getState().invalidateAllEntities();
    } catch {
      // Ignore refresh failures so SSE processing can continue.
    } finally {
      refreshingRef.current = false;
      if (needsRefreshRef.current) {
        needsRefreshRef.current = false;
        void refreshProject();
      }
    }
  }, [projectName]);

  useEffect(() => {
    previousStatusRef.current.clear();
    processedTransitionsRef.current.clear();
    initializedRef.current = false;
    needsRefreshRef.current = false;
    refreshingRef.current = false;
  }, [projectName]);

  useEffect(() => {
    if (!projectName) return;

    const projectTasks = tasks.filter((task) => task.project_name === projectName);

    if (!initializedRef.current) {
      previousStatusRef.current = new Map(
        projectTasks.map((task) => [task.task_id, task.status]),
      );
      initializedRef.current = true;
      return;
    }

    let shouldRefresh = false;

    for (const task of projectTasks) {
      const previousStatus = previousStatusRef.current.get(task.task_id);
      const transitionKey = `${task.task_id}:${task.updated_at}`;

      if (
        previousStatus &&
        previousStatus !== "succeeded" &&
        task.status === "succeeded" &&
        SYNCED_TASK_TYPES.has(task.task_type) &&
        !processedTransitionsRef.current.has(transitionKey)
      ) {
        processedTransitionsRef.current.add(transitionKey);
        shouldRefresh = true;
      }
    }

    previousStatusRef.current = new Map(
      projectTasks.map((task) => [task.task_id, task.status]),
    );

    if (shouldRefresh) {
      void refreshProject();
    }
  }, [projectName, refreshProject, tasks]);
}
