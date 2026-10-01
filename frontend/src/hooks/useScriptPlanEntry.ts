import { useTranslation } from "react-i18next";
import { scriptPlanResourceId } from "@/actions/generation";
import { useActiveResourceIds, useLatestTasksByResource } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import type { TaskItem } from "@/types";

/** 制作状态里「AI 规划脚本」的操作键，与后端 `WorkflowActionType.PREPARE_SCRIPT_PLAN` 一致。 */
const SCRIPT_PLAN_OPERATION = "prepare_script_plan";

export interface ScriptPlanEntry {
  /** 本集的脚本规划任务在排队或生成中（含请求往返期间的乐观占用）。 */
  busy: boolean;
  /** 本集最近一次脚本规划任务；没有时为 undefined。 */
  latestTask: TaskItem | undefined;
  /** 准入不成立时的置灰原因；成立或制作状态尚未取回时为 null（提交时服务端按同一谓词复核）。 */
  refusedReason: string | null;
}

/**
 * 「AI 规划脚本」入口的状态：任务占用、最近一次任务与准入。
 *
 * 准入取集页制作状态里同一操作的结论，与服务端提交时的拒绝理由同源；制作状态属于别的集时不采用。
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
