import type { EditTimelineIssueRef } from "@/types";
import type { TimelineNarration } from "@/types/edit-timeline";

/**
 * Issues blocking rendering. Without a narration version, count only issues blocking every deliverable; when a version is specified,
 * the version with narration also includes issues blocking only that version, such as missing narration audio.
 */
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

/** Video-unit numbers affected by issues, deduplicated in order of appearance. */
export function issueUnitIds(issues: readonly EditTimelineIssueRef[]): string[] {
  return [...new Set(issues.flatMap((issue) => (issue.unit_id ? [issue.unit_id] : [])))];
}
