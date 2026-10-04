import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useActiveResourceIds, type ResourceKind } from "@/stores/tasks-store";
import { useWorkflowStore, workflowPlanKey } from "@/stores/workflow-store";

export type BatchFillKind = "storyboards" | "videos" | "narration";

/** 计划里各类产物的集合名。 */
const ARTIFACT_OF: Record<BatchFillKind, string> = {
  storyboards: "storyboards",
  videos: "videos",
  narration: "audio",
};

/**
 * 这一集某类产物还要补齐几份：计划里缺失的条目扣掉正在生成的。
 * 计划还没取到或属于别的集、这类产物读不了或本项目不涉及时数不出来，返回 null。
 */
export function useBatchGap(
  projectName: string,
  episode: number,
  kind: BatchFillKind,
  taskKind: ResourceKind,
): number | null {
  const collection = useWorkflowStore((s) =>
    s.planKey === workflowPlanKey(projectName, episode) ? s.plan?.status.artifacts[ARTIFACT_OF[kind]] : undefined,
  );
  const busy = useActiveResourceIds(taskKind, projectName);
  const missing = collection?.missing_ids;
  if (!Array.isArray(missing) || collection?.state === "blocked" || collection?.state === "not_applicable") {
    return null;
  }
  return missing.filter((id) => !busy.has(id)).length;
}

/**
 * 页头的批量补齐按钮：写明补哪类产物、补几份；没有可补的置灰并说明已齐。
 * 数量数不出来时照常可用、不写数量，点开的确认框会列出这一批。
 */
export function BatchFillButton({
  kind,
  count,
  units = false,
  disabled = false,
  onClick,
}: {
  kind: BatchFillKind;
  count: number | null;
  /** 参考生视频项目：提示里逐条重新生成的位置是视频单元。 */
  units?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const done = count === 0;
  const label = done
    ? t(`batch_fill_${kind}_done`)
    : count === null
      ? t(`batch_generate_${kind}`)
      : t(`batch_fill_${kind}_count`, { count });
  return (
    <Tooltip>
      <TooltipTrigger
        render={<Button variant="outline" size="sm" disabled={done || disabled} onClick={onClick} />}
      >
        <Sparkles aria-hidden data-icon="inline-start" />
        <span className="num">{label}</span>
      </TooltipTrigger>
      <TooltipContent>{t(units ? "batch_fill_hint_units" : "batch_fill_hint")}</TooltipContent>
    </Tooltip>
  );
}
