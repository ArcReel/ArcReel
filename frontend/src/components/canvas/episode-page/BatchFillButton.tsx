import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useActiveResourceIds, type ResourceKind } from "@/stores/tasks-store";
import { useWorkflowStore, workflowPlanKey } from "@/stores/workflow-store";

export type BatchFillKind = "storyboards" | "videos" | "narration";

/** Artifact collection names in the plan. */
const ARTIFACT_OF: Record<BatchFillKind, string> = {
  storyboards: "storyboards",
  videos: "videos",
  narration: "audio",
};

/**
 * How many episode artifacts of a type remain: missing planned entries minus those generating.
 * Return null when the plan is absent/belongs to another episode, this artifact state is unreadable, or the type does not apply.
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
 * Header batch-fill button identifies the artifact type/count; disable with a complete explanation when nothing remains.
 * If the count is unavailable, remain enabled without it; the confirmation dialog lists batch targets.
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
  /** Reference-to-video projects: individually regenerated positions in hints are video units. */
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
