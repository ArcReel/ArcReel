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

    // Switch to demos: debouncedFetch internally waits 500ms, but demo state must clear immediately,
    // preventing UI reads of previous real-project cost caches during debounce windows.
    useCostStore.getState().debouncedFetch(DEMO_PROJECT_NAME);

    expect(useCostStore.getState().costData).toBeNull();
    expect(useCostStore.getState().loading).toBe(false);
    expect(API.getCostEstimate).not.toHaveBeenCalledWith(DEMO_PROJECT_NAME);
  });

  it("discards a late successful response whose fetchCost call fires after the user already switched projects", async () => {
    // project-b finishes first: users already switched, B cost requests completed/persisted.
    useProjectsStore.setState({ currentProjectName: "project-b" });
    vi.spyOn(API, "getCostEstimate").mockResolvedValueOnce(buildResponse("project-b"));
    await useCostStore.getState().fetchCost("project-b");
    expect(useCostStore.getState().costData?.project_name).toBe("project-b");

    // Slow project-a PATCH .then callbacks trigger only after users leave, now invoking
    // fetchCost("project-a") without subsequent calls to abort it. Only current-project-name validation at
    // persistence can block it.
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

    // Never write errors into stores (users already left, so departed-project failures need no notice)
    // or disturb already-settled loading.
    expect(useCostStore.getState().error).toBeNull();
    expect(useCostStore.getState().costData?.project_name).toBe("project-b");
    expect(useCostStore.getState().loading).toBe(false);
  });

  it("does not clear or replace the shared debounce timer for a call from a project the user already left", () => {
    vi.useFakeTimers();
    useProjectsStore.setState({ currentProjectName: "project-b" });
    const fetchCostSpy = vi.spyOn(useCostStore.getState(), "fetchCost").mockResolvedValue();

    // project-b queued one debounced refresh.
    useCostStore.getState().debouncedFetch("project-b");

    // project-a calls trigger only after users leave (for example slow PATCH .then callbacks);
    // never clear/replace project-b's newly queued timer.
    useCostStore.getState().debouncedFetch("project-a");

    vi.advanceTimersByTime(500);

    expect(fetchCostSpy).toHaveBeenCalledTimes(1);
    expect(fetchCostSpy).toHaveBeenCalledWith("project-b");
  });

  it("does not abort the current project's in-flight request when a stale project's fetchCost fires late", async () => {
    // project-b requests start first, remaining pending to simulate awaiting responses.
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

    // project-a calls trigger only after users leave (for example slow PATCH .then callbacks);
    // never abort still-valid project-b requests, unlike replacing same-project in-flight requests.
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

    // Later same-project calls replace earlier ones: actually abort signals/network requests,
    // and late superseded responses must never write stores.
    expect(signals[0]?.aborted).toBe(true);
    releaseFirst(buildResponse("first"));
    await first;

    expect(useCostStore.getState().costData?.project_name).toBe("second");
    expect(useCostStore.getState().loading).toBe(false);
  });
});
