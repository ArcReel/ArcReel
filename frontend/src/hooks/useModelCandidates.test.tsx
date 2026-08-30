import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useEffect } from "react";
import i18n from "@/i18n";
import { API } from "@/api";
import { useModelCandidates } from "@/hooks/useModelCandidates";
import type { ModelCandidatesResponse } from "@/types/system";

const CANDIDATES = {
  video: { default: ["gemini/veo-3"], buckets: { i2v: ["gemini/veo-3"], r2v: [] } },
  image: { default: ["gemini/imagen-4"], buckets: { t2i: ["gemini/imagen-4"], i2i: [] } },
} as unknown as ModelCandidatesResponse;

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useModelCandidates", () => {
  it("starts neutral: neither loaded nor failed until reload is called", () => {
    const { result } = renderHook(() => useModelCandidates());
    expect(result.current.candidates).toBeNull();
    expect(result.current.error).toBe(false);
    expect(result.current.retrying).toBe(false);
  });

  it("marks the error state on failure and clears it once a later attempt succeeds", async () => {
    const spy = vi
      .spyOn(API, "getModelCandidates")
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(CANDIDATES);
    const { result } = renderHook(() => useModelCandidates());

    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.error).toBe(true);
    expect(result.current.candidates).toBeNull();

    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.error).toBe(false);
    expect(result.current.candidates).toEqual(CANDIDATES);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(result.current.retrying).toBe(false);
  });

  it("does not treat an empty candidate set as a failure", async () => {
    const empty = {
      video: { default: [], buckets: { i2v: [], r2v: [] } },
      image: { default: [], buckets: { t2i: [], i2i: [] } },
    } as unknown as ModelCandidatesResponse;
    vi.spyOn(API, "getModelCandidates").mockResolvedValue(empty);
    const { result } = renderHook(() => useModelCandidates());

    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.error).toBe(false);
    expect(result.current.candidates).toEqual(empty);
  });

  it("aborts the in-flight request when a new reload takes over, and keeps the newer result", async () => {
    const signals: AbortSignal[] = [];
    let settleFirst: ((v: ModelCandidatesResponse) => void) | undefined;
    vi.spyOn(API, "getModelCandidates").mockImplementation((options = {}) => {
      if (options.signal) signals.push(options.signal);
      if (signals.length === 1) {
        return new Promise<ModelCandidatesResponse>((resolve) => {
          settleFirst = resolve;
        });
      }
      return Promise.resolve(CANDIDATES);
    });
    const { result } = renderHook(() => useModelCandidates());

    let first: Promise<void>;
    act(() => {
      first = result.current.reload();
    });
    await waitFor(() => expect(result.current.retrying).toBe(true));

    await act(async () => {
      await result.current.reload();
    });
    expect(signals[0].aborted).toBe(true);
    expect(result.current.candidates).toEqual(CANDIDATES);

    // 过期响应即便晚到也不得回写，否则会用旧结果盖掉接管方刚落地的值。
    const stale = { video: null, image: null } as unknown as ModelCandidatesResponse;
    await act(async () => {
      settleFirst?.(stale);
      await first;
    });
    expect(result.current.candidates).toEqual(CANDIDATES);
    expect(result.current.retrying).toBe(false);
  });

  it("aborts the in-flight request on unmount", async () => {
    const signals: AbortSignal[] = [];
    vi.spyOn(API, "getModelCandidates").mockImplementation((options = {}) => {
      if (options.signal) signals.push(options.signal);
      return new Promise<ModelCandidatesResponse>(() => {});
    });
    const { result, unmount } = renderHook(() => useModelCandidates());

    act(() => {
      void result.current.reload();
    });
    await waitFor(() => expect(signals).toHaveLength(1));
    unmount();
    expect(signals[0].aborted).toBe(true);
  });
});

describe("useModelCandidates – 语言切换", () => {
  afterEach(async () => {
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
  });

  it("换语言即换掉 reload 的标识，调用点的 effect 据此重取译名", async () => {
    const spy = vi.spyOn(API, "getModelCandidates").mockResolvedValue(CANDIDATES);
    renderHook(() => {
      const { reload } = useModelCandidates();
      // 调用点惯常写法：把 reload 放进依赖数组
      useEffect(() => {
        void reload();
      }, [reload]);
    });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    await act(async () => {
      await i18n.changeLanguage("en");
    });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });
});
