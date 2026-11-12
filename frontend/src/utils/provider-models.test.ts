import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { CustomProviderInfo, ProviderInfo } from "@/types";
import {
  catalogDurations,
  getCustomProviderModels,
  getProviderModels,
  lookupCatalogVideoAudio,
  lookupQualities,
  lookupResolutions,
  lookupVideoAudioControl,
} from "./provider-models";

describe("provider-models fetchers", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // 供应商配置可变（用户在设置页编辑模型 supported_durations），前端不得持久缓存它——
  // 每次消费都必须重拉，否则项目设置/向导读到的时长集会陈旧（ADR 0035）。
  it("getCustomProviderModels re-fetches on every call (no persistent cache)", async () => {
    const spy = vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    await getCustomProviderModels();
    await getCustomProviderModels();

    expect(spy).toHaveBeenCalledTimes(2);
  });

  // 内置供应商缓存同理：status/enabled 等可变项陈旧会让模型选择器漏显刚配好的供应商。
  it("getProviderModels re-fetches on every call (no persistent cache)", async () => {
    const spy = vi.spyOn(API, "getProviders").mockResolvedValue({ providers: [] });

    await getProviderModels();
    await getProviderModels();

    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("forwards the caller's AbortSignal to the requests", async () => {
    const providers = vi.spyOn(API, "getProviders").mockResolvedValue({ providers: [] });
    const custom = vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    const { signal } = new AbortController();

    await getProviderModels({ signal });
    await getCustomProviderModels({ signal });

    expect(providers).toHaveBeenCalledWith({ signal });
    expect(custom).toHaveBeenCalledWith({ signal });
  });
});

const VEO_PROVIDERS: ProviderInfo[] = [
  {
    id: "gemini-aistudio",
    display_name: "AI Studio",
    description: "",
    status: "ready",
    media_types: ["video"],
    capabilities: [],
    credential_count: 0,
    models: {
      "veo-3.1-generate-preview": {
        display_name: "Veo 3.1",
        media_type: "video",
        capabilities: [],
        default: false,
        supported_durations: [8, 4, 6],
        resolutions: ["720p", "1080p", "4k"],
        qualities: [],
        audio_track: "controllable",
        reference_route_audio_track: "controllable",
        voice_consistency: "soft",
      },
      "seedance-like": {
        display_name: "无约束模型",
        media_type: "video",
        capabilities: [],
        default: false,
        supported_durations: [5, 8, 10],
        resolutions: ["720p", "1080p"],
        qualities: [],
        audio_track: "controllable",
        reference_route_audio_track: "controllable",
        voice_consistency: "soft",
      },
    },
  },
];

describe("lookupCatalogVideoAudio", () => {
  it("derives hasAudioTrack / voiceConsistency off the model declaration", () => {
    expect(lookupCatalogVideoAudio(VEO_PROVIDERS, "gemini-aistudio/veo-3.1-generate-preview")).toEqual({
      hasAudioTrack: true,
      voiceConsistency: "soft",
    });
  });

  it("returns null for unknown model, unknown provider and malformed strings", () => {
    expect(lookupCatalogVideoAudio(VEO_PROVIDERS, "gemini-aistudio/unknown")).toBeNull();
    expect(lookupCatalogVideoAudio(VEO_PROVIDERS, "bogus-provider/whatever")).toBeNull();
    expect(lookupCatalogVideoAudio(VEO_PROVIDERS, "no-slash")).toBeNull();
  });

  // 自定义供应商目录无逐模型声明，与服务端 `_resolve_video_caps_for_model` 同口径固定假定
  // 有声（soft）——不是「未知」，故不返回 null。
  it("assumes audible/soft for custom backends (no per-model declaration, same default as the server)", () => {
    expect(lookupCatalogVideoAudio(VEO_PROVIDERS, "custom-3/my-model")).toEqual({
      hasAudioTrack: true,
      voiceConsistency: "soft",
    });
  });
});

describe("lookupVideoAudioControl", () => {
  const PROVIDERS: ProviderInfo[] = [
    {
      ...VEO_PROVIDERS[0],
      models: {
        controllable: {
          ...VEO_PROVIDERS[0].models["seedance-like"],
          audio_track: "controllable",
          reference_route_audio_track: "controllable",
        },
        "always-on": {
          ...VEO_PROVIDERS[0].models["seedance-like"],
          audio_track: "always_on",
          reference_route_audio_track: "always_on",
        },
        "always-off": {
          ...VEO_PROVIDERS[0].models["seedance-like"],
          audio_track: "always_off",
          reference_route_audio_track: "always_off",
        },
        // 可灵 v3-omni 的形状：图生子路径带音轨开关，参考生子路径的原生 schema 不含该字段。
        "route-split": {
          ...VEO_PROVIDERS[0].models["seedance-like"],
          audio_track: "controllable",
          reference_route_audio_track: "always_off",
        },
      },
    },
  ];

  it.each([
    ["controllable", "controllable"],
    ["always-on", "always_on"],
    ["always-off", "always_off"],
  ])("maps %s to %s on both routes", (modelId, expected) => {
    expect(lookupVideoAudioControl(PROVIDERS, `gemini-aistudio/${modelId}`, "i2v")).toBe(expected);
    expect(lookupVideoAudioControl(PROVIDERS, `gemini-aistudio/${modelId}`, "r2v")).toBe(expected);
  });

  // 逐路径取值：按模型取会让参考生视频放行一个执行期必然被丢弃的开关（用户开了音频拿到无声成片）。
  it("reads the reference-route declaration for r2v", () => {
    expect(lookupVideoAudioControl(PROVIDERS, "gemini-aistudio/route-split", "i2v")).toBe("controllable");
    expect(lookupVideoAudioControl(PROVIDERS, "gemini-aistudio/route-split", "r2v")).toBe("always_off");
  });

  // 自定义供应商无逐模型音轨声明：无信号不收紧，开关保持可控。
  it("keeps custom backends controllable", () => {
    expect(lookupVideoAudioControl(PROVIDERS, "custom-3/my-model", "i2v")).toBe("controllable");
    expect(lookupVideoAudioControl(PROVIDERS, "custom-3/my-model", "r2v")).toBe("controllable");
  });

  it("returns null for unknown model, unknown provider and malformed strings", () => {
    expect(lookupVideoAudioControl(PROVIDERS, "gemini-aistudio/unknown", "i2v")).toBeNull();
    expect(lookupVideoAudioControl(PROVIDERS, "bogus-provider/whatever", "i2v")).toBeNull();
    expect(lookupVideoAudioControl(PROVIDERS, "no-slash", "i2v")).toBeNull();
  });
});

describe("catalogDurations", () => {
  it("returns the declared full set in ascending order, untouched by any linkage constraint", () => {
    expect(catalogDurations(VEO_PROVIDERS, [], "gemini-aistudio/veo-3.1-generate-preview")).toEqual([4, 6, 8]);
  });

  it("returns null for unknown model / provider, empty backend and models without durations", () => {
    expect(catalogDurations(VEO_PROVIDERS, [], "gemini-aistudio/unknown")).toBeNull();
    expect(catalogDurations(VEO_PROVIDERS, [], "bogus/whatever")).toBeNull();
    expect(catalogDurations(VEO_PROVIDERS, [], "")).toBeNull();
  });

  it("reads custom backends off the model's duration override", () => {
    const custom = [
      {
        id: 3,
        display_name: "Relay",
        models: [
          { model_id: "relay-video", display_name: "Relay video", capability_overrides: { durations: [10, 5] } },
          { model_id: "plain-video", display_name: "Plain video", capability_overrides: null },
        ],
      },
    ] as unknown as Parameters<typeof catalogDurations>[1];
    expect(catalogDurations(VEO_PROVIDERS, custom, "custom-3/relay-video")).toEqual([5, 10]);
    // 没有覆盖时时长由模型条目给出，前端查不到
    expect(catalogDurations(VEO_PROVIDERS, custom, "custom-3/plain-video")).toBeNull();
  });
});

describe("lookupQualities", () => {
  const providers: ProviderInfo[] = [
    {
      ...VEO_PROVIDERS[0],
      id: "openai",
      models: {
        "gpt-image-2": {
          ...VEO_PROVIDERS[0].models["seedance-like"],
          media_type: "image",
          qualities: ["medium", "low", "high"],
        },
      },
    },
  ];
  const custom = [
    { id: 4, models: [{ model_id: "gpt-image-2", qualities: ["high"] }] },
  ] as unknown as CustomProviderInfo[];

  it("returns the builtin model's qualities in the order the server sent them", () => {
    expect(lookupQualities(providers, "openai/gpt-image-2")).toEqual(["medium", "low", "high"]);
  });

  it("reads a custom provider's model row", () => {
    expect(lookupQualities(providers, "custom-4/gpt-image-2", custom)).toEqual(["high"]);
  });

  it("returns an empty list for unknown models, providers and malformed strings", () => {
    expect(lookupQualities(providers, "openai/unknown")).toEqual([]);
    expect(lookupQualities(providers, "nobody/gpt-image-2")).toEqual([]);
    expect(lookupQualities(providers, "custom-4/gpt-image-2")).toEqual([]);
    expect(lookupQualities(providers, "")).toEqual([]);
  });
});

describe("lookupResolutions", () => {
  const providers: ProviderInfo[] = [
    {
      ...VEO_PROVIDERS[0],
      id: "openai",
      models: {
        "gpt-image-legacy": {
          ...VEO_PROVIDERS[0].models["seedance-like"],
          media_type: "image",
          resolutions: ["512px", "1K", "2K"],
        },
      },
    },
  ];
  const custom = [
    {
      id: 4,
      models: [
        { model_id: "img", endpoint: "openai-images", resolution: "512px" },
        { model_id: "vid", endpoint: "openai-video", resolution: null },
      ],
    },
  ] as unknown as CustomProviderInfo[];
  const media = { "openai-images": "image", "openai-video": "video" } as const;

  it("suggests no 512px for a custom image model", () => {
    const { options, isCustom } = lookupResolutions(providers, "custom-4/img", custom, media);
    expect(isCustom).toBe(true);
    expect(options).toEqual(["1K", "2K", "4K"]);
  });

  it("leaves the custom video suggestions unchanged", () => {
    expect(lookupResolutions(providers, "custom-4/vid", custom, media).options).toEqual([
      "480p",
      "720p",
      "1080p",
      "4K",
    ]);
  });

  it("keeps the options a builtin model declares, 512px included", () => {
    expect(lookupResolutions(providers, "openai/gpt-image-legacy")).toEqual({
      options: ["512px", "1K", "2K"],
      isCustom: false,
    });
  });
});
