import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { ProviderInfo } from "@/types";
import {
  constrainDurations,
  getCustomProviderModels,
  getProviderModels,
  lookupDurationConstraints,
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
});

const VEO_PROVIDERS: ProviderInfo[] = [
  {
    id: "gemini-aistudio",
    display_name: "AI Studio",
    description: "",
    status: "ready",
    media_types: ["video"],
    capabilities: [],
    configured_keys: [],
    missing_keys: [],
    models: {
      "veo-3.1-generate-preview": {
        display_name: "Veo 3.1",
        media_type: "video",
        capabilities: [],
        default: false,
        supported_durations: [4, 6, 8],
        duration_resolution_constraints: { "1080p": [8], "4k": [8] },
        reference_image_durations: [8],
        resolutions: ["720p", "1080p", "4k"],
      },
      "seedance-like": {
        display_name: "无约束模型",
        media_type: "video",
        capabilities: [],
        default: false,
        supported_durations: [5, 8, 10],
        duration_resolution_constraints: {},
        resolutions: ["720p", "1080p"],
      },
    },
  },
];

describe("lookupDurationConstraints", () => {
  it("reads both constraint kinds off the model declaration", () => {
    const c = lookupDurationConstraints(VEO_PROVIDERS, "gemini-aistudio/veo-3.1-generate-preview");

    expect(c.byResolution).toEqual({ "1080p": [8], "4k": [8] });
    expect(c.withReferenceImages).toEqual([8]);
  });

  it("returns empty constraints for models, providers and custom backends without declarations", () => {
    expect(lookupDurationConstraints(VEO_PROVIDERS, "gemini-aistudio/seedance-like")).toEqual({
      byResolution: {},
      withReferenceImages: [],
    });
    expect(lookupDurationConstraints(VEO_PROVIDERS, "gemini-aistudio/unknown")).toEqual({
      byResolution: {},
      withReferenceImages: [],
    });
    expect(lookupDurationConstraints(VEO_PROVIDERS, "custom-3/my-model")).toEqual({
      byResolution: {},
      withReferenceImages: [],
    });
    expect(lookupDurationConstraints(VEO_PROVIDERS, "no-slash")).toEqual({
      byResolution: {},
      withReferenceImages: [],
    });
  });
});

describe("constrainDurations", () => {
  const constraints = lookupDurationConstraints(
    VEO_PROVIDERS,
    "gemini-aistudio/veo-3.1-generate-preview",
  );

  it("keeps every duration at an unconstrained resolution without reference images", () => {
    expect(constrainDurations([4, 6, 8], constraints, { resolution: "720p" })).toEqual([4, 6, 8]);
    expect(constrainDurations([4, 6, 8], constraints, { resolution: null })).toEqual([4, 6, 8]);
  });

  it.each(["1080p", "4k", "4K"])("narrows to 8s at %s", (resolution) => {
    expect(constrainDurations([4, 6, 8], constraints, { resolution })).toEqual([8]);
  });

  it("narrows to 8s on the reference-image path at any resolution", () => {
    expect(
      constrainDurations([4, 6, 8], constraints, { resolution: "720p", usesReferenceImages: true }),
    ).toEqual([8]);
  });

  it("keeps the original options when constraints would leave nothing selectable", () => {
    const contradictory = { byResolution: { "720p": [16] }, withReferenceImages: [] };
    expect(constrainDurations([4, 6, 8], contradictory, { resolution: "720p" })).toEqual([4, 6, 8]);
  });
});
