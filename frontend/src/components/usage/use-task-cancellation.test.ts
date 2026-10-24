import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";

import { useTaskCancellation } from "./use-task-cancellation";

afterEach(() => vi.restoreAllMocks());

describe("useTaskCancellation", () => {
  it("drops the pending confirmation as soon as the scope changes", async () => {
    vi.spyOn(API, "cancelAllPreview").mockResolvedValue({ queued_count: 2 });
    const { result, rerender } = renderHook(({ scope }) => useTaskCancellation(scope), {
      initialProps: { scope: "a" },
    });
    await act(() => result.current.requestAll("a"));
    expect(result.current.request).toEqual({ kind: "all", projectName: "a", queuedCount: 2 });

    rerender({ scope: "b" });
    expect(result.current.request).toBeNull();
  });

  it("discards a preview of the previous scope that resolves after the switch", async () => {
    let resolve!: (value: { queued_count: number }) => void;
    let signal: AbortSignal | undefined;
    vi.spyOn(API, "cancelAllPreview").mockImplementation((_project, options) => {
      signal = options?.signal;
      return new Promise((r) => {
        resolve = r;
      });
    });
    const { result, rerender } = renderHook(({ scope }) => useTaskCancellation(scope), {
      initialProps: { scope: "a" },
    });
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.requestAll("a");
    });

    rerender({ scope: "b" });
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      resolve({ queued_count: 3 });
      await pending;
    });
    expect(result.current.request).toBeNull();
  });
});
