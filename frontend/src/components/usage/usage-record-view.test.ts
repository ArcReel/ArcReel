import { describe, expect, it } from "vitest";

import { makeTask } from "@/test/factories";
import { taskToUsageRecordView } from "./usage-record-view";

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

  it("projects text tasks with their purpose and no segment label", () => {
    const planView = taskToUsageRecordView(
      makeTask({ task_type: "text_episode_plan", media_type: "text", resource_id: "episode-planning" }),
    );
    expect(planView).toMatchObject({ mediaType: "text", purpose: "episode_planning", segmentId: null });

    const scriptView = taskToUsageRecordView(
      makeTask({ task_type: "text_drama_script_plan", media_type: "text", resource_id: "episode-1" }),
    );
    expect(scriptView).toMatchObject({ mediaType: "text", purpose: "script_generation", segmentId: null });
  });
});
