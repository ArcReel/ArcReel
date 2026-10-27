import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API, ApiRequestError } from "@/api";
import type { TrialRunInfo } from "@/types";

import { useTrialRun } from "./use-trial-run";

afterEach(() => vi.restoreAllMocks());

const RUNNING: TrialRunInfo = {
  id: "run-1",
  status: "running",
  provider: "example.test",
  model: "video-1",
  media_type: "video",
  created_at: 1,
  finished_at: null,
  api_call_id: null,
  provider_job_id: null,
  stages: {},
  request: null,
  submit_response: null,
  result_response: null,
  poll_responses: [],
  extractions: {},
  video_url: null,
  duration_seconds: null,
  error: null,
  error_code: null,
  error_action: null,
  has_artifact: false,
};

const SUCCEEDED: TrialRunInfo = { ...RUNNING, status: "succeeded", finished_at: 2, api_call_id: 7 };

const notFound = () => new ApiRequestError("not found", undefined, 404);

async function startedTrial() {
  vi.spyOn(API, "createTrialRun").mockResolvedValue(RUNNING);
  const hook = renderHook(() => useTrialRun());
  await act(() => hook.result.current.start({ parameters: {} as never }));
  return hook;
}

describe("useTrialRun cancel", () => {
  it("shows the kept result when the cancel arrives after the provider already returned", async () => {
    const { result } = await startedTrial();
    vi.spyOn(API, "cancelTrialRun").mockRejectedValue(notFound());
    const getTrialRun = vi.spyOn(API, "getTrialRun").mockResolvedValue(SUCCEEDED);

    await act(() => result.current.cancel());

    expect(getTrialRun).toHaveBeenCalledWith("run-1");
    expect(result.current.run).toEqual(SUCCEEDED);
    expect(result.current.finished).toBe(true);
    expect(result.current.cancelled).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("clears the trial as cancelled when the run is gone on read-back too", async () => {
    const { result } = await startedTrial();
    vi.spyOn(API, "cancelTrialRun").mockRejectedValue(notFound());
    vi.spyOn(API, "getTrialRun").mockRejectedValue(notFound());

    await act(() => result.current.cancel());

    expect(result.current.run).toBeNull();
    expect(result.current.cancelled).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("clears the trial as cancelled without reading back when the cancel succeeds", async () => {
    const { result } = await startedTrial();
    vi.spyOn(API, "cancelTrialRun").mockResolvedValue(undefined);
    const getTrialRun = vi.spyOn(API, "getTrialRun");

    await act(() => result.current.cancel());

    expect(getTrialRun).not.toHaveBeenCalled();
    expect(result.current.run).toBeNull();
    expect(result.current.cancelled).toBe(true);
  });

  it("keeps the run and reports the error when the read-back fails for another reason", async () => {
    const { result } = await startedTrial();
    vi.spyOn(API, "cancelTrialRun").mockRejectedValue(notFound());
    vi.spyOn(API, "getTrialRun").mockRejectedValue(new ApiRequestError("network down", undefined, 503));

    await act(() => result.current.cancel());

    expect(result.current.run).toEqual(RUNNING);
    expect(result.current.cancelled).toBe(false);
    expect(result.current.error).toBe("network down");
  });
});
