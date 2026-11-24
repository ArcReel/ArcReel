import { AssetSheetBatchNotifications } from "./AssetSheetBatchNotifications";
import { useTaskFailureNotifications } from "@/hooks/useTaskFailureNotifications";

interface TaskFailureListenerProps {
  projectName?: string | null;
}

export function TaskFailureListener({ projectName }: TaskFailureListenerProps) {
  useTaskFailureNotifications(projectName);
  return <AssetSheetBatchNotifications projectName={projectName} />;
}
