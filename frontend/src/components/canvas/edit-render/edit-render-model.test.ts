import { describe, expect, it } from "vitest";
import type { EditTimelineIssueRef } from "@/types";
import { blockingIssues, blockingUnitIds } from "./edit-render-model";

describe("blocking issues", () => {
  const issues: EditTimelineIssueRef[] = [
    { code: "trim_ignored", severity: "info", applies_to: "all", unit_id: "E1S01" },
    { code: "video_missing", severity: "blocking", applies_to: "all", unit_id: "E1S03" },
    { code: "hold_too_long", severity: "warning", applies_to: "all", unit_id: "E1S02" },
    { code: "video_missing", severity: "blocking", applies_to: "all", unit_id: "E1S03" },
    { code: "video_missing", severity: "blocking", applies_to: "all", unit_id: "E1S05" },
  ];

  it("只取阻断级 issue", () => {
    expect(blockingIssues(issues).map((issue) => issue.code)).toEqual([
      "video_missing",
      "video_missing",
      "video_missing",
    ]);
  });

  it("只影响带旁白版本的阻断不禁用出片入口", () => {
    expect(
      blockingIssues([{ code: "video_missing", severity: "blocking", applies_to: "with_narration", unit_id: "E1S04" }]),
    ).toEqual([]);
  });

    it("阻断涉及的视频单元去重并保持顺序", () => {
    expect(blockingUnitIds(issues)).toEqual(["E1S03", "E1S05"]);
  });
});
