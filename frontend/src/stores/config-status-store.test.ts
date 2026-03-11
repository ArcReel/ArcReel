import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { GetSystemConfigResponse } from "@/types";
import { getConfigIssues, useConfigStatusStore } from "./config-status-store";

function makeResponse(): GetSystemConfigResponse {
  return {
    config: {
      image_backend: "aistudio",
      video_backend: "aistudio",
      image_model: "gemini-3.1-flash-image-preview",
      video_model: "veo-3.1-generate-001",
      video_generate_audio: true,
      video_generate_audio_effective: true,
      video_generate_audio_editable: true,
      rate_limit: {
        image_rpm: 15,
        video_rpm: 10,
        request_gap_seconds: 3,
      },
      performance: {
        image_max_workers: 3,
        video_max_workers: 2,
      },
      gemini_api_key: {
        is_set: false,
        masked: null,
        source: "unset",
      },
      gemini_base_url: {
        value: null,
        source: "unset",
      },
      anthropic_api_key: {
        is_set: false,
        masked: null,
        source: "unset",
      },
      anthropic_base_url: {
        value: null,
        source: "unset",
      },
      anthropic_model: {
        value: null,
        source: "unset",
      },
      anthropic_default_haiku_model: {
        value: null,
        source: "unset",
      },
      anthropic_default_opus_model: {
        value: null,
        source: "unset",
      },
      anthropic_default_sonnet_model: {
        value: null,
        source: "unset",
      },
      claude_code_subagent_model: {
        value: null,
        source: "unset",
      },
      vertex_gcs_bucket: {
        value: null,
        source: "unset",
      },
      vertex_credentials: {
        is_set: false,
        filename: null,
        project_id: null,
      },
    },
    options: {
      image_models: ["gemini-3.1-flash-image-preview"],
      video_models: ["veo-3.1-generate-001"],
    },
  };
}

describe("config-status-store", () => {
  beforeEach(() => {
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("merges shared media credential warnings into one issue", () => {
    const response = makeResponse();

    expect(getConfigIssues(response.config)).toEqual([
      {
        key: "anthropic",
        tab: "agent",
        label: "ArcReel 智能体 API Key（Anthropic）未配置",
      },
      {
        key: "media-aistudio",
        tab: "media",
        label: "AI 生图/生视频 API Key（Gemini AI Studio）未配置",
      },
    ]);
  });

  it("allows fetch to retry after a transient error", async () => {
    vi.spyOn(API, "getSystemConfig")
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce(makeResponse());

    await useConfigStatusStore.getState().fetch();
    expect(useConfigStatusStore.getState().initialized).toBe(false);

    await useConfigStatusStore.getState().fetch();

    expect(API.getSystemConfig).toHaveBeenCalledTimes(2);
    expect(useConfigStatusStore.getState().initialized).toBe(true);
    expect(useConfigStatusStore.getState().issues).toHaveLength(2);
  });
});
