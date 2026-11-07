import { useTranslation } from "react-i18next";
import { promptAuthoringResourceId } from "@/actions/generation";
import { useActiveResourceIds, useLatestTasksByResource } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import type { TaskItem } from "@/types";

/** Production-state whole ad/short-film generation action key, matching backend `WorkflowActionType.GENERATE_SCRIPT`. */
const GENERATE_SCRIPT_OPERATION = "generate_script";

export interface AdScriptEntry {
  /** This episode has queued/running script-text tasks (whole generation and prompt authoring share one occupancy slot). */
  busy: boolean;
  /** Latest script-text task for this episode; undefined if absent. */
  latestTask: TaskItem | undefined;
  /**
   * Disabled reason when creative briefs and products are missing, shared by Generate script and Regenerate script;
   * null when inputs are complete or production state is not yet loaded (server rechecks the same predicate on submission).
   */
  refusedReason: string | null;
}

/**
 * Whole ad/short-film generation tasks: whole regeneration, failed contract violations, or returned newly registered assets.
 * Prompt-authoring tasks sharing the slot do not belong to this category.
 */
export function isAdScriptTask(task: TaskItem | undefined): boolean {
  if (!task) return false;
  return (
    task.payload?.regenerate === true ||
    task.error_code === "ad_script_rejected" ||
    Array.isArray(task.result?.new_assets)
  );
}

/**
 * Ad/short-film Generate script entry state: task occupancy, latest task and admission.
 *
 * Admission uses episode production-state `generate_script`, sharing server submission refusal reasons. Servers report missing inputs
 * before existing formal scripts, so only `ad_brief_and_products_missing` disables entries: `formal_script_exists`
 * blocks initial generation only; that entry appears only without formal scripts, while whole regeneration remains available.
 */
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
