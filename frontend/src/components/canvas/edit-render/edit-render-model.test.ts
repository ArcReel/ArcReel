import { describe, expect, it } from "vitest";
import type { EditTimelineIssueRef, EpisodeScript } from "@/types";
import { blockingIssues, blockingUnitIds, scriptHasUsableVideo } from "./edit-render-model";

function scriptWith(field: string, clips: (string | null)[]): EpisodeScript {
  return {
    [field]: clips.map((clip, index) => ({ id: `E1S0${index + 1}`, generated_assets: { video_clip: clip } })),
  } as unknown as EpisodeScript;
}

describe("scriptHasUsableVideo", () => {
  it.each(["segments", "scenes", "shots", "video_units"])("%s 里有一个已生成的视频即满足准入", (field) => {
    expect(scriptHasUsableVideo(scriptWith(field, [null, "videos/E1S02.mp4"]))).toBe(true);
  });

  it("没有任何已生成的视频时不满足准入", () => {
    expect(scriptHasUsableVideo(scriptWith("segments", [null, null]))).toBe(false);
    expect(scriptHasUsableVideo(null)).toBe(false);
  });
});

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
