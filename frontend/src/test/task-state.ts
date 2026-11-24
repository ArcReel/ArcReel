import { isActiveStatus, mergeTaskSnapshot, useTasksStore } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";

type Fixture = Partial<ReturnType<typeof useTasksStore.getState>> & { tasks?: TaskItem[] };

export function seedTaskState({ tasks, ...state }: Fixture) {
  useTasksStore.setState((prev) => {
    if (!tasks) return state;
    const snapshot = { items: tasks.filter((task) => isActiveStatus(task.status)), truncated: false };
    return { ...state, ...mergeTaskSnapshot({ ...prev, ...state }, snapshot, tasks) };
  });
}

export function seedTasks(tasks: TaskItem[]) {
  seedTaskState({ tasks });
}
