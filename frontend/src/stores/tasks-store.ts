import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { TaskItem, TaskStats, TaskStatus } from "@/types";

interface TasksState {
  tasks: TaskItem[];
  stats: TaskStats;
  connected: boolean;

  // Actions
  setTasks: (tasks: TaskItem[]) => void;
  upsertTask: (task: TaskItem) => void;
  setStats: (stats: TaskStats) => void;
  setConnected: (connected: boolean) => void;
}

const defaultStats: TaskStats = {
  queued: 0, running: 0, cancelling: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0,
};

export const useTasksStore = create<TasksState>((set) => ({
  tasks: [],
  stats: defaultStats,
  connected: false,

  setTasks: (tasks) => set({ tasks }),
  upsertTask: (task) =>
    set((s) => {
      const idx = s.tasks.findIndex((t) => t.task_id === task.task_id);
      if (idx >= 0) {
        const updated = [...s.tasks];
        updated[idx] = task;
        return { tasks: updated };
      }
      return { tasks: [task, ...s.tasks] };
    }),
  setStats: (stats) => set({ stats }),
  setConnected: (connected) => set({ connected }),
}));

// ---------------------------------------------------------------------------
// 派生 selector —— 任务队列两条不变量的单一真相源
//
// 消费点（画布 loading 派生、参考视频单元状态等）此前各自重写两条隐性契约：
//   1.「什么算活跃」——排队或运行中的任务占用其 resource（isActiveStatus）。
//   2.「最新行胜出」——同一 resource 可能有多条任务行：失败后重试是新的 task_id，
//      store 按 task_id upsert（见 upsertTask）且不保证 tasks 顺序，故判定时须取
//      updated_at 最新的一行，重试的新行不被旧失败行遮挡（selectLatestTaskByResource）。
//
// 纯函数版把两条不变量收敛于此、可直接用 vitest 测试；hook 版用 useShallow 比较
// Set/Map 内容，保证内容不变时引用稳定，避免每次渲染返回新集合触发重渲染。
// ---------------------------------------------------------------------------

/** 不变量 1：排队或运行中的任务视为占用其 resource。 */
export function isActiveStatus(status: TaskStatus): boolean {
  return status === "queued" || status === "running";
}

/** 终态：任务生命周期末端，不再占用 resource。 */
export function isTerminalStatus(status: TaskStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

/**
 * 不变量 2：按 resource_id 归并任务，同一 resource 多行时取 updated_at 最新的一行。
 * 可选按 projectName / taskType 预筛；store 不保证顺序，故显式比较 updated_at。
 */
export function selectLatestTaskByResource(
  tasks: TaskItem[],
  filter: { projectName?: string; taskType?: string } = {},
): Map<string, TaskItem> {
  const latest = new Map<string, TaskItem>();
  for (const task of tasks) {
    if (filter.projectName !== undefined && task.project_name !== filter.projectName) continue;
    if (filter.taskType !== undefined && task.task_type !== filter.taskType) continue;
    const prev = latest.get(task.resource_id);
    if (!prev || task.updated_at > prev.updated_at) latest.set(task.resource_id, task);
  }
  return latest;
}

/**
 * 命中 taskType + projectName 且「最新行」处于活跃态的 resource_id 集合。
 * 「最新行胜出」下沉于此：重试的新 running/queued 行不被同 resource 的旧 failed 行盖住。
 */
export function selectActiveResourceIds(
  tasks: TaskItem[],
  taskType: string,
  projectName: string,
): Set<string> {
  const ids = new Set<string>();
  for (const task of selectLatestTaskByResource(tasks, { projectName, taskType }).values()) {
    if (isActiveStatus(task.status)) ids.add(task.resource_id);
  }
  return ids;
}

// projectName 缺失时复用同一空集，保证 hook 引用稳定。
const EMPTY_ACTIVE_IDS: Set<string> = new Set();

/** hook 版 {@link selectActiveResourceIds}；projectName 缺失时返回稳定空集。 */
export function useActiveResourceIds(
  taskType: string,
  projectName: string | undefined | null,
): Set<string> {
  return useTasksStore(
    useShallow((s) =>
      projectName ? selectActiveResourceIds(s.tasks, taskType, projectName) : EMPTY_ACTIVE_IDS,
    ),
  );
}

/** hook 版 {@link selectLatestTaskByResource}，按 project + type 预筛。 */
export function useLatestTasksByResource(
  projectName: string,
  taskType: string,
): Map<string, TaskItem> {
  return useTasksStore(
    useShallow((s) => selectLatestTaskByResource(s.tasks, { projectName, taskType })),
  );
}
