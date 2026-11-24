import { useTranslation } from "react-i18next";
import { scriptPlanResourceId } from "@/actions/generation";
import { useActiveResourceIds, useLatestTasksByResource } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import type { TaskItem } from "@/types";

const SCRIPT_PLAN_OPERATION = "prepare_script_plan";

export interface ScriptPlanEntry {
  busy: boolean;
  latestTask: TaskItem | undefined;
  refusedReason: string | null;
}

export function useScriptPlanEntry(projectName: string, episode: number): ScriptPlanEntry {
  const { t } = useTranslation("dashboard");
  const resourceId = scriptPlanResourceId(episode);
  const busy = useActiveResourceIds("text_script_plan", projectName).has(resourceId);
  const latestTask = useLatestTasksByResource(projectName, "text_script_plan").get(resourceId);
  const operation = useWorkflowStore((s) =>
    s.planKey === `${projectName}::${episode}` ? s.plan?.status.operations[SCRIPT_PLAN_OPERATION] : undefined,
  );
  let refusedReason: string | null = null;
  if (operation && operation.state !== "admitted") {
    refusedReason =
      operation.reason === "episode_source_missing"
        ? t("script_plan_refused_episode_source_missing")
        : t("script_plan_refused");
  }
  return { busy, latestTask, refusedReason };
}
