import { describe, expect, it } from "vitest";

import { makeTask } from "@/test/factories";

import { customModelSettingsPath, outputTruncationOf } from "./output-truncation";

describe("outputTruncationOf", () => {
  it("reads the model out of a truncated text task, and ignores other failures", () => {
    const truncated = makeTask({
      status: "failed",
      error_code: "text_output_truncated",
      error_params: { provider_id: "custom-3", model: "my-llm", custom_model: true },
    });
    expect(outputTruncationOf(truncated)).toEqual({ providerId: "custom-3", model: "my-llm", custom: true });
    expect(
      outputTruncationOf({ error_code: "text_output_truncated", error_params: { provider_id: "openai", model: "gpt" } }),
    ).toEqual({ providerId: "openai", model: "gpt", custom: false });
    expect(outputTruncationOf({ error_code: "generation_refused", error_params: {} })).toBeNull();
    expect(outputTruncationOf({ error_code: "text_output_truncated" })).toBeNull();
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
