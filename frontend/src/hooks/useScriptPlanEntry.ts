import { useTranslation } from "react-i18next";
import { scriptPlanResourceId } from "@/actions/generation";
import { useActiveResourceIds, useLatestTasksByResource } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import type { TaskItem } from "@/types";

/** Production-state Plan script action key, matching backend `WorkflowActionType.PREPARE_SCRIPT_PLAN`. */
const SCRIPT_PLAN_OPERATION = "prepare_script_plan";

export interface ScriptPlanEntry {
  /** This episode has queued/running script-planning tasks (including optimistic occupancy during request round trips). */
  busy: boolean;
  /** Latest script-planning task for this episode; undefined if absent. */
  latestTask: TaskItem | undefined;
  /** Disabled admission reason; null when admitted or production state is not yet loaded (server rechecks the same predicate on submission). */
  refusedReason: string | null;
}

/**
 * Plan script entry state: task occupancy, latest task and admission.
 *
 * Admission uses the same episode production-state action, sharing server submission refusal reasons; ignore other episodes' production state.
 */
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
