import { useAssetSheetBatchNotifications } from "@/hooks/useAssetSheetBatchNotifications";
import { useTaskFailureNotifications } from "@/hooks/useTaskFailureNotifications";

interface TaskFailureListenerProps {
  projectName?: string | null;
}

export function TaskFailureListener({ projectName }: TaskFailureListenerProps) {
  useTaskFailureNotifications(projectName);
  useAssetSheetBatchNotifications(projectName);
  return null;
}
