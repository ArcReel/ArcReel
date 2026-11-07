import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { ProviderInfo } from "@/types";
import {
  catalogDurations,
  getCustomProviderModels,
  getProviderModels,
  lookupCatalogVideoAudio,
  lookupVideoAudioControl,
} from "./provider-models";

describe("provider-models fetchers", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // Provider settings change, for example edited supported_durations; frontend must never persistently cache them:
  // refetch per consumption, avoiding stale project-setting/wizard durations (ADR 0035).
  it("getCustomProviderModels re-fetches on every call (no persistent cache)", async () => {
    const spy = vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    await getCustomProviderModels();
    await getCustomProviderModels();

    expect(spy).toHaveBeenCalledTimes(2);
  });

  // Same for built-ins: stale status/enabled caches hide newly configured providers.
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

  // Custom catalogs lack per-model declarations; match server `_resolve_video_caps_for_model` assuming
  // audio soft, never unknown/null.
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
        // Kling v3-omni shape: image-to-video subpaths include switches, reference-video native schemas omit them.
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

  // Per-path lookup prevents reference-to-video controls necessarily discarded at execution (audio-enabled users receiving silent outputs).
  it("reads the reference-route declaration for r2v", () => {
    expect(lookupVideoAudioControl(PROVIDERS, "gemini-aistudio/route-split", "i2v")).toBe("controllable");
    expect(lookupVideoAudioControl(PROVIDERS, "gemini-aistudio/route-split", "r2v")).toBe("always_off");
  });

  // Custom providers lack per-model audio declarations; do not tighten without signals, leaving toggles controllable.
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
    // Without overrides, durations come from catalog entries unavailable to frontends.
    expect(catalogDurations(VEO_PROVIDERS, custom, "custom-3/plain-video")).toBeNull();
  });
});
