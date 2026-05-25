import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useTasksStore } from "@/stores/tasks-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { buildTaskFailureTarget, describeTaskFailure } from "@/utils/task-target";
import type { TaskStatus } from "@/types";

/**
 * 全局后台任务失败通知：监听任务队列，任务转入 failed 时推送一条持久（可点击回跳）
 * 通知。这是后台任务失败的唯一通知来源——用户可能已离开出错的页面，因此用
 * pushNotification 而非瞬时 toast（入队同步失败仍由调用点用 toast 反馈，那类任务
 * 从未进入队列，不会被这里捕获）。覆盖 storyboard/video/character/scene/prop/grid/
 * reference_video。
 *
 * 任务队列是 3 秒轮询（useTasksSSE）。两类情况推送：
 * 1. 观察到非 failed → failed 的状态转换；
 * 2. 基线建立后才首次出现、且首次就已是 failed 的新任务——任务在两次 poll 之间
 *    快速失败、从未被观测到非 failed 状态，否则会被漏报。
 * 用首个成功 poll（connected）建立基线：基线内已存在的 failed 视为历史失败、不推送，
 * 避免初次加载/项目切换时对历史失败刷屏。
 */
export function useTaskFailureNotifications(projectName?: string | null): void {
  const { t } = useTranslation("dashboard");
  // 经 ref 暴露最新 t，避免切语言导致整个转换检测 effect 重建、prevStatus 丢失。
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
  // 是否已用首个成功 poll 建立基线。基线内的 failed 一律视为历史失败、不推送。
  const seededRef = useRef(false);
  // 上次 effect 跑时观察到的 projectName。projectName 切换的过渡 commit 里 tasks 仍是
  // 旧项目数据，此 ref 仍是旧 projectName；只有等到 tasks/connected 因新一轮 poll
  // 变化、effect 再次跑时，ref 才与 props 同步。用它检测"这一轮是否是过渡 commit"。
  const lastSeenProjectNameRef = useRef<string | null | undefined>(projectName);

  // 项目切换时重置基线，避免把新项目的历史 failed 误判为新失败。
  useEffect(() => {
    prevStatusRef.current = new Map();
    seededRef.current = false;
  }, [projectName]);

  useEffect(() => {
    // 过渡 commit：projectName 已变但 tasks/lastSeenProjectName 尚未跟上。此时
    // tasks 仍是旧项目数据，不该用它建立新项目的基线，否则下一轮新 tasks 进来时
    // prev 为空、seeded=true 会把历史 failed 全部当 fresh failure 重弹。
    const isTransitionCommit = lastSeenProjectNameRef.current !== projectName;
    // 在 connected 检查之前同步 lastSeenProjectName——否则 connected=false 期间
    // 切换项目时 effect early return 会让 ref 卡在旧 projectName，下一次 connected
    // 恢复时第一个成功 poll 又被误判为 transition、不 seed，进而漏报本应捕获的
    // fast failure。
    lastSeenProjectNameRef.current = projectName;
    if (!connected) return;
    const prev = prevStatusRef.current;
    const next = new Map<string, TaskStatus>();
    const seeded = seededRef.current;
    for (const tk of tasks) {
      // 只跟踪当前项目的任务：其余项目的任务既不通知也不进 prevStatus。
      if (tk.project_name !== projectName) continue;
      const before = prev.get(tk.task_id);
      const isTransition = before !== undefined && before !== "failed";
      const isFreshFailure = seeded && before === undefined;
      if (tk.status === "failed" && (isTransition || isFreshFailure)) {
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
    // 非过渡 commit 才建立基线——即使本轮 tasks 为空（项目就是没有任务），也代表
    // 已经收到当前项目的真实响应；后续若有 task 在两 poll 间快速失败被首次观测即
    // failed，仍能走 isFreshFailure 通道触发通知。
    if (!isTransitionCommit) {
      seededRef.current = true;
    }
  }, [tasks, connected, projectName]);
}
