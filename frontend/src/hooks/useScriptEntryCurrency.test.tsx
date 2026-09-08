import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useScriptEntryCurrency } from "@/hooks/useScriptEntryCurrency";
import type { ScriptEntryCurrency, ScriptReviewState } from "@/types";

function reviewState(currency: ScriptEntryCurrency | null): ScriptReviewState {
  return {
    episode: 1,
    content_mode: "narration",
    status: "pending_review",
    fingerprint: "fp1",
    confirmed_at: null,
    quarantine: null,
    supported_durations: null,
    duration_tiers: null,
    episode_target_duration: null,
    content: null,
    script_entry_currency: currency,
  };
}

function currency(stale: string[]): ScriptEntryCurrency {
  return { stale, added: [], removed: [], order_changed: false };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useScriptEntryCurrency", () => {
  it("失效条目取自内容确认状态的 script_entry_currency，不走机械转换预演", async () => {
    const get = vi.spyOn(API, "getScriptReview").mockResolvedValue(reviewState(currency(["E1S02"])));
    const preview = vi.spyOn(API, "previewScriptPlanConversion");

    const { result } = renderHook(() =>
      useScriptEntryCurrency({ projectName: "p", episode: 1, enabled: true, scriptRevision: {} }),
    );

    await waitFor(() => expect(result.current.staleIds.has("E1S02")).toBe(true));
    expect(get).toHaveBeenCalledWith("p", 1, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(preview).not.toHaveBeenCalled();
  });

  it("服务端给 null（没有可比对的两方）时视为没有失效条目", async () => {
    vi.spyOn(API, "getScriptReview").mockResolvedValue(reviewState(null));

    const { result } = renderHook(() =>
      useScriptEntryCurrency({ projectName: "p", episode: 1, enabled: true, scriptRevision: {} }),
    );

    await waitFor(() => expect(API.getScriptReview).toHaveBeenCalledTimes(1));
    expect(result.current.staleIds.size).toBe(0);
  });

  it("请求失败时保留上一次结果并记警告，不把「读不出」呈现成「全部一致」", async () => {
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(reviewState(currency(["E1S02"])))
      .mockRejectedValueOnce(new Error("boom"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { result, rerender } = renderHook(
      ({ revision }: { revision: object }) =>
        useScriptEntryCurrency({ projectName: "p", episode: 1, enabled: true, scriptRevision: revision }),
      { initialProps: { revision: {} } },
    );
    await waitFor(() => expect(result.current.staleIds.has("E1S02")).toBe(true));

    rerender({ revision: {} });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(warn).toHaveBeenCalledTimes(1));
    expect(warn.mock.calls[0][0]).toContain("条目时效");
    expect(result.current.staleIds.has("E1S02")).toBe(true);
  });

  it("换一份剧本就重新比对", async () => {
    const get = vi
      .spyOn(API, "getScriptReview")
      .mockResolvedValueOnce(reviewState(currency(["E1S02"])))
      .mockResolvedValueOnce(reviewState(currency([])));

    const { result, rerender } = renderHook(
      ({ revision }: { revision: object }) =>
        useScriptEntryCurrency({ projectName: "p", episode: 1, enabled: true, scriptRevision: revision }),
      { initialProps: { revision: {} } },
    );
    await waitFor(() => expect(result.current.staleIds.has("E1S02")).toBe(true));

    rerender({ revision: {} });

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.staleIds.size).toBe(0));
  });

  it("未启用时不发请求，返回空集合", () => {
    const get = vi.spyOn(API, "getScriptReview");

    const { result } = renderHook(() =>
      useScriptEntryCurrency({ projectName: "p", episode: 1, enabled: false, scriptRevision: {} }),
    );

    expect(get).not.toHaveBeenCalled();
    expect(result.current.staleIds.size).toBe(0);
  });
});
