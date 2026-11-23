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

  it("getCustomProviderModels re-fetches on every call (no persistent cache)", async () => {
    const spy = vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    await getCustomProviderModels();
    await getCustomProviderModels();

    expect(spy).toHaveBeenCalledTimes(2);
  });

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

  it("returns null when the custom model declaration is unavailable", () => {
    expect(lookupCatalogVideoAudio(VEO_PROVIDERS, "custom-3/my-model")).toBeNull();
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

  it("reads the reference-route declaration for r2v", () => {
    expect(lookupVideoAudioControl(PROVIDERS, "gemini-aistudio/route-split", "i2v")).toBe("controllable");
    expect(lookupVideoAudioControl(PROVIDERS, "gemini-aistudio/route-split", "r2v")).toBe("always_off");
  });

  it("returns null without the custom model declaration", () => {
    expect(lookupVideoAudioControl(PROVIDERS, "custom-3/my-model", "i2v")).toBeNull();
    expect(lookupVideoAudioControl(PROVIDERS, "custom-3/my-model", "r2v")).toBeNull();
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
      isKnown: true,
    });
  });
});

describe("custom video audio declarations", () => {
  const custom = [{ id: 3, models: [
    { model_id: "fixed", audio_track: "always_on", reference_route_audio_track: "always_off", voice_consistency: "soft" },
    { model_id: "silent", audio_track: "always_off", reference_route_audio_track: "always_off", voice_consistency: "none" },
  ] }] as unknown as CustomProviderInfo[];
  it("uses the effective server declaration on each route", () => {
    expect(lookupVideoAudioControl([], "custom-3/fixed", "i2v", custom)).toBe("always_on");
    expect(lookupVideoAudioControl([], "custom-3/fixed", "r2v", custom)).toBe("always_off");
    expect(lookupCatalogVideoAudio([], "custom-3/silent", custom)).toEqual({ hasAudioTrack: false, voiceConsistency: "none" });
  });
});
