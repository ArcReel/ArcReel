import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API, SpeechAdmissionError } from "@/api";
import { useReferenceDurationGate } from "@/hooks/useReferenceDurationGate";
import { useAppStore } from "@/stores/app-store";

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useReferenceDurationGate", () => {
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
