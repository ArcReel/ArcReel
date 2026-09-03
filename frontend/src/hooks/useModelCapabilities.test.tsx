import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { durationOutOfRangeReason, useModelCapabilities } from "@/hooks/useModelCapabilities";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { DurationConstraints, VideoCapabilities } from "@/types";

const PROJECT = "demo-project";
const BACKEND = "gemini/veo-3";

function constraints(overrides: Partial<DurationConstraints> = {}): DurationConstraints {
  return {
    resolution: null,
    uses_reference_images: false,
    allowed: [4, 6, 8],
    allowed_without_reference_images: [4, 6, 8],
    excluded: {},
    ...overrides,
  };
}

function caps(overrides: Partial<VideoCapabilities> = {}): VideoCapabilities {
  return {
    provider_id: "gemini",
    model: "veo-3",
    supported_durations: [8, 4, 6],
    max_duration: 8,
    max_reference_images: 3,
    first_frame: true,
    last_frame: true,
    source: "registry",
    voice_consistency: "soft",
    duration_constraints: constraints(),
    ...overrides,
  };
}

beforeEach(() => {
  useCapabilitiesStore.setState({ revision: 0 });
  useProjectsStore.setState({ currentProjectName: null, currentProjectData: null });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useModelCapabilities 时长维度", () => {
  it("全集与收窄结果都取服务端值，全集按升序整理", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(
      caps({
        duration_constraints: constraints({
          resolution: "1080p",
          allowed: [8],
          excluded: { "4": "resolution", "6": "resolution" },
        }),
      }),
    );
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND }),
    );
    await waitFor(() => expect(result.current.supportedDurations).toEqual([8]));
    expect(result.current.rawDurations).toEqual([4, 6, 8]);
    expect(result.current.excludedDurations).toEqual({ "4": "resolution", "6": "resolution" });
    expect(result.current.resolvedVideoBackend).toBe("gemini/veo-3");
  });

  it("参考生视频画布用的无参考图档位同样来自服务端", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(
      caps({
        duration_constraints: constraints({
          uses_reference_images: true,
          allowed: [8],
          allowed_without_reference_images: [4, 6, 8],
          excluded: { "4": "reference", "6": "reference" },
        }),
      }),
    );
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND }),
    );
    await waitFor(() => expect(result.current.supportedDurations).toEqual([8]));
    expect(result.current.supportedDurationsWithoutReference).toEqual([4, 6, 8]);
  });

  it("查询未落地 / 失败时时长为未知（null），不谎报成空集合", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND }),
    );
    expect(result.current.supportedDurations).toBeNull();
    expect(result.current.rawDurations).toBeNull();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.supportedDurations).toBeNull();
    expect(result.current.excludedDurations).toEqual({});
  });

  it("裸 provider 后端下 resolvedVideoBackend 给出服务端补全的 provider/model", async () => {
    // 裸 provider 是合法项目配置（服务端补全默认视频模型）。调用方按「项目为该后端保存了什么」
    // 查配置时必须用这个已解析值，拿裸值去查会把 provider ID 当成 model ID、读不到实际档位。
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: "gemini" }),
    );
    await waitFor(() => expect(result.current.resolvedVideoBackend).toBe("gemini/veo-3"));
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

  it("enabled=false 时不查", () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND, enabled: false }),
    );
    expect(result.current.supportedDurations).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });

  it("演示项目不发请求", () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({ projectName: DEMO_PROJECT_NAME, videoBackend: BACKEND }),
    );
    expect(result.current.supportedDurations).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("useModelCapabilities 约束上下文", () => {
  it("把表单里未保存的分辨率与参考图路径交给服务端求值", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    renderHook(() =>
      useModelCapabilities({
        projectName: PROJECT,
        videoBackend: BACKEND,
        videoResolution: "1080p",
        usesReferenceImages: true,
      }),
    );
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]?.[1]).toMatchObject({
      videoBackend: BACKEND,
      resolution: "1080p",
      usesReferenceImages: true,
    });
  });

  it("表单里的「自动」分辨率显式传 null，不让服务端回退到已保存档位", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    renderHook(() =>
      useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND, videoResolution: null }),
    );
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]?.[1]?.resolution).toBeNull();
  });

  it("不传上下文时不带参数，服务端按项目已保存档位与生成模式求值", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    renderHook(() => useModelCapabilities({ projectName: PROJECT, videoBackend: "" }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const options = spy.mock.calls[0]?.[1];
    expect(options?.videoBackend).toBeUndefined();
    expect(options?.resolution).toBeUndefined();
    expect(options?.usesReferenceImages).toBeUndefined();
  });

  it("切换分辨率时重取，重取期间保留同一模型的旧收窄结果而不闪未知态", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result, rerender } = renderHook(
      (resolution: string | null) =>
        useModelCapabilities({ projectName: PROJECT, videoBackend: BACKEND, videoResolution: resolution }),
      { initialProps: null as string | null },
    );
    await waitFor(() => expect(result.current.supportedDurations).toEqual([4, 6, 8]));

    spy.mockReturnValue(new Promise(() => {}));
    rerender("1080p");
    // 同一模型下旧的收窄结果仍是当前最优估计：时长选择器不该整块消失再出现。
    expect(result.current.supportedDurations).toEqual([4, 6, 8]);
    expect(result.current.loading).toBe(true);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy.mock.calls[1]?.[1]?.resolution).toBe("1080p");
  });
});

describe("useModelCapabilities 无项目上下文", () => {
  it("创建向导没有项目时按候选模型走无项目端点", async () => {
    const modelSpy = vi.spyOn(API, "getModelVideoCapabilities").mockResolvedValue(
      caps({ duration_constraints: constraints({ uses_reference_images: true, allowed: [8] }) }),
    );
    const projectSpy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() =>
      useModelCapabilities({ videoBackend: BACKEND, usesReferenceImages: true }),
    );
    await waitFor(() => expect(result.current.supportedDurations).toEqual([8]));
    expect(projectSpy).not.toHaveBeenCalled();
    expect(modelSpy.mock.calls[0]?.[0]).toBe(BACKEND);
    expect(modelSpy.mock.calls[0]?.[1]).toMatchObject({ usesReferenceImages: true });
  });

  it("既无项目也无候选模型时不发请求", () => {
    const modelSpy = vi.spyOn(API, "getModelVideoCapabilities").mockResolvedValue(caps());
    const { result } = renderHook(() => useModelCapabilities({ videoBackend: "" }));
    expect(result.current.supportedDurations).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(modelSpy).not.toHaveBeenCalled();
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

describe("durationOutOfRangeReason", () => {
  const capsIn = {
    rawDurations: [4, 6, 8],
    supportedDurations: [8],
    excludedDurations: { "4": "resolution" as const, "6": "reference" as const },
  };

  it("全集就不含该值 → model", () => {
    expect(durationOutOfRangeReason(5, capsIn)).toBe("model");
  });

  // 成因决定提示把用户引向哪里：分辨率 / 参考图两条改对应设置也能解决，不该被引去换模型。
  it("被约束剔除的值按服务端给出的成因分类", () => {
    expect(durationOutOfRangeReason(4, capsIn)).toBe("resolution");
    expect(durationOutOfRangeReason(6, capsIn)).toBe("reference");
  });

  it("未越界 / 值缺失 / 能力未知一律 null", () => {
    expect(durationOutOfRangeReason(8, capsIn)).toBeNull();
    expect(durationOutOfRangeReason(null, capsIn)).toBeNull();
    expect(durationOutOfRangeReason(4, { ...capsIn, supportedDurations: null })).toBeNull();
  });
});
