import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { GetSystemConfigResponse, ProviderInfo } from "@/types";
import { useConfigStatusStore } from "./config-status-store";
import { useEndpointCatalogStore } from "./endpoint-catalog-store";

function makeConfigResponse(overrides?: Partial<GetSystemConfigResponse["settings"]>): GetSystemConfigResponse {
  return {
    settings: {
      default_video_backend: "gemini/veo-3",
      default_image_backend: "gemini/imagen-4",
      default_text_backend: "",
      text_backend_simple: "",
      text_backend_complex: "",
      video_generate_audio: true,
      video_poll_timeout_seconds: 3600,
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
      credential_count: 0,
      models: {},
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
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());
  });

  it("reports one issue per missing required media type", async () => {
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders());
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    await useConfigStatusStore.getState().fetch();

    const { issues, initialized, isEmbeddedAgentConfigured } = useConfigStatusStore.getState();
    expect(initialized).toBe(true);
    expect(isEmbeddedAgentConfigured).toBe(false);
    expect(issues.find((i) => i.key === "anthropic")).toBeUndefined();
    expect(issues.find((i) => i.key === "no-video-provider")).toBeTruthy();
    expect(issues.find((i) => i.key === "no-image-provider")).toBeTruthy();
    expect(issues.find((i) => i.key === "no-text-provider")).toBeTruthy();
    expect(issues).toHaveLength(3);
  });

  it("reports no issues when all configured", async () => {
    vi.spyOn(API, "getProviders").mockResolvedValue(
      makeProviders([{ id: "gemini", display_name: "Google Gemini", status: "ready", media_types: ["image", "video", "text"], capabilities: [], credential_count: 1, models: {} }]),
    );
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: true, masked: "sk-ant-***" } }),
    );

    await useConfigStatusStore.getState().fetch();

    const { issues, isComplete, isEmbeddedAgentConfigured } = useConfigStatusStore.getState();
    expect(issues).toHaveLength(0);
    expect(isComplete).toBe(true);
    expect(isEmbeddedAgentConfigured).toBe(true);
  });

  it("allows fetch to retry after a transient error", async () => {
    vi.spyOn(API, "getProviders")
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce(makeProviders());
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    await useConfigStatusStore.getState().fetch();
    expect(useConfigStatusStore.getState().initialized).toBe(false);

    await useConfigStatusStore.getState().fetch();

    expect(API.getProviders).toHaveBeenCalledTimes(2);
    expect(useConfigStatusStore.getState().initialized).toBe(true);
    expect(useConfigStatusStore.getState().issues.length).toBeGreaterThan(0);
  });

  it("resets initialized when a later refresh fails, so stale capabilities are not trusted", async () => {
    vi.spyOn(API, "getProviders")
      .mockResolvedValueOnce(
        makeProviders([
          {
            id: "dashscope",
            display_name: "DashScope",
            status: "ready",
            media_types: ["image", "video", "text", "audio"],
            capabilities: [],
            credential_count: 1,
            models: {},
          },
        ]),
      )
      .mockRejectedValueOnce(new Error("temporary failure"));
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    await useConfigStatusStore.getState().fetch();
    expect(useConfigStatusStore.getState().initialized).toBe(true);
    expect(useConfigStatusStore.getState().hasMediaType("audio")).toBe(true);

    await useConfigStatusStore.getState().refresh();

    // Failed refreshes become uninitialized with empty capability sets, preventing stale consumer reads.
    expect(useConfigStatusStore.getState().initialized).toBe(false);
    expect(useConfigStatusStore.getState().availableMediaTypes).toEqual([]);
    expect(useConfigStatusStore.getState().isEmbeddedAgentConfigured).toBe(false);
  });

  it("detects audio capability from an enabled custom provider model", async () => {
    vi.spyOn(API, "getProviders").mockResolvedValue(
      makeProviders([
        {
          id: "gemini",
          display_name: "Google Gemini",
          status: "ready",
          media_types: ["image", "video", "text"],
          capabilities: [],
          credential_count: 1,
          models: {},
        },
      ]),
    );
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({
      providers: [
        {
          id: 1,
          display_name: "Local TTS",
          discovery_format: "openai",
          base_url: "http://localhost:8000/v1",
          api_key_masked: "sk-***",
          created_at: "2026-01-01T00:00:00Z",
          image_max_workers: null,
          video_max_workers: null,
          audio_max_workers: null,
          models: [
            {
              id: 1,
              model_id: "tts-1",
              display_name: "tts-1",
              endpoint: "openai-tts",
              is_default: true,
              is_enabled: true,
              resolution: null,
              multiplier: 1,
              entry_binding: null,
              matched_entry: null,
              system_capabilities: null,
              capability_overrides: null,
              path_overrides: null,
              requests: [],
            },
          ],
        },
      ],
    });
    // Initialized catalogs make getConfigStatus fetch() short-circuit; endpoint→mediaType mappings come from state.
    useEndpointCatalogStore.setState({
      initialized: true,
      endpointToMediaType: { "openai-tts": "audio" },
    });

    await useConfigStatusStore.getState().fetch();

    const state = useConfigStatusStore.getState();
    expect(state.hasMediaType("audio")).toBe(true);
    expect(state.issues).toHaveLength(0);
  });

  it("exposes hasMediaType for audio without flagging it as a config issue", async () => {
    vi.spyOn(API, "getProviders").mockResolvedValue(
      makeProviders([
        {
          id: "dashscope",
          display_name: "DashScope",
          status: "ready",
          media_types: ["image", "video", "text", "audio"],
          capabilities: [],
          credential_count: 1,
          models: {},
        },
      ]),
    );
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    await useConfigStatusStore.getState().fetch();

    expect(useConfigStatusStore.getState().hasMediaType("audio")).toBe(true);
    expect(useConfigStatusStore.getState().issues).toHaveLength(0);
  });

  it("reports audio unavailable when no ready provider supports it, still without an issue entry", async () => {
    vi.spyOn(API, "getProviders").mockResolvedValue(
      makeProviders([
        {
          id: "gemini",
          display_name: "Google Gemini",
          status: "ready",
          media_types: ["image", "video", "text"],
          capabilities: [],
          credential_count: 1,
          models: {},
        },
      ]),
    );
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    await useConfigStatusStore.getState().fetch();

    expect(useConfigStatusStore.getState().hasMediaType("audio")).toBe(false);
    // Audio is optional; absence never adds issues/red dots.
    expect(useConfigStatusStore.getState().issues).toHaveLength(0);
  });

  it("coalesces a refresh requested while one is in flight instead of dropping it", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let providersCall = 0;
    vi.spyOn(API, "getProviders").mockImplementation(async () => {
      providersCall += 1;
      if (providersCall === 1) {
        await gate; // Hold initial refresh in flight, simulating post-save refresh during loading.
        return makeProviders(); // Old data: unready, with issues.
      }
      return makeProviders([
        {
          id: "gemini",
          display_name: "Google Gemini",
          status: "ready",
          media_types: ["image", "video", "text"],
          capabilities: [],
          credential_count: 1,
          models: {},
        },
      ]); // New data: all ready, no issues.
    });
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    const store = useConfigStatusStore.getState();
    const first = store.refresh();
    const second = store.refresh(); // loading guards must merge, never discard.
    expect(useConfigStatusStore.getState().pendingRefresh).toBe(true);

    release();
    await first; // Initial and merged follow-up refreshes both complete here.
    await second;

    // Follow-up really ran (getProviders twice) and latest data persisted.
    expect(API.getProviders).toHaveBeenCalledTimes(2);
    expect(useConfigStatusStore.getState().issues).toHaveLength(0);
    expect(useConfigStatusStore.getState().isComplete).toBe(true);
    expect(useConfigStatusStore.getState().pendingRefresh).toBe(false);
  });
});
