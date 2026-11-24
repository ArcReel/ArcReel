import { useTranslation } from "react-i18next";
import { promptAuthoringResourceId } from "@/actions/generation";
import { useActiveResourceIds, useLatestTasksByResource } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import type { TaskItem } from "@/types";

const GENERATE_SCRIPT_OPERATION = "generate_script";

export interface AdScriptEntry {
  busy: boolean;
  latestTask: TaskItem | undefined;
  refusedReason: string | null;
}

export function isAdScriptTask(task: TaskItem | undefined): boolean {
  if (!task) return false;
  return (
    task.payload?.regenerate === true ||
    task.error_code === "ad_script_rejected" ||
    Array.isArray(task.result?.new_assets)
  );
}

export function useAdScriptEntry(projectName: string, episode: number): AdScriptEntry {
  const { t } = useTranslation("dashboard");
  const resourceId = promptAuthoringResourceId(episode);
  const busy = useActiveResourceIds("text_episode_script", projectName).has(resourceId);
  const latestTask = useLatestTasksByResource(projectName, "text_episode_script").get(resourceId);
  const operation = useWorkflowStore((s) =>
    s.planKey === `${projectName}::${episode}` ? s.plan?.status.operations[GENERATE_SCRIPT_OPERATION] : undefined,
  );
  const inputsMissing = operation?.state === "refused" && operation.reason === "ad_brief_and_products_missing";
  return { busy, latestTask, refusedReason: inputsMissing ? t("ad_script_refused_inputs") : null };
}
