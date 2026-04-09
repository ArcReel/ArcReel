import { create } from "zustand";
import type { TaskItem, TaskStats } from "@/types";

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
  queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0,
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
