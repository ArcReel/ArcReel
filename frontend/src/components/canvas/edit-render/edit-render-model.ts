import type { EditTimelineIssueRef, EpisodeScript } from "@/types";

/** 各剧本布局存放视频单元的数组字段。 */
const UNIT_FIELDS = ["segments", "scenes", "shots", "video_units"] as const;

interface UnitWithAssets {
  generated_assets?: { video_clip?: string | null } | null;
}

/**
 * 本集是否至少有一个可用视频：新建剪辑时间线与交给 Agent 剪辑的准入条件。
 * 视频是否全部齐全只决定何时建议剪辑，不作为准入。
 */
export function scriptHasUsableVideo(script: EpisodeScript | null | undefined): boolean {
  if (!script) return false;
  const record = script as unknown as Record<string, unknown>;
  return UNIT_FIELDS.some((field) => {
    const units = record[field];
    return (
      Array.isArray(units) &&
      units.some((unit: UnitWithAssets | null) => Boolean(unit?.generated_assets?.video_clip))
    );
  });
}

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
