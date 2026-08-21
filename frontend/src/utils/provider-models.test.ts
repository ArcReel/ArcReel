import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { ProviderInfo } from "@/types";
import {
  constrainDurations,
  getCustomProviderModels,
  getProviderModels,
  lookupCatalogVideoAudio,
  lookupDurationConstraints,
  lookupProjectVideoResolution,
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
        duration_resolution_constraints: {},
        resolutions: ["720p", "1080p"],
        audio_track: "controllable",
        reference_route_audio_track: "controllable",
        voice_consistency: "soft",
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

describe("lookupProjectVideoResolution", () => {
  const BACKEND = "gemini-aistudio/veo-3.1-generate-preview";

  it("读 model_settings 的 provider/model 复合键", () => {
    expect(
      lookupProjectVideoResolution({ model_settings: { [BACKEND]: { resolution: "4K" } } }, BACKEND),
    ).toBe("4K");
  });

  // 旧项目的分辨率存在 video_model_settings 下、键是裸 model_id；不回退会让老项目的
  // 时长候选按「未设分辨率」处理，选到该分辨率下必然被拒的时长。
  it("回退 legacy video_model_settings 的裸 model_id 键", () => {
    expect(
      lookupProjectVideoResolution(
        { video_model_settings: { "veo-3.1-generate-preview": { resolution: "1080p" } } },
        BACKEND,
      ),
    ).toBe("1080p");
  });

  it("新键优先于 legacy", () => {
    expect(
      lookupProjectVideoResolution(
        {
          model_settings: { [BACKEND]: { resolution: "720p" } },
          video_model_settings: { "veo-3.1-generate-preview": { resolution: "1080p" } },
        },
        BACKEND,
      ),
    ).toBe("720p");
  });

  // null 表示用户未选档位：执行期省略 resolution 参数、供应商按自己的默认档位处理，
  // 该档位下全集本就合法，故不替用户假定档位（返回 null → 不收窄）。
  it("未配置 / 缺项目 / 空后端一律 null", () => {
    expect(lookupProjectVideoResolution({}, BACKEND)).toBeNull();
    expect(lookupProjectVideoResolution({ model_settings: {} }, BACKEND)).toBeNull();
    expect(lookupProjectVideoResolution(null, BACKEND)).toBeNull();
    expect(lookupProjectVideoResolution({ model_settings: { [BACKEND]: { resolution: "4K" } } }, "")).toBeNull();
    expect(
      lookupProjectVideoResolution({ model_settings: { [BACKEND]: { resolution: null } } }, BACKEND),
    ).toBeNull();
  });

  // 新键为空值按「未配置」处理并继续回退 legacy，与后端 `_resolution_from_project` 及保存期的
  // legacy 迁移（`_migrate_legacy_resolution_on_save` 只在 resolution 有值时清理）同口径。
  // 三处必须描述同一套语义，否则同一份 project.json 会让工作台呈现执行期实际不接受的时长。
  it("新键为空值时回退 legacy，与后端读取口径一致", () => {
    expect(
      lookupProjectVideoResolution(
        {
          model_settings: { [BACKEND]: { resolution: null } },
          video_model_settings: { "veo-3.1-generate-preview": { resolution: "1080p" } },
        },
        BACKEND,
      ),
    ).toBe("1080p");
  });

  // 后端 `_resolution_from_project` 对新键与 legacy 两层都用真值判断，只归一新键会让
  // 「空值按未配置处理」在 legacy 上失效。
  it("legacy 侧的空串同样归一为 null", () => {
    expect(
      lookupProjectVideoResolution(
        {
          model_settings: { [BACKEND]: { resolution: null } },
          video_model_settings: { "veo-3.1-generate-preview": { resolution: "" } },
        },
        BACKEND,
      ),
    ).toBeNull();
  });
});
