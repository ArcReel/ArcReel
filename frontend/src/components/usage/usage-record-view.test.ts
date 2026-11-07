import { describe, expect, it } from "vitest";

import { makeTask } from "@/test/factories";
import { taskToUsageRecordView } from "./usage-record-view";

/** Only tasks receiving `segment_id` at persistence gain running-row storyboard labels. */
function segmentIdOf(overrides: Parameters<typeof makeTask>[0]) {
  return taskToUsageRecordView(makeTask(overrides))?.segmentId;
}

describe("taskToUsageRecordView", () => {
  it("keeps the shot label for the tasks the ledger bills against a segment", () => {
    expect(
      segmentIdOf({ task_type: "storyboard", media_type: "image", resource_id: "E1S10" }),
    ).toBe("E1S10");
    expect(segmentIdOf({ task_type: "grid", media_type: "image", resource_id: "E1G2" })).toBe(
      "E1G2",
    );
    expect(segmentIdOf({ task_type: "video", media_type: "video", resource_id: "E1S10" })).toBe(
      "E1S10",
    );
    expect(
      segmentIdOf({ task_type: "reference_video", media_type: "video", resource_id: "E1U1" }),
    ).toBe("E1U1");
    // audio accounting has no allowlist; resource_id unconditionally becomes segment_id.
    expect(segmentIdOf({ task_type: "tts", media_type: "audio", resource_id: "E1S10" })).toBe(
      "E1S10",
    );
  });

  it("drops the shot label for asset tasks, whose records carry no segment", () => {
    expect(
      segmentIdOf({ task_type: "character", media_type: "image", resource_id: "林夏" }),
    ).toBeNull();
    expect(segmentIdOf({ task_type: "prop", media_type: "image", resource_id: "怀表" })).toBeNull();
    expect(segmentIdOf({ task_type: "scene", media_type: "image", resource_id: "月台" })).toBeNull();
  });

  it("reads the edited resource kind from resource_type for image edits", () => {
    expect(
      segmentIdOf({
        task_type: "image_edit",
        media_type: "image",
        resource_type: "storyboard",
        resource_id: "E1S10",
      }),
    ).toBe("E1S10");
    expect(
      segmentIdOf({
        task_type: "image_edit",
        media_type: "image",
        resource_type: "character",
        resource_id: "林夏",
      }),
    ).toBeNull();
  });

  it("drops the label for task types with no known ledger resource type", () => {
    expect(
      segmentIdOf({ task_type: "unknown_future", media_type: "image", resource_id: "x" }),
    ).toBeNull();
  });

  it("keeps the label for an unmapped audio task, as the audio ledger would", () => {
    // audio accounting has no allowlist: backends record resource_id as segment_id for every resource_type,
    // so frontends must not fall back to generic labels for unregistered resource kinds either.
    expect(
      segmentIdOf({ task_type: "unknown_future", media_type: "audio", resource_id: "E1S10" }),
    ).toBe("E1S10");
  });

  it("projects no usage row for a local render task, which bills nothing", () => {
    expect(
      taskToUsageRecordView(
        makeTask({ task_type: "render_final_cut", media_type: "render", provider_id: "render" }),
      ),
    ).toBeNull();
  });
});
