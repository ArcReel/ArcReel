import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { API, type ProjectEventStreamOptions } from "@/api";
import { useProjectEventsSSE } from "@/hooks/useProjectEventsSSE";
import { useAppStore } from "@/stores/app-store";
import { useUsageHeaderStore } from "@/stores/usage-header-store";
import { FakeSseStream } from "@/test/fakeSseStream";
import type { ProjectChange } from "@/types";
import { UsageHeaderEntry } from "./UsageHeaderEntry";
import { makeUsageSummary } from "./usage-fixtures";
import { HEADER_PROJECT, resetHeaderStores } from "./usage-header-test-utils";

/**
 * 顶栏入口挂在订阅项目事件流的工作台里；两者一起渲染，断言的是「事件到达后重新取数」
 * 这条端到端行为，而不是 hook 内部调了谁。
 */
function Harness() {
  useProjectEventsSSE(HEADER_PROJECT);
  return <UsageHeaderEntry projectName={HEADER_PROJECT} />;
}

function mockProjectEventStream() {
  let captured: ProjectEventStreamOptions | undefined;
  FakeSseStream.reset();
  vi.spyOn(API, "openProjectEventStream").mockImplementation((options) => {
    captured = options;
    return new FakeSseStream();
  });
  const connected = () => {
    if (!captured) throw new Error("事件流尚未建连");
    return captured;
  };
  return {
    get connected() {
      return captured !== undefined;
    },
    emitChanges: (changes: ProjectChange[]) =>
      connected().onChanges?.({ fingerprint: "f1", changes, source: "agent" } as never),
    emitSnapshot: (fingerprint: string) =>
      connected().onSnapshot?.({ fingerprint } as never),
  };
}

function usageRecordChange(): ProjectChange {
  return {
    entity_type: "usage_record",
    entity_id: "42",
    action: "recorded",
  } as ProjectChange;
}

function taskTerminalChange(): ProjectChange {
  return {
    entity_type: "task",
    entity_id: "t-1",
    action: "task_succeeded",
  } as ProjectChange;
}

function renderHarness() {
  const location = memoryLocation({ path: "/characters", record: true });
  return render(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <Harness />
    </Router>,
  );
}

describe("UsagePopover event-driven refresh", () => {
  beforeEach(() => {
    resetHeaderStores();
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    vi.spyOn(API, "getUsageRecords").mockResolvedValue({
      items: [],
      next_cursor: null,
      total: 0,
    });
    vi.spyOn(API, "getProject").mockResolvedValue({ project: null, scripts: {} } as never);
    useUsageHeaderStore.setState({ projectName: HEADER_PROJECT });
    useAppStore.setState({ usagePanelOpen: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("refetches the summary and the recent records on a settled usage record", async () => {
    const stream = mockProjectEventStream();
    renderHarness();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(1));

    act(() => stream.emitChanges([usageRecordChange()]));

    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(2));
    expect(API.getUsageRecords).toHaveBeenCalledWith(
      {
        projectName: HEADER_PROJECT,
        statuses: ["success", "failed", "cancelled"],
        limit: 10,
      },
      expect.anything(),
    );
  });

  it("refetches when a task reaches a terminal state", async () => {
    const stream = mockProjectEventStream();
    renderHarness();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(1));

    act(() => stream.emitChanges([taskTerminalChange()]));

    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(2));
  });

  it("refetches everything once the event stream reconnects", async () => {
    const stream = mockProjectEventStream();
    renderHarness();
    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(1));

    // 首个快照是建连，不是重连；此后每个快照都意味着断过一次线。
    act(() => stream.emitSnapshot("f1"));
    expect(API.getUsageSummary).toHaveBeenCalledTimes(1);

    act(() => stream.emitSnapshot("f1"));

    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(2));
  });

  it("never polls while no event arrives", async () => {
    vi.useFakeTimers();
    const stream = mockProjectEventStream();
    renderHarness();
    await vi.waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(1));
    expect(stream.connected).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(API.getUsageSummary).toHaveBeenCalledTimes(1);
  });
});
