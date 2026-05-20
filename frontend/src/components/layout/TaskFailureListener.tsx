import { useTaskFailureNotifications } from "@/hooks/useTaskFailureNotifications";

interface TaskFailureListenerProps {
  projectName?: string | null;
}

/**
 * 无 UI 监听组件：把 useTaskFailureNotifications 对 tasks store 的订阅隔离在叶子节点，
 * 避免直接挂在 StudioLayout 时任务列表每次更新都触发整个布局（顶栏/侧栏/面板）重渲染。
 */
export function TaskFailureListener({ projectName }: TaskFailureListenerProps) {
  useTaskFailureNotifications(projectName);
  return null;
}
