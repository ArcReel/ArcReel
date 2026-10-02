import { describe, expect, it } from "vitest";

import { ApiRequestError } from "@/api/errors";
import { makeTask } from "@/test/factories";

import { customModelSettingsPath, outputTruncationOf, outputTruncationOfError } from "./output-truncation";

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

describe("outputTruncationOfError", () => {
  const problem = (params: Record<string, unknown>) => ({ code: "text_output_truncated", detail: "x", params });

  it("reads the model out of a truncated request failure, and ignores other failures", () => {
    const truncated = new ApiRequestError(
      "truncated",
      problem({ provider_id: "custom-3", model: "my-llm", custom_model: true }),
      422,
    );
    expect(outputTruncationOfError(truncated)).toEqual({ providerId: "custom-3", model: "my-llm", custom: true });
    expect(
      outputTruncationOfError(new ApiRequestError("t", problem({ provider_id: "openai", model: "gpt" }), 422)),
    ).toEqual({ providerId: "openai", model: "gpt", custom: false });
    expect(outputTruncationOfError(new ApiRequestError("t", { code: "other", params: {} }, 422))).toBeNull();
    expect(outputTruncationOfError(new ApiRequestError("t", problem({ model: "gpt" }), 422))).toBeNull();
    expect(outputTruncationOfError(new ApiRequestError("t"))).toBeNull();
    expect(outputTruncationOfError(new Error("boom"))).toBeNull();
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
