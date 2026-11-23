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
    unpriced: { estimate: [], actual: [] },
    missing_local_calls: false,
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

    useCostStore.getState().debouncedFetch(DEMO_PROJECT_NAME);

    expect(useCostStore.getState().costData).toBeNull();
    expect(useCostStore.getState().loading).toBe(false);
    expect(API.getCostEstimate).not.toHaveBeenCalledWith(DEMO_PROJECT_NAME);
  });

  it("discards a late successful response whose fetchCost call fires after the user already switched projects", async () => {
    useProjectsStore.setState({ currentProjectName: "project-b" });
    vi.spyOn(API, "getCostEstimate").mockResolvedValueOnce(buildResponse("project-b"));
    await useCostStore.getState().fetchCost("project-b");
    expect(useCostStore.getState().costData?.project_name).toBe("project-b");

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

    expect(useCostStore.getState().error).toBeNull();
    expect(useCostStore.getState().costData?.project_name).toBe("project-b");
    expect(useCostStore.getState().loading).toBe(false);
  });

  it("does not clear or replace the shared debounce timer for a call from a project the user already left", () => {
    vi.useFakeTimers();
    useProjectsStore.setState({ currentProjectName: "project-b" });
    const fetchCostSpy = vi.spyOn(useCostStore.getState(), "fetchCost").mockResolvedValue();

    useCostStore.getState().debouncedFetch("project-b");

    useCostStore.getState().debouncedFetch("project-a");

    vi.advanceTimersByTime(500);

    expect(fetchCostSpy).toHaveBeenCalledTimes(1);
    expect(fetchCostSpy).toHaveBeenCalledWith("project-b");
  });

  it("does not abort the current project's in-flight request when a stale project's fetchCost fires late", async () => {
    useProjectsStore.setState({ currentProjectName: "project-b" });
    let releaseB: (data: CostEstimateResponse) => void = () => {};
    const bSignals: (AbortSignal | undefined)[] = [];
    vi.spyOn(API, "getCostEstimate").mockImplementation((name, options) => {
      if (name === "project-b") {
        bSignals.push(options?.signal);
        return new Promise<CostEstimateResponse>((resolve) => {
          releaseB = resolve;
        });
      }
      return Promise.resolve(buildResponse(name));
    });

    const bPromise = useCostStore.getState().fetchCost("project-b");

    await useCostStore.getState().fetchCost("project-a");
    expect(bSignals[0]?.aborted).toBe(false);

    releaseB(buildResponse("project-b"));
    await bPromise;

    expect(useCostStore.getState().costData?.project_name).toBe("project-b");
    expect(useCostStore.getState().loading).toBe(false);
  });

  it("aborts the in-flight request when a newer fetchCost supersedes it", async () => {
    useProjectsStore.setState({ currentProjectName: "project-a" });
    const signals: (AbortSignal | undefined)[] = [];
    let releaseFirst: (data: CostEstimateResponse) => void = () => {};
    vi.spyOn(API, "getCostEstimate")
      .mockImplementationOnce((_name, options) => {
        signals.push(options?.signal);
        return new Promise<CostEstimateResponse>((resolve) => {
          releaseFirst = resolve;
        });
      })
      .mockImplementationOnce((_name, options) => {
        signals.push(options?.signal);
        return Promise.resolve(buildResponse("second"));
      });

    const first = useCostStore.getState().fetchCost("project-a");
    const second = useCostStore.getState().fetchCost("project-a");
    await second;

    expect(signals[0]?.aborted).toBe(true);
    releaseFirst(buildResponse("first"));
    await first;

    expect(useCostStore.getState().costData?.project_name).toBe("second");
    expect(useCostStore.getState().loading).toBe(false);
  });
});
