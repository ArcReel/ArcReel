import { useTranslation } from "react-i18next";
import type { EditTimelineIssueRef } from "@/types";
import type { TimelineNarration } from "@/types/edit-timeline";
import { useItemPositionLabeler } from "@/components/shared/episode-item/useEpisodeItem";
import { formatNameList } from "@/utils/list-format";
import { blockingIssues, issueUnitIds } from "./edit-render-model";

/**
 * Final-render blockers: issue count and affected video units, named by their current episode positions; reason is null without blockers.
 * ``narration`` has the same meaning as in {@link blockingIssues}.
 */
export function useBlockedReason(
  issues: readonly EditTimelineIssueRef[],
  narration?: TimelineNarration,
): { count: number; reason: string | null } {
  const { t, i18n } = useTranslation("dashboard");
  const itemLabel = useItemPositionLabeler();
  const blocking = blockingIssues(issues, narration);
  if (blocking.length === 0) return { count: 0, reason: null };
  const units = issueUnitIds(blocking);
  const reason =
    units.length > 0
      ? t("edit_render_blocked_reason_units", {
          count: blocking.length,
          units: formatNameList(units.map(itemLabel), i18n.language),
        })
      : t("edit_render_blocked_reason", { count: blocking.length });
  return { count: blocking.length, reason };
}
