import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { catalogDurations, useModelCapabilities } from "@/hooks/useModelCapabilities";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import type { ProviderInfo, VideoCapabilities } from "@/types";

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
    ...overrides,
  };
}

beforeEach(() => {
  useCapabilitiesStore.setState({ revision: 0 });
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

  it("目录查不到但服务端结果描述的正是所问后端时，采信服务端时长", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      // providers 为空模拟目录请求失败降级；后端与服务端解析结果同一模型。
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND, providers: [] }),
    );
    await waitFor(() => expect(result.current.supportedDurations).toEqual([5, 10]));
  });

  it("服务端结果描述的是另一个模型时不采信，时长按未知处理", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      // 设置页切到未保存的候选后端 + 目录请求失败：服务端返回的仍是已保存模型的时长，
      // 采信会把旧模型时长摆成新候选的选项。
      useModelCapabilities({ projectName: PROJECT, videoBackend: "ark/other-model", providers: [] }),
    );
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(result.current.rawDurations).toBeNull();
    expect(result.current.supportedDurations).toBeNull();
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
