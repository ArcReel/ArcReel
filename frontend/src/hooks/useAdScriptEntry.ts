import { useTranslation } from "react-i18next";
import { promptAuthoringResourceId } from "@/actions/generation";
import { useActiveResourceIds, useLatestTasksByResource } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import type { TaskItem } from "@/types";

/** 制作状态里广告/短片整份生成的操作键，与后端 `WorkflowActionType.GENERATE_SCRIPT` 一致。 */
const GENERATE_SCRIPT_OPERATION = "generate_script";

export interface AdScriptEntry {
  /** 本集的脚本文本任务在排队或生成中（整份生成与提示词编写共用一个占用槽）。 */
  busy: boolean;
  /** 本集最近一次脚本文本任务；没有时为 undefined。 */
  latestTask: TaskItem | undefined;
  /**
   * 缺创作灵感与商品时的置灰原因，「AI 生成脚本」与「重新生成脚本」共用；
   * 输入齐备或制作状态尚未取回时为 null（提交时服务端按同一谓词复核）。
   */
  refusedReason: string | null;
}

/**
 * 广告/短片整份生成任务：整份重做、因违约失败，或交回了新登记的资产。
 * 同一占用槽上的提示词编写任务不属于这一类。
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
 * 广告/短片「AI 生成脚本」入口的状态：任务占用、最近一次任务与准入。
 *
 * 准入取集页制作状态里 `generate_script` 的结论，与服务端提交时的拒绝理由同源。服务端先报输入缺失、
 * 再报已有正式脚本，所以只有 `ad_brief_and_products_missing` 让入口置灰：`formal_script_exists`
 * 只拦首次生成，而首次生成的入口只在没有正式脚本时出现，整份重做照常可用。
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
