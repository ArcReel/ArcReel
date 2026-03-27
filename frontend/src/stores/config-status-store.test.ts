import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { GetSystemConfigResponse, ProviderInfo } from "@/types";
import { useConfigStatusStore } from "./config-status-store";

function makeConfigResponse(overrides?: Partial<GetSystemConfigResponse["settings"]>): GetSystemConfigResponse {
  return {
    settings: {
      default_video_backend: "gemini/veo-3",
      default_image_backend: "gemini/imagen-4",
      default_text_backend: "",
      text_backend_script: "",
      text_backend_overview: "",
      text_backend_style: "",
      video_generate_audio: true,
      anthropic_api_key: { is_set: false, masked: null },
      anthropic_base_url: "",
      anthropic_model: "",
      anthropic_default_haiku_model: "",
      anthropic_default_opus_model: "",
      anthropic_default_sonnet_model: "",
      claude_code_subagent_model: "",
      agent_session_cleanup_delay_seconds: 300,
      agent_max_concurrent_sessions: 5,
      ...overrides,
    },
    options: {
      video_backends: ["gemini/veo-3"],
      image_backends: ["gemini/imagen-4"],
      text_backends: [],
    },
  };
}

function makeProviders(overrides?: Partial<ProviderInfo>[]): { providers: ProviderInfo[] } {
  const defaults: ProviderInfo[] = [
    {
      id: "gemini",
      display_name: "Google Gemini",
      description: "Google Gemini API",
      status: "unconfigured",
      media_types: ["image", "video"],
      capabilities: [],
      configured_keys: [],
      missing_keys: ["api_key"],
    },
  ];
  if (overrides) {
    return { providers: overrides.map((o, i) => ({ ...defaults[i], ...o })) };
  }
  return { providers: defaults };
}

describe("config-status-store", () => {
  beforeEach(() => {
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("reports anthropic and provider issues when both unconfigured", async () => {
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders());
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());

    await useConfigStatusStore.getState().fetch();

    const { issues, initialized } = useConfigStatusStore.getState();
    expect(initialized).toBe(true);
    // anthropic issue + no ready provider for each media type
    expect(issues.find((i) => i.key === "anthropic")).toBeTruthy();
    expect(issues.find((i) => i.key === "no-video-provider")).toBeTruthy();
    expect(issues.find((i) => i.key === "no-image-provider")).toBeTruthy();
    expect(issues.find((i) => i.key === "no-text-provider")).toBeTruthy();
    expect(issues).toHaveLength(4);
  });

  it("reports no issues when all configured", async () => {
    vi.spyOn(API, "getProviders").mockResolvedValue(
      makeProviders([{ id: "gemini", display_name: "Google Gemini", status: "ready", media_types: ["image", "video", "text"], capabilities: [], configured_keys: ["api_key"], missing_keys: [] }]),
    );
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: true, masked: "sk-ant-***" } }),
    );

    await useConfigStatusStore.getState().fetch();

    const { issues, isComplete } = useConfigStatusStore.getState();
    expect(issues).toHaveLength(0);
    expect(isComplete).toBe(true);
  });

  it("allows fetch to retry after a transient error", async () => {
    vi.spyOn(API, "getProviders")
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce(makeProviders());
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());

    await useConfigStatusStore.getState().fetch();
    expect(useConfigStatusStore.getState().initialized).toBe(false);

    await useConfigStatusStore.getState().fetch();

    expect(API.getProviders).toHaveBeenCalledTimes(2);
    expect(useConfigStatusStore.getState().initialized).toBe(true);
    expect(useConfigStatusStore.getState().issues.length).toBeGreaterThan(0);
  });
});
