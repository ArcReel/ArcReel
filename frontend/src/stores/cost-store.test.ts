import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import { useCostStore } from "./cost-store";

describe("cost-store", () => {
  beforeEach(() => {
    useCostStore.setState(useCostStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("clears stale cost data immediately when switching to the demo project via debouncedFetch", async () => {
    vi.spyOn(API, "getCostEstimate").mockResolvedValue({
      project_totals: { total: 42 },
      episodes: [],
    } as never);

    await useCostStore.getState().fetchCost("real-project");
    expect(useCostStore.getState().costData).not.toBeNull();

    // 切到演示项目：debouncedFetch 内部有 500ms 防抖，但演示态必须立即清空，
    // 不能让 UI 在防抖窗口期间继续读到上一个真实项目的费用缓存。
    useCostStore.getState().debouncedFetch(DEMO_PROJECT_NAME);

    expect(useCostStore.getState().costData).toBeNull();
    expect(useCostStore.getState().loading).toBe(false);
    expect(API.getCostEstimate).not.toHaveBeenCalledWith(DEMO_PROJECT_NAME);
  });
});
