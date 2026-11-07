import { useAssetSheetBatchNotifications } from "@/hooks/useAssetSheetBatchNotifications";
import { useTaskFailureNotifications } from "@/hooks/useTaskFailureNotifications";

interface TaskFailureListenerProps {
  projectName?: string | null;
}

/**
 * Listener without UI: isolate tasks-store subscriptions for task failures and asset-image batch notifications in leaf nodes,
 * avoiding full StudioLayout (header/sidebar/panel) rerenders whenever task lists change.
 */
export function TaskFailureListener({ projectName }: TaskFailureListenerProps) {
  useTaskFailureNotifications(projectName);
  useAssetSheetBatchNotifications(projectName);
  return null;
}
