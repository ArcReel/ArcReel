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

describe("useReferenceDurationGate", () => {
  it("marks direct submissions as not explicitly confirmed", async () => {
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
    const commit = vi.fn(async () => {});
    const { result } = renderHook(() => useReferenceDurationGate({ projectName: "demo", episode: 1 }));

    await act(async () => {
      await result.current.run(["E1U1"], commit, () => true);
    });

    expect(commit).toHaveBeenCalledWith(["E1U1"], false);
  });

  it("marks submissions after the duration dialog as explicitly confirmed", async () => {
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
    const commit = vi.fn(async () => {});
    const { result } = renderHook(() => useReferenceDurationGate({ projectName: "demo", episode: 1 }));

    await act(async () => {
      await result.current.run(["E1U1"], commit, () => true);
    });
    act(() => result.current.dialogProps.onConfirm());

    await waitFor(() => expect(commit).toHaveBeenCalledWith(["E1U1"], true));
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
          locations: [{ path: ["references"], line: null }],
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
