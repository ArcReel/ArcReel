import { describe, expect, it } from "vitest";

import { makeTask } from "@/test/factories";
import type { TaskItem } from "@/types";

import { customModelSettingsPath, lastPlanningFailure } from "./episode-planning-model";

function planning(overrides: Partial<TaskItem>): TaskItem {
  return makeTask({ task_type: "text_episode_plan", resource_id: "episode-planning", ...overrides });
}

describe("lastPlanningFailure", () => {
  it("traces a cascaded window back to the window that actually failed", () => {
    const failed = planning({
      task_id: "w2",
      status: "failed",
      queued_at: "2026-09-30T10:01:00Z",
      error_code: "text_output_truncated",
      error_message: "文本模型 my-llm 的输出超出了最大输出长度，内容不完整",
      error_params: { provider_id: "custom-3", model: "my-llm", custom_model: true },
    });
    const cascaded = planning({
      task_id: "w3",
      resource_id: "episode-planning-next",
      status: "failed",
      queued_at: "2026-09-30T10:02:00Z",
      error_code: "cascade_blocked_dependency",
      error_params: { dependency_task_id: "w2" },
    });
    const finished = planning({ task_id: "w1", status: "succeeded", queued_at: "2026-09-30T10:00:00Z" });

    expect(lastPlanningFailure([cascaded, finished, failed], "proj")).toEqual({
      code: "text_output_truncated",
      message: "文本模型 my-llm 的输出超出了最大输出长度，内容不完整",
      truncated: { providerId: "custom-3", model: "my-llm", custom: true },
    });
  });

  it("forgets an old failure once a later planning run has started", () => {
    const failed = planning({ task_id: "old", status: "failed", queued_at: "2026-09-30T10:00:00Z", error_code: "x" });
    const rerun = planning({ task_id: "new", status: "running", queued_at: "2026-09-30T11:00:00Z" });

    expect(lastPlanningFailure([failed, rerun], "proj")).toBeNull();
  });
});

describe("customModelSettingsPath", () => {
  it("opens the custom provider form at the model, and has nowhere to go for a built-in provider", () => {
    expect(customModelSettingsPath("custom-3", "my/llm")).toBe(
      "/app/settings?section=providers&custom=3&model=my%2Fllm",
    );
    expect(customModelSettingsPath("gemini-aistudio", "gemini-3-pro")).toBeNull();
  });
});
