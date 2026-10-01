import { useTranslation } from "react-i18next";
import type { EditTimelineIssueRef } from "@/types";
import type { TimelineNarration } from "@/types/edit-timeline";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { formatNameList } from "@/utils/list-format";
import { blockingIssues, issueUnitIds } from "./edit-render-model";

/**
 * 出片被阻断的原因：阻断 issue 的条数与涉及的视频单元；没有阻断时 reason 为 null。
 * ``narration`` 的含义同 {@link blockingIssues}。
 */
export function useBlockedReason(
  issues: readonly EditTimelineIssueRef[],
  narration?: TimelineNarration,
): { count: number; reason: string | null } {
  const { t, i18n } = useTranslation("dashboard");
  const blocking = blockingIssues(issues, narration);
  if (blocking.length === 0) return { count: 0, reason: null };
  const units = issueUnitIds(blocking);
  const reason =
    units.length > 0
      ? t("edit_render_blocked_reason_units", {
          count: blocking.length,
          units: formatNameList(units.map(itemIdWithinEpisode), i18n.language),
        })
      : t("edit_render_blocked_reason", { count: blocking.length });
  return { count: blocking.length, reason };
}
