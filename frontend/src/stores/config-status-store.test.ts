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
  });

  it("reports anthropic and provider issues when both unconfigured", async () => {
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders());
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
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
      makeProviders([{ id: "gemini", display_name: "Google Gemini", status: "ready", media_types: ["image", "video", "text"], capabilities: [], configured_keys: ["api_key"], missing_keys: [], models: {} }]),
    );
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
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
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());

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
            configured_keys: ["api_key"],
            missing_keys: [],
            models: {},
          },
        ]),
      )
      .mockRejectedValueOnce(new Error("temporary failure"));
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: true, masked: "sk-ant-***" } }),
    );

    await useConfigStatusStore.getState().fetch();
    expect(useConfigStatusStore.getState().initialized).toBe(true);
    expect(useConfigStatusStore.getState().hasMediaType("audio")).toBe(true);

    await useConfigStatusStore.getState().refresh();

    // 刷新失败后回到未初始化且清空能力集：任何消费方都不再读到过期数据
    expect(useConfigStatusStore.getState().initialized).toBe(false);
    expect(useConfigStatusStore.getState().availableMediaTypes).toEqual([]);
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
          configured_keys: ["api_key"],
          missing_keys: [],
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
          models: [
            {
              id: 1,
              model_id: "tts-1",
              display_name: "tts-1",
              endpoint: "openai-tts",
              is_default: true,
              is_enabled: true,
              price_unit: null,
              price_input: null,
              price_output: null,
              currency: null,
              supported_durations: null,
              resolution: null,
            },
          ],
        },
      ],
    });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: true, masked: "sk-ant-***" } }),
    );
    // catalog 已初始化时 getConfigStatus 内的 fetch() 直接短路，endpoint→mediaType 映射取自 state
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
          configured_keys: ["api_key"],
          missing_keys: [],
          models: {},
        },
      ]),
    );
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: true, masked: "sk-ant-***" } }),
    );

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
          configured_keys: ["api_key"],
          missing_keys: [],
          models: {},
        },
      ]),
    );
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: true, masked: "sk-ant-***" } }),
    );

    await useConfigStatusStore.getState().fetch();

    expect(useConfigStatusStore.getState().hasMediaType("audio")).toBe(false);
    // audio 是可选能力,缺失不进 issues 红点
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
        await gate; // 让首次刷新停在 in-flight,模拟"保存后刷新"撞上进行中的加载
        return makeProviders(); // 旧数据:未就绪 → 有 issues
      }
      return makeProviders([
        {
          id: "gemini",
          display_name: "Google Gemini",
          status: "ready",
          media_types: ["image", "video", "text"],
          capabilities: [],
          configured_keys: ["api_key"],
          missing_keys: [],
          models: {},
        },
      ]); // 新数据:全就绪 → 无 issues
    });
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: true, masked: "sk-ant-***" } }),
    );

    const store = useConfigStatusStore.getState();
    const first = store.refresh();
    const second = store.refresh(); // 命中 loading 守卫 → 应被合并而非丢弃
    expect(useConfigStatusStore.getState().pendingRefresh).toBe(true);

    release();
    await first; // 首次刷新 + 合并补跑的尾部刷新都在此完成
    await second;

    // 补跑确实执行(getProviders 被调两次),且最终落地的是最新数据
    expect(API.getProviders).toHaveBeenCalledTimes(2);
    expect(useConfigStatusStore.getState().issues).toHaveLength(0);
    expect(useConfigStatusStore.getState().isComplete).toBe(true);
    expect(useConfigStatusStore.getState().pendingRefresh).toBe(false);
  });
});
