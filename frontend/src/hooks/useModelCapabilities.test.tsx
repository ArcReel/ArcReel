import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import {
  catalogDurations,
  durationOutOfRangeReason,
  narrowDurations,
  useModelCapabilities,
} from "@/hooks/useModelCapabilities";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData, ProviderInfo, VideoCapabilities } from "@/types";

const PROJECT = "demo-project";
const BACKEND = "gemini/veo-3";

function provider(overrides: Partial<ProviderInfo["models"][string]> = {}): ProviderInfo[] {
  return [
    {
      id: "gemini",
      display_name: "Gemini",
      description: "",
      status: "ready",
      media_types: ["video"],
      capabilities: [],
      configured_keys: [],
      missing_keys: [],
      models: {
        "veo-3": {
          display_name: "Veo 3",
          media_type: "video",
          capabilities: [],
          default: true,
          supported_durations: [4, 6, 8],
          duration_resolution_constraints: {},
          resolutions: ["720p", "1080p"],
          has_audio_track: true,
          voice_consistency: "soft",
          ...overrides,
        },
      },
    },
  ];
}

function caps(overrides: Partial<VideoCapabilities> = {}): VideoCapabilities {
  return {
    provider_id: "gemini",
    model: "veo-3",
    supported_durations: [5, 10],
    max_duration: 10,
    max_reference_images: 3,
    first_frame: true,
    last_frame: true,
    source: "registry",
    voice_consistency: "soft",
    ...overrides,
  };
}

/** 只含能力 key 关心的字段：集级 generation_mode 覆盖与其回退到的项目级值。 */
function projectData(episodeMode: string | null): ProjectData {
  return {
    generation_mode: "storyboard",
    episodes: [{ episode: 1, ...(episodeMode ? { generation_mode: episodeMode } : {}) }],
  } as ProjectData;
}

beforeEach(() => {
  useCapabilitiesStore.setState({ revision: 0 });
  useProjectsStore.setState({ currentProjectName: null, currentProjectData: null });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useModelCapabilities 时长维度", () => {
  it("目录能解析出候选模型时以目录为准，不等服务端", () => {
    vi.spyOn(API, "getVideoCapabilities").mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND, providers: provider() }),
    );
    // 服务端在途，时长首帧即可用：目录侧同步可得，不闪加载态。
    expect(result.current.supportedDurations).toEqual([4, 6, 8]);
    expect(result.current.rawDurations).toEqual([4, 6, 8]);
  });

  it("按分辨率联动约束收窄，rawDurations 保留收窄前全集", () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({
        videoBackend: BACKEND,
        providers: provider({ duration_resolution_constraints: { "1080p": [8] } }),
        videoResolution: "1080p",
      }),
    );
    expect(result.current.supportedDurations).toEqual([8]);
    expect(result.current.rawDurations).toEqual([4, 6, 8]);
  });

  it("按参考图路径联动约束收窄", () => {
    const { result } = renderHook(() =>
      useModelCapabilities({
        videoBackend: BACKEND,
        providers: provider({ reference_image_durations: [6] }),
        usesReferenceImages: true,
      }),
    );
    expect(result.current.supportedDurations).toEqual([6]);
  });

  it("后端未配置时退回服务端为本项目解析出的时长", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: "", providers: provider() }),
    );
    await waitFor(() => expect(result.current.supportedDurations).toEqual([5, 10]));
  });

  it("已保存后端解析不出目录时，采信服务端为实际执行模型解析出的时长", async () => {
    // 存值指向已被删除 / 禁用的自定义模型：目录查不到，服务端按执行层规则回退到默认模型，
    // 返回的正是实际会执行的模型的能力。
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({
        projectName: PROJECT,
        videoBackend: "custom/removed-model",
        providers: provider(),
      }),
    );
    await waitFor(() => expect(result.current.supportedDurations).toEqual([5, 10]));
  });

  it("走服务端回退时按服务端返回的模型查联动约束，不用传入的后端", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({
        projectName: PROJECT,
        videoBackend: "custom/removed-model",
        providers: provider({ duration_resolution_constraints: { "1080p": [10] } }),
        videoResolution: "1080p",
      }),
    );
    // 约束出自服务端解析到的 gemini/veo-3，故 5 秒被 1080p 约束收窄掉。
    await waitFor(() => expect(result.current.supportedDurations).toEqual([10]));
    expect(result.current.rawDurations).toEqual([5, 10]);
  });

  it("裸 provider 后端下 resolvedVideoBackend 给出服务端补全的 provider/model", async () => {
    // 裸 provider 是合法项目配置（服务端补全默认视频模型）。调用方按「项目为该后端保存了什么」
    // 查配置时必须用这个已解析值，拿裸值去查会把 provider ID 当成 model ID、读不到实际档位。
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: "gemini", providers: provider() }),
    );
    await waitFor(() => expect(result.current.resolvedVideoBackend).toBe("gemini/veo-3"));
  });

  it("目录能解析时 resolvedVideoBackend 即传入的后端", () => {
    vi.spyOn(API, "getVideoCapabilities").mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND, providers: provider() }),
    );
    expect(result.current.resolvedVideoBackend).toBe(BACKEND);
  });

  it("unsavedBackend 时不采用服务端回退，时长按未知处理", async () => {
    // 表单里 backend 是未保存候选：服务端返回的仍是已保存模型的时长，采信会把它摆成新候选
    // 的选项，用户能存下新模型不支持的值。
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({
        projectName: PROJECT,
        videoBackend: "ark/other-model",
        unsavedBackend: true,
        providers: [],
      }),
    );
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(result.current.rawDurations).toBeNull();
    expect(result.current.supportedDurations).toBeNull();
  });

  it("请求 key 用元组编码，字段内含分隔符也不碰撞", async () => {
    const spy = vi
      .spyOn(API, "getVideoCapabilities")
      .mockResolvedValue(caps({ supported_durations: [7] }));
    const { result, rerender } = renderHook(
      (props: { projectName: string; videoBackend: string }) => useModelCapabilities(props),
      { initialProps: { projectName: "a b", videoBackend: "c" } },
    );
    await waitFor(() => expect(result.current.rawDurations).toEqual([7]));
    spy.mockReturnValue(new Promise(() => {}));
    rerender({ projectName: "a", videoBackend: "b c" });
    // 拼接 key 下两组会撞成 "a b c"，前一组结果被当作本组已落地。
    expect(result.current.rawDurations).toBeNull();
    expect(result.current.loading).toBe(true);
  });

  it("无项目名（项目尚不存在）时只走目录，不发请求", () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({ videoBackend: BACKEND, providers: provider() }),
    );
    expect(result.current.supportedDurations).toEqual([4, 6, 8]);
    expect(spy).not.toHaveBeenCalled();
  });

  it("enabled=false 时目录与服务端都不查", () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({
        projectName: PROJECT,
        videoBackend: BACKEND,
        providers: provider(),
        enabled: false,
      }),
    );
    expect(result.current.supportedDurations).toBeNull();
    expect(result.current.rawDurations).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("useModelCapabilities 首尾帧维度", () => {
  it("取服务端生效值（含用户覆盖）", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(
      caps({ first_frame: true, last_frame: false }),
    );
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND }),
    );
    await waitFor(() => expect(result.current.lastFrame).toBe(false));
    expect(result.current.firstFrame).toBe(true);
  });

  it("查询未落地时为未知（null），不谎报不支持", () => {
    vi.spyOn(API, "getVideoCapabilities").mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND }),
    );
    expect(result.current.lastFrame).toBeNull();
    expect(result.current.loading).toBe(true);
  });

  it("查询失败时为未知（null）", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.lastFrame).toBeNull();
  });

  it("换视频后端立刻丢弃旧能力，不按过期值门控", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps({ last_frame: true }));
    const { result, rerender } = renderHook(
      (backend: string) => useModelCapabilities({ projectName: PROJECT, videoBackend: backend }),
      { initialProps: BACKEND },
    );
    await waitFor(() => expect(result.current.lastFrame).toBe(true));

    spy.mockResolvedValue(caps({ last_frame: false }));
    rerender("ark/seedance");
    // 新 key 未落地前是未知而非旧的 true。
    expect(result.current.lastFrame).toBeNull();
    await waitFor(() => expect(result.current.lastFrame).toBe(false));
  });
});

describe("useModelCapabilities voiceConsistency 维度", () => {
  it("取服务端二维派生值", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps({ voice_consistency: "native" }));
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND }),
    );
    await waitFor(() => expect(result.current.voiceConsistency).toBe("native"));
  });

  it("查询未落地时为未知（null）", () => {
    vi.spyOn(API, "getVideoCapabilities").mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND }),
    );
    expect(result.current.voiceConsistency).toBeNull();
  });
});

describe("能力查询带上候选模型", () => {
  it("把编辑中的 videoBackend 作为 video_backend 传给服务端，档位不停留在已保存模型上", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps({}));
    renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: "openai/sora-2", unsavedBackend: true }),
    );
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]?.[1]).toMatchObject({ videoBackend: "openai/sora-2" });
  });

  it("videoBackend 为空时不传该参数，服务端按已落盘配置解析", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps({}));
    renderHook(() => useModelCapabilities({ projectName: PROJECT, videoBackend: "" }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]?.[1]?.videoBackend).toBeUndefined();
  });
});

describe("useModelCapabilities 失效时机", () => {
  it("能力覆盖变更（store 失效）后自动重取，无需重新挂载或任何交互", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps({ last_frame: true }));
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND }),
    );
    await waitFor(() => expect(result.current.lastFrame).toBe(true));

    spy.mockResolvedValue(caps({ last_frame: false }));
    act(() => useCapabilitiesStore.getState().invalidate());
    await waitFor(() => expect(result.current.lastFrame).toBe(false));
  });

  it("集级 generation_mode 覆盖变更后自动重取，不等切集或重新挂载", async () => {
    // 覆盖经 PATCH /projects/{name} 写入、项目事件 SSE 刷进 store：集号没变，能力却已换桶。
    useProjectsStore.setState({ currentProjectName: PROJECT, currentProjectData: projectData(null) });
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps({ last_frame: true }));
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND, episode: 1 }),
    );
    await waitFor(() => expect(result.current.lastFrame).toBe(true));

    spy.mockResolvedValue(caps({ last_frame: false }));
    act(() => useProjectsStore.setState({ currentProjectData: projectData("reference_video") }));
    await waitFor(() => expect(result.current.lastFrame).toBe(false));
  });

  it("失效重取期间保留旧值，不闪未知态", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps({ last_frame: false }));
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND }),
    );
    await waitFor(() => expect(result.current.lastFrame).toBe(false));

    spy.mockReturnValue(new Promise(() => {}));
    act(() => useCapabilitiesStore.getState().invalidate());
    // 同一上下文的重取：旧值仍是当前最优估计，否则警告会闪一次消失。
    expect(result.current.lastFrame).toBe(false);
  });
});

describe("catalogDurations", () => {
  it("与 hook 同规则：收窄后升序返回", () => {
    expect(
      catalogDurations(provider({ supported_durations: [8, 4, 6] }), [], BACKEND),
    ).toEqual([4, 6, 8]);
  });

  it("目录查不到该模型时为 null", () => {
    expect(catalogDurations(provider(), [], "ark/unknown-model")).toBeNull();
    expect(catalogDurations(provider(), [], "")).toBeNull();
  });
});

describe("narrowDurations", () => {
  const CONSTRAINTS = { byResolution: { "1080p": [8] }, withReferenceImages: [8] };

  // 上下文按集变化（generation_mode 可被单集覆盖），而能力查询只在组件顶层做一次；
  // 收窄规则仍留在本模块，调用点不重新拼查表链路。
  it("用已取到的能力对另一份上下文再算一次收窄", () => {
    const capsIn = { rawDurations: [4, 6, 8], durationConstraints: CONSTRAINTS };
    expect(narrowDurations(capsIn, { videoResolution: "1080p" })).toEqual([8]);
    expect(narrowDurations(capsIn, { usesReferenceImages: true })).toEqual([8]);
    expect(narrowDurations(capsIn, {})).toEqual([4, 6, 8]);
    expect(narrowDurations(capsIn, { videoResolution: "720p" })).toEqual([4, 6, 8]);
  });

  it("能力未知时为 null（不谎报成空集合）", () => {
    expect(narrowDurations({ rawDurations: null, durationConstraints: CONSTRAINTS }, {})).toBeNull();
  });
});

describe("durationOutOfRangeReason", () => {
  const CONSTRAINTS = { byResolution: { "1080p": [8] }, withReferenceImages: [8] };

  it("全集就不含该值 → model", () => {
    expect(
      durationOutOfRangeReason(
        5,
        { rawDurations: [4, 6, 8], supportedDurations: [4, 6, 8], durationConstraints: CONSTRAINTS },
        {},
      ),
    ).toBe("model");
  });

  // 成因决定提示把用户引向哪里：分辨率 / 参考图两条改对应设置也能解决，不该被引去换模型。
  it("被分辨率约束收窄 → resolution", () => {
    expect(
      durationOutOfRangeReason(
        4,
        { rawDurations: [4, 6, 8], supportedDurations: [8], durationConstraints: CONSTRAINTS },
        { videoResolution: "1080p" },
      ),
    ).toBe("resolution");
  });

  it("被参考图约束收窄 → reference", () => {
    expect(
      durationOutOfRangeReason(
        4,
        { rawDurations: [4, 6, 8], supportedDurations: [8], durationConstraints: CONSTRAINTS },
        { usesReferenceImages: true },
      ),
    ).toBe("reference");
  });

  it("未越界 / 值缺失 / 能力未知一律 null", () => {
    const capsIn = {
      rawDurations: [4, 6, 8],
      supportedDurations: [8],
      durationConstraints: CONSTRAINTS,
    };
    expect(durationOutOfRangeReason(8, capsIn, {})).toBeNull();
    expect(durationOutOfRangeReason(null, capsIn, {})).toBeNull();
    expect(
      durationOutOfRangeReason(4, { ...capsIn, supportedDurations: null }, {}),
    ).toBeNull();
  });
});
