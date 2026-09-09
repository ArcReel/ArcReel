import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API, ReferenceProjectionError, SpeechAdmissionError } from "@/api";
import { useReferenceDurationGate } from "@/hooks/useReferenceDurationGate";
import { useAppStore } from "@/stores/app-store";

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** 预检对参考图取前 N 张的非阻断告知；后端已把 message 渲染成当前语言。 */
const clampedProblem = {
  code: "reference_images_clamped",
  blocking: false,
  unit_id: "E1U1",
  locations: [{ path: ["text"], line: null }],
  params: { count: 3, max_count: 1, provider: "openai", model: "sora-2" },
  action: "review_reference_selection",
  message: "参考图数量 3 超出 openai/sora-2 上限 1，请求将使用前 1 张",
};

describe("useReferenceDurationGate", () => {
  it("submits exact-tier requests without a confirmation coordinate", async () => {
    vi.spyOn(API, "precheckReferenceVideoDuration").mockResolvedValue({
      needs_confirmation: false,
      script_duration: 4,
      duration_input: 4,
      request_duration: 4,
      adjustment: "exact",
      declared_capability: "i2v",
      hydrated_capability: "i2v",
      provider_id: "kling",
      model_id: "kling-v2-1-master",
      problems: [],
    });
    const commit = vi.fn(async (_unitIds: string[], _confirmed: ReadonlyMap<string, number>) => {});
    const { result } = renderHook(() => useReferenceDurationGate({ projectName: "demo", episode: 1 }));

    await act(async () => {
      await result.current.run(["E1U1"], commit, () => true);
    });

    expect(commit).toHaveBeenCalledTimes(1);
    expect([...commit.mock.calls[0]![1]]).toEqual([]);
  });

  it("submits the exact accepted tier after the duration dialog", async () => {
    vi.spyOn(API, "precheckReferenceVideoDuration").mockResolvedValue({
      needs_confirmation: true,
      script_duration: 5,
      duration_input: 5,
      request_duration: 8,
      adjustment: "up",
      declared_capability: "i2v",
      hydrated_capability: "i2v",
      provider_id: "kling",
      model_id: "kling-v2-1-master",
      problems: [],
    });
    const commit = vi.fn(async (_unitIds: string[], _confirmed: ReadonlyMap<string, number>) => {});
    const { result } = renderHook(() => useReferenceDurationGate({ projectName: "demo", episode: 1 }));

    await act(async () => {
      await result.current.run(["E1U1"], commit, () => true);
    });
    act(() => result.current.dialogProps.onConfirm());

    await waitFor(() => expect(commit).toHaveBeenCalledTimes(1));
    expect(commit.mock.calls[0]![0]).toEqual(["E1U1"]);
    expect([...commit.mock.calls[0]![1]]).toEqual([["E1U1", 8]]);
  });

  it("preserves structured speech admission details from precheck", async () => {
    const error = new SpeechAdmissionError({
      allowed: false,
      unit_id: "E1U1",
      mode: null,
      problems: [
        {
          code: "mixed_speech",
          unit_id: "E1U1",
          locations: [{ path: ["shots", 0, "text"], line: 1 }],
          reason: "character_and_narrator_mixed",
          action: "replan_unit",
        },
      ],
    });
    vi.spyOn(API, "precheckReferenceVideoDuration").mockRejectedValue(error);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const commit = vi.fn(async () => {});
    const { result } = renderHook(() => useReferenceDurationGate({ projectName: "demo", episode: 1 }));

    await act(async () => {
      await result.current.run(["E1U1"], commit, () => true);
    });

    expect(pushToast).toHaveBeenCalledWith(error.message, "error");
    expect(pushToast).toHaveBeenCalledTimes(1);
    expect(commit).not.toHaveBeenCalled();
  });

  it("presents a structured reference projection repair message", async () => {
    const error = new ReferenceProjectionError({
      allowed: false,
      kind: "reference_request_projection",
      unit_id: "E1U1",
      problems: [
        {
          code: "reference_asset_missing",
          blocking: true,
          unit_id: "E1U1",
          locations: [{ path: ["text"], line: null }],
          params: { missing: [["character", "张三"]] },
          action: "repair_reference_assets",
          message: "请补齐张三的参考图",
        },
      ],
    });
    vi.spyOn(API, "precheckReferenceVideoDuration").mockRejectedValue(error);
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const commit = vi.fn(async () => {});
    const { result } = renderHook(() => useReferenceDurationGate({ projectName: "demo", episode: 1 }));

    await act(async () => {
      await result.current.run(["E1U1"], commit, () => true);
    });

    expect(pushToast).toHaveBeenCalledWith("请补齐张三的参考图", "error");
    expect(pushToast).toHaveBeenCalledTimes(1);
    expect(commit).not.toHaveBeenCalled();
  });

  it("confirms advisory precheck problems even when no tier changes", async () => {
    vi.spyOn(API, "precheckReferenceVideoDuration").mockResolvedValue({
      needs_confirmation: false,
      script_duration: 4,
      duration_input: 4,
      request_duration: 4,
      adjustment: "exact",
      declared_capability: "r2v",
      hydrated_capability: "r2v",
      provider_id: "openai",
      model_id: "sora-2",
      problems: [clampedProblem],
    });
    const commit = vi.fn(async (_unitIds: string[], _confirmed: ReadonlyMap<string, number>) => {});
    const { result } = renderHook(() => useReferenceDurationGate({ projectName: "demo", episode: 1 }));

    await act(async () => {
      await result.current.run(["E1U1"], commit, () => true);
    });

    expect(commit).not.toHaveBeenCalled();
    expect(result.current.dialogProps.open).toBe(true);

    act(() => result.current.dialogProps.onConfirm());

    await waitFor(() => expect(commit).toHaveBeenCalledTimes(1));
    expect(commit.mock.calls[0]![0]).toEqual(["E1U1"]);
    // 没有取档偏离要拍板，就不替用户声明一个已确认档位
    expect([...commit.mock.calls[0]![1]]).toEqual([]);
  });

  it("enqueues nothing when the advisory confirmation is cancelled", async () => {
    vi.spyOn(API, "precheckReferenceVideoDuration").mockResolvedValue({
      needs_confirmation: false,
      script_duration: 4,
      duration_input: 4,
      request_duration: 4,
      adjustment: "exact",
      declared_capability: "r2v",
      hydrated_capability: "r2v",
      provider_id: "openai",
      model_id: "sora-2",
      problems: [clampedProblem],
    });
    const commit = vi.fn(async (_unitIds: string[], _confirmed: ReadonlyMap<string, number>) => {});
    const { result } = renderHook(() => useReferenceDurationGate({ projectName: "demo", episode: 1 }));

    await act(async () => {
      await result.current.run(["E1U1"], commit, () => true);
    });
    act(() => result.current.dialogProps.onCancel());

    expect(result.current.dialogProps.open).toBe(false);
    expect(commit).not.toHaveBeenCalled();
  });

  it("keeps the aggregate fallback for non-admission precheck failures", async () => {
    vi.spyOn(API, "precheckReferenceVideoDuration").mockRejectedValue(new Error("offline"));
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    const { result } = renderHook(() => useReferenceDurationGate({ projectName: "demo", episode: 1 }));

    await act(async () => {
      await result.current.run(["E1U1"], vi.fn(async () => {}), () => true);
    });

    expect(pushToast).toHaveBeenCalledWith(expect.stringContaining("1 个单元"), "error");
    expect(pushToast).not.toHaveBeenCalledWith("offline", "error");
  });
});
