import type { EditTimelineIssueRef } from "@/types";
import type { TimelineNarration } from "@/types/edit-timeline";

/**
 * 阻断出片的 issues。不给旁白版本时只算阻断全部交付物的；给出旁白版本时，
 * 带旁白版本还算上只阻断带旁白版本的（如缺旁白配音）。
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

/** issues 涉及的视频单元编号，去重后按出现顺序排列。 */
export function issueUnitIds(issues: readonly EditTimelineIssueRef[]): string[] {
  return [...new Set(issues.flatMap((issue) => (issue.unit_id ? [issue.unit_id] : [])))];
}
