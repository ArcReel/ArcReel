import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import { useProjectsStore } from "@/stores/projects-store";
import type { CostEstimateResponse } from "@/types";
import { useCostStore } from "./cost-store";

function buildResponse(projectName: string): CostEstimateResponse {
  return {
    project_name: projectName,
    models: { image: { provider: "p", model: "m" }, video: { provider: "p", model: "m" } },
    episodes: [],
    project_totals: { estimate: {}, actual: {} },
  };
}

describe("cost-store", () => {
  beforeEach(() => {
    useCostStore.setState(useCostStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("clears stale cost data immediately when switching to the demo project via debouncedFetch", async () => {
    useProjectsStore.setState({ currentProjectName: "real-project" });
    vi.spyOn(API, "getCostEstimate").mockResolvedValue(buildResponse("real-project"));

    await useCostStore.getState().fetchCost("real-project");
    expect(useCostStore.getState().costData).not.toBeNull();

    // 切到演示项目：debouncedFetch 内部有 500ms 防抖，但演示态必须立即清空，
    // 不能让 UI 在防抖窗口期间继续读到上一个真实项目的费用缓存。
    useCostStore.getState().debouncedFetch(DEMO_PROJECT_NAME);

    expect(useCostStore.getState().costData).toBeNull();
    expect(useCostStore.getState().loading).toBe(false);
    expect(API.getCostEstimate).not.toHaveBeenCalledWith(DEMO_PROJECT_NAME);
  });

  it("discards a late successful response whose fetchCost call fires after the user already switched projects", async () => {
    // project-b 先完整落地：模拟用户已经切走，且 B 自己的费用请求已经完成写入。
    useProjectsStore.setState({ currentProjectName: "project-b" });
    vi.spyOn(API, "getCostEstimate").mockResolvedValueOnce(buildResponse("project-b"));
    await useCostStore.getState().fetchCost("project-b");
    expect(useCostStore.getState().costData?.project_name).toBe("project-b");

    // project-a 页面里某个慢 PATCH 的 .then 回调在用户已经离开之后才触发，此时才发起
    // fetchCost("project-a")——没有任何后续调用去 abort 它，只能靠落地时校验当前
    // 项目名才能拦下。
    vi.spyOn(API, "getCostEstimate").mockResolvedValueOnce(buildResponse("project-a"));
    await useCostStore.getState().fetchCost("project-a");

    expect(useCostStore.getState().costData?.project_name).toBe("project-b");
    expect(useCostStore.getState().loading).toBe(false);
  });

  it("discards a late failed response whose fetchCost call fires after the user already switched projects, without surfacing its error", async () => {
    useProjectsStore.setState({ currentProjectName: "project-b" });
    vi.spyOn(API, "getCostEstimate").mockResolvedValueOnce(buildResponse("project-b"));
    await useCostStore.getState().fetchCost("project-b");
    expect(useCostStore.getState().costData?.project_name).toBe("project-b");

    vi.spyOn(API, "getCostEstimate").mockRejectedValueOnce(new Error("boom"));
    await useCostStore.getState().fetchCost("project-a");

    // 不应该把 error 写回 store（用户早已不在那个项目上，没有意义提示一个已经离开
    // 的项目的错误），也不应打断已经收尾的 loading。
    expect(useCostStore.getState().error).toBeNull();
    expect(useCostStore.getState().costData?.project_name).toBe("project-b");
    expect(useCostStore.getState().loading).toBe(false);
  });

  it("does not clear or replace the shared debounce timer for a call from a project the user already left", () => {
    vi.useFakeTimers();
    useProjectsStore.setState({ currentProjectName: "project-b" });
    const fetchCostSpy = vi.spyOn(useCostStore.getState(), "fetchCost").mockResolvedValue();

    // project-b 排了一次防抖刷新。
    useCostStore.getState().debouncedFetch("project-b");

    // project-a 的调用在用户已经离开之后才触发（比如某个慢 PATCH 的 .then 回调），
    // 不应清空/顶替 project-b 刚排的计时器。
    useCostStore.getState().debouncedFetch("project-a");

    vi.advanceTimersByTime(500);

    expect(fetchCostSpy).toHaveBeenCalledTimes(1);
    expect(fetchCostSpy).toHaveBeenCalledWith("project-b");
  });
});
