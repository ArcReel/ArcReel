import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useActiveResourceIds, type ResourceKind } from "@/stores/tasks-store";
import { useWorkflowStore, workflowPlanKey } from "@/stores/workflow-store";

export type BatchFillKind = "storyboards" | "videos" | "narration";

const ARTIFACT_OF: Record<BatchFillKind, string> = {
  storyboards: "storyboards",
  videos: "videos",
  narration: "audio",
};

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

export function BatchFillButton({
  kind,
  count,
  units = false,
  disabled = false,
  onClick,
}: {
  kind: BatchFillKind;
  count: number | null;
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
