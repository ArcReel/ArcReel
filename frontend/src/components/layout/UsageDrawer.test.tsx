import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UsageDrawer } from "./UsageDrawer";
import { API } from "@/api";
import { useUsageStore } from "@/stores/usage-store";
import { createRef } from "react";

describe("UsageDrawer", () => {
  beforeEach(() => {
    useUsageStore.setState(useUsageStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("aborts the in-flight stats/calls requests when the project switches before they resolve", async () => {
    const statsPending: {
      signal: AbortSignal | undefined;
      resolve: (v: Record<string, unknown>) => void;
    }[] = [];
    const callsPending: {
      signal: AbortSignal | undefined;
      resolve: (v: Record<string, unknown>) => void;
    }[] = [];
    vi.spyOn(API, "getUsageStats").mockImplementation(
      (_filters, options) =>
        new Promise((resolve, reject) => {
          options?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
          statsPending.push({ signal: options?.signal, resolve });
        }),
    );
    vi.spyOn(API, "getUsageCalls").mockImplementation(
      (_filters, options) =>
        new Promise((resolve, reject) => {
          options?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
          callsPending.push({ signal: options?.signal, resolve });
        }),
    );

    const anchorRef = createRef<HTMLElement>();
    const { rerender } = render(
      <UsageDrawer
        open
        onClose={() => {}}
        projectName="real-project"
        anchorRef={anchorRef}
      />,
    );

    await waitFor(() => {
      expect(statsPending.length).toBe(1);
      expect(callsPending.length).toBe(1);
    });

    // 抽屉仍打开，但项目名从真实项目切到演示项目（如工作台切入演示态）——
    // 此前未完成的请求须被 abort，不能让旧项目响应之后覆盖 store
    rerender(
      <UsageDrawer open onClose={() => {}} projectName={null} anchorRef={anchorRef} />,
    );

    await waitFor(() => {
      expect(statsPending.length).toBe(2);
      expect(callsPending.length).toBe(2);
    });
    expect(statsPending[0].signal?.aborted).toBe(true);
    expect(callsPending[0].signal?.aborted).toBe(true);

    statsPending[1].resolve({ cost_by_currency: { usd: 1 } });
    callsPending[1].resolve({ items: [], total: 0 });

    await waitFor(() => {
      expect(useUsageStore.getState().stats?.cost_by_currency).toEqual({ usd: 1 });
    });
  });
});
