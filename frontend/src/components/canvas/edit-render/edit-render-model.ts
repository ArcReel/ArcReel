import type { EditTimelineIssueRef } from "@/types";

/**
 * 阻断全部交付物的 issues；只要有一条，出片按钮就不可点。
 * 只影响带旁白版本的阻断不在此列，由提交时的检查按实际版本报告。
 */
export function blockingIssues<T extends EditTimelineIssueRef>(issues: readonly T[]): T[] {
  return issues.filter((issue) => issue.severity === "blocking" && issue.applies_to === "all");
}

/** 阻断 issues 涉及的视频单元编号，去重后按出现顺序排列。 */
export function blockingUnitIds(issues: readonly EditTimelineIssueRef[]): string[] {
  return [...new Set(blockingIssues(issues).flatMap((issue) => (issue.unit_id ? [issue.unit_id] : [])))];
}
