import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TrialRunInfo } from "@/types";

import { useTrialRun } from "./use-trial-run";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

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

type Route = "create" | "read" | "cancel";
type Reply = TrialRunInfo | number | (() => Promise<Response>);

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/**
 * 在 fetch 边界按路由回放响应：真实的 API 方法与 HTTP 状态到 ApiRequestError 的映射都参与测试。
 * 每条路由按队列取回复，最后一条重复使用；数字回复是带 detail 的错误状态。
 */
function stubTrialRunServer(replies: Partial<Record<Route, Reply[]>>) {
  const queues: Record<Route, Reply[]> = { create: [RUNNING], read: [], cancel: [], ...replies };
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const route: Route = url.endsWith("/cancel") ? "cancel" : init?.method === "POST" ? "create" : "read";
    const queue = queues[route];
    const reply = queue.length > 1 ? queue.shift() : queue[0];
    if (reply === undefined) throw new Error(`unexpected ${route} request: ${url}`);
    if (typeof reply === "function") return reply();
    if (typeof reply === "number") {
      return Promise.resolve(json({ detail: reply === 404 ? "not found" : "network down" }, reply));
    }
    return Promise.resolve(json(reply));
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    replies: queues,
    requested: (route: Route) =>
      fetchMock.mock.calls.filter(([input, init]) => {
        const url = String(input);
        if (route === "cancel") return url.endsWith("/cancel");
        return !url.endsWith("/cancel") && (route === "create") === (init?.method === "POST");
      }).map(([input]) => String(input)),
  };
}

async function startedTrial(replies: Partial<Record<Route, Reply[]>>) {
  const server = stubTrialRunServer(replies);
  const hook = renderHook(() => useTrialRun());
  await act(() => hook.result.current.start({ parameters: {} as never }));
  return { ...hook, server };
}

describe("useTrialRun cancel", () => {
  it("shows the kept result when the cancel arrives after the provider already returned", async () => {
    const { result, server } = await startedTrial({ cancel: [404], read: [SUCCEEDED] });

    await act(() => result.current.cancel());

    expect(server.requested("read")).toEqual(["/api/v1/custom-endpoints/trial-runs/run-1"]);
    expect(result.current.run).toEqual(SUCCEEDED);
    expect(result.current.finished).toBe(true);
    expect(result.current.cancelled).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("resumes polling after a stopped poll reads a nonterminal run on late cancel", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const networkDown = () => Promise.reject(new TypeError("network down"));
    const { result, server } = await startedTrial({ read: [networkDown] });
    for (let attempt = 0; attempt < 5; attempt += 1) {
      await act(() => vi.advanceTimersByTimeAsync(2000));
    }
    expect(result.current.pollStopped).toBe(true);
    server.replies.cancel.push(404);
    server.replies.read.splice(0, 1, RUNNING, SUCCEEDED);

    await act(() => result.current.cancel());
    expect(result.current.pollStopped).toBe(false);
    await act(() => vi.advanceTimersByTimeAsync(2000));

    expect(result.current.run).toEqual(SUCCEEDED);
    expect(result.current.finished).toBe(true);
    expect(result.current.cancelled).toBe(false);
  });

  it("clears the trial as cancelled when the run is gone on read-back too", async () => {
    const { result } = await startedTrial({ cancel: [404], read: [404] });

    await act(() => result.current.cancel());

    expect(result.current.run).toBeNull();
    expect(result.current.cancelled).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("clears the trial as cancelled without reading back when the cancel succeeds", async () => {
    const { result, server } = await startedTrial({ cancel: [() => Promise.resolve(new Response(null, { status: 204 }))] });

    await act(() => result.current.cancel());

    expect(server.requested("read")).toEqual([]);
    expect(result.current.run).toBeNull();
    expect(result.current.cancelled).toBe(true);
  });

  it("keeps the run and reports the error when the read-back fails for another reason", async () => {
    const { result } = await startedTrial({ cancel: [404], read: [503] });

    await act(() => result.current.cancel());

    expect(result.current.run).toEqual(RUNNING);
    expect(result.current.cancelled).toBe(false);
    expect(result.current.error).toBe("network down");
  });

  it.each([
    ["the kept result", SUCCEEDED],
    ["not found", 404],
  ] as const)("leaves a newly started run alone when the old cancel reads back %s", async (_, readBack) => {
    let answerCancel: (response: Response) => void = () => {};
    const cancelReply = () => new Promise<Response>((resolve) => (answerCancel = resolve));
    const next: TrialRunInfo = { ...RUNNING, id: "run-2" };
    const { result } = await startedTrial({ create: [RUNNING, next], cancel: [cancelReply], read: [readBack] });

    let cancelling: Promise<void> = Promise.resolve();
    act(() => {
      cancelling = result.current.cancel();
    });
    await act(() => result.current.start({ parameters: {} as never }));
    await act(async () => {
      answerCancel(json({ detail: "not found" }, 404));
      await cancelling;
    });

    expect(result.current.run).toEqual(next);
    expect(result.current.cancelled).toBe(false);
  });
});
