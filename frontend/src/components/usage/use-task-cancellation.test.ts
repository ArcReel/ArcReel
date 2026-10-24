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

  it("does not bring the confirmation back when switching to another scope and back", async () => {
    vi.spyOn(API, "cancelAllPreview").mockResolvedValue({ queued_count: 2 });
    const { result, rerender } = renderHook(({ scope }) => useTaskCancellation(scope), {
      initialProps: { scope: "a" },
    });
    await act(() => result.current.requestAll("a"));

    rerender({ scope: "b" });
    rerender({ scope: "a" });
    expect(result.current.request).toBeNull();
  });

  it("keeps the new scope's confirmation when a confirm from the previous scope settles later", async () => {
    vi.spyOn(API, "cancelAllPreview").mockResolvedValue({ queued_count: 2 });
    let reject!: (error: Error) => void;
    vi.spyOn(API, "cancelAllQueued").mockImplementation(
      () =>
        new Promise((_, r) => {
          reject = r;
        }),
    );
    const { result, rerender } = renderHook(({ scope }) => useTaskCancellation(scope), {
      initialProps: { scope: "a" },
    });
    await act(() => result.current.requestAll("a"));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.confirm();
    });

    rerender({ scope: "b" });
    await act(() => result.current.requestAll("b"));
    await act(async () => {
      reject(new Error("network"));
      await pending;
    });

    expect(result.current.request).toEqual({ kind: "all", projectName: "b", queuedCount: 2 });
    expect(result.current.failed).toBe(false);
    expect(result.current.cancelling).toBe(false);
  });

  it("ignores a confirmation written for the previous scope even when its preview was not aborted", async () => {
    vi.spyOn(API, "cancelAllPreview").mockResolvedValue({ queued_count: 2 });
    const { result, rerender } = renderHook(({ scope }) => useTaskCancellation(scope), {
      initialProps: { scope: "a" },
    });
    const requestAllInA = result.current.requestAll;

    rerender({ scope: "b" });
    await act(() => requestAllInA("a"));
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
