import type { EditTimelineIssueRef } from "@/types";
import type { TimelineNarration } from "@/types/edit-timeline";

export function blockingIssues<T extends EditTimelineIssueRef>(
  issues: readonly T[],
  narration?: TimelineNarration,
): T[] {
  return issues.filter(
    (issue) =>
      issue.severity === "blocking" &&
      (issue.applies_to === "all" || (narration === "with_narration" && issue.applies_to === "with_narration")),
  );
}

export function issueUnitIds(issues: readonly EditTimelineIssueRef[]): string[] {
  return [...new Set(issues.flatMap((issue) => (issue.unit_id ? [issue.unit_id] : [])))];
}
