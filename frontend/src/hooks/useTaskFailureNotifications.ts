import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useTasksStore } from "@/stores/tasks-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { buildTaskFailureTarget, describeTaskFailure } from "@/utils/task-target";
import type { TaskStatus } from "@/types";

/**
 * 全局后台任务失败通知：监听任务队列，任务由非 failed 转入 failed 时推送一条
 * 持久（可点击回跳）通知。这是后台任务失败的唯一通知来源——用户可能已离开出错
 * 的页面，因此用 pushNotification 而非瞬时 toast（入队同步失败仍由调用点用 toast
 * 反馈，那类任务从未进入队列，不会被这里捕获）。
 *
 * `before !== undefined` 守卫：仅在观察到状态转换时推送，避免首次加载对既有 failed
 * 任务刷屏。覆盖 storyboard/video/character/scene/prop/grid/reference_video。
 */
export function useTaskFailureNotifications(projectName?: string | null): void {
  const { t } = useTranslation("dashboard");
  // 经 ref 暴露最新 t，避免切语言导致整个转换检测 effect 重建、prevStatus 丢失。
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const tasks = useTasksStore((s) => s.tasks);
  const projectData = useProjectsStore((s) => s.currentProjectData);
  const projectDataRef = useRef(projectData);
  useEffect(() => {
    projectDataRef.current = projectData;
  }, [projectData]);

  const prevStatusRef = useRef<Map<string, TaskStatus>>(new Map());

  // 项目切换时重置，避免把新项目首次出现的 failed 任务误判为转换。
  useEffect(() => {
    prevStatusRef.current = new Map();
  }, [projectName]);

  useEffect(() => {
    const prev = prevStatusRef.current;
    const next = new Map<string, TaskStatus>();
    for (const tk of tasks) {
      // 只跟踪当前项目的任务：其余项目的任务既不通知也不进 prevStatus。
      if (tk.project_name !== projectName) continue;
      const before = prev.get(tk.task_id);
      if (
        tk.status === "failed" &&
        before !== undefined &&
        before !== "failed"
      ) {
        const text = describeTaskFailure(tRef.current, tk);
        if (text) {
          useAppStore.getState().pushNotification(text, "error", {
            target: buildTaskFailureTarget(tk, projectDataRef.current),
          });
        }
      }
      next.set(tk.task_id, tk.status);
    }
    prevStatusRef.current = next;
  }, [tasks, projectName]);
}
