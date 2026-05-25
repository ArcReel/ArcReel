import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import type { ProjectData, TaskItem } from "@/types";
import { buildTaskFailureTarget, describeTaskFailure } from "@/utils/task-target";

function makeTask(overrides: Partial<TaskItem>): TaskItem {
  return {
    task_id: "t1",
    project_name: "demo",
    task_type: "storyboard",
    media_type: "image",
    resource_id: "E1S01",
    script_file: "ep1.json",
    payload: {},
    status: "failed",
    result: null,
    error_message: "boom",
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: "",
    started_at: null,
    finished_at: null,
    updated_at: "",
    ...overrides,
  };
}

const projectData = {
  episodes: [
    { episode: 1, title: "E1", script_file: "scripts/ep1.json" },
    { episode: 2, title: "E2", script_file: "scripts/ep2.json" },
  ],
} as unknown as ProjectData;

// 回显 key 与参数，便于断言文案选择逻辑。
const t = ((key: string, params?: Record<string, unknown>) =>
  `${key}|${params?.id ?? params?.unitId ?? ""}|${params?.reason ?? ""}`) as unknown as TFunction;

describe("buildTaskFailureTarget", () => {
  it("maps character/scene/prop to asset routes", () => {
    expect(buildTaskFailureTarget(makeTask({ task_type: "character", resource_id: "Hero" }), null)).toEqual({
      type: "character",
      id: "Hero",
      route: "/characters",
      highlight_style: "flash",
    });
    expect(buildTaskFailureTarget(makeTask({ task_type: "scene", resource_id: "Hall" }), null)?.route).toBe("/scenes");
    expect(buildTaskFailureTarget(makeTask({ task_type: "prop", resource_id: "Sword" }), null)?.route).toBe("/props");
  });

  it("maps storyboard/video to the episode segment via script_file", () => {
    const target = buildTaskFailureTarget(
      makeTask({ task_type: "video", resource_id: "E2S03", script_file: "ep2.json" }),
      projectData,
    );
    expect(target).toEqual({ type: "segment", id: "E2S03", route: "/episodes/2", highlight_style: "flash" });
  });

  it("normalizes scripts/ prefix on either side when matching episode", () => {
    // task.script_file 带前缀也能匹配 episodes 中带前缀的记录
    const target = buildTaskFailureTarget(
      makeTask({ task_type: "storyboard", script_file: "scripts/ep1.json" }),
      projectData,
    );
    expect(target?.route).toBe("/episodes/1");
  });

  it("maps grid to the episode (navigate-only, no highlight)", () => {
    const target = buildTaskFailureTarget(
      makeTask({ task_type: "grid", resource_id: "grid-abc", script_file: "ep1.json" }),
      projectData,
    );
    expect(target).toEqual({ type: "grid", id: "grid-abc", route: "/episodes/1" });
  });

  it("maps reference_video to the episode reference unit", () => {
    const target = buildTaskFailureTarget(
      makeTask({ task_type: "reference_video", resource_id: "E1U1", script_file: "ep1.json" }),
      projectData,
    );
    expect(target).toEqual({ type: "reference_unit", id: "E1U1", route: "/episodes/1" });
  });

  it("returns null when the episode cannot be resolved from script_file", () => {
    expect(buildTaskFailureTarget(makeTask({ script_file: "unknown.json" }), projectData)).toBeNull();
    expect(buildTaskFailureTarget(makeTask({ script_file: null }), projectData)).toBeNull();
    expect(buildTaskFailureTarget(makeTask({ task_type: "storyboard" }), null)).toBeNull();
  });

  it("returns null for unknown task types", () => {
    expect(buildTaskFailureTarget(makeTask({ task_type: "unknown" }), projectData)).toBeNull();
  });
});

describe("describeTaskFailure", () => {
  it("selects per-type keys for media/asset tasks", () => {
    expect(describeTaskFailure(t, makeTask({ task_type: "storyboard", resource_id: "E1S01" }))).toBe(
      "storyboard_task_failed|E1S01|boom",
    );
    expect(describeTaskFailure(t, makeTask({ task_type: "grid", resource_id: "g1" }))).toBe("grid_task_failed|g1|boom");
  });

  it("uses the reference key with unitId for reference_video", () => {
    expect(describeTaskFailure(t, makeTask({ task_type: "reference_video", resource_id: "E1U1" }))).toBe(
      "reference_generation_task_failed|E1U1|boom",
    );
  });

  it("falls back to a generic reason when error_message is null", () => {
    const text = describeTaskFailure(t, makeTask({ task_type: "video", error_message: null }));
    expect(text).toContain("reference_status_failed");
  });

  it("returns null for unknown task types", () => {
    expect(describeTaskFailure(t, makeTask({ task_type: "unknown" }))).toBeNull();
  });
});
