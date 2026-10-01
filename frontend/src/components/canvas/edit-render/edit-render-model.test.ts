import { describe, expect, it } from "vitest";
import type { EditTimelineIssueRef } from "@/types";
import { blockingIssues, issueUnitIds } from "./edit-render-model";

describe("blocking issues", () => {
  const issues: EditTimelineIssueRef[] = [
    { code: "trim_ignored", severity: "info", applies_to: "all", unit_id: "E1S01" },
    { code: "video_missing", severity: "blocking", applies_to: "all", unit_id: "E1S03" },
    { code: "hold_too_long", severity: "warning", applies_to: "all", unit_id: "E1S02" },
    { code: "video_missing", severity: "blocking", applies_to: "all", unit_id: "E1S03" },
    { code: "video_missing", severity: "blocking", applies_to: "all", unit_id: "E1S05" },
  ];
  const narrationMissing: EditTimelineIssueRef = {
    code: "narration_missing",
    severity: "blocking",
    applies_to: "with_narration",
    unit_id: "E1S04",
  };

  it("只取阻断级 issue", () => {
    expect(blockingIssues(issues).map((issue) => issue.code)).toEqual([
      "video_missing",
      "video_missing",
      "video_missing",
    ]);
  });

  it("只阻断带旁白版本的 issue 只在选了带旁白版本时算作阻断", () => {
    expect(blockingIssues([narrationMissing])).toEqual([]);
    expect(blockingIssues([narrationMissing], "without_narration")).toEqual([]);
    expect(blockingIssues([narrationMissing], "with_narration")).toEqual([narrationMissing]);
  });

  it("涉及的视频单元去重并保持顺序", () => {
    expect(issueUnitIds(blockingIssues(issues))).toEqual(["E1S03", "E1S05"]);
  });
});
