import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { makeUsageRecord, makeUsageSummary } from "@/components/usage/usage-fixtures";
import { createDeferred } from "@/test/deferred";
import type { UsageSummary } from "@/types";
import { useUsageHeaderStore } from "./usage-header-store";

function stubRecords() {
  return vi.spyOn(API, "getUsageRecords").mockResolvedValue({
    items: [makeUsageRecord()],
    next_cursor: null,
    total: 1,
  });
}

describe("usage-header-store", () => {
  beforeEach(() => {
    useUsageHeaderStore.setState(useUsageHeaderStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("loads the summary, the recent records and the taskless pending calls", async () => {
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    const recordsSpy = stubRecords();

    await useUsageHeaderStore.getState().setProject("星海列车");

    const state = useUsageHeaderStore.getState();
    expect(state.summary?.primary_currency).toBe("CNY");
    expect(state.recent).toHaveLength(1);
    expect(state.pending).toHaveLength(1);
    expect(recordsSpy).toHaveBeenCalledWith(
      { projectName: "星海列车", statuses: ["pending"] },
      expect.anything(),
    );
  });

  it("refreshes again when the same project is registered after a remount", async () => {
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    stubRecords();
    await useUsageHeaderStore.getState().setProject("星海列车");

    // 顶栏从设置页返回后重新登记同一项目：离开期间的事件已错过，要补一轮重取。
    await useUsageHeaderStore.getState().setProject("星海列车");

    expect(API.getUsageSummary).toHaveBeenCalledTimes(2);
    expect(useUsageHeaderStore.getState().summary?.primary_currency).toBe("CNY");
  });

  it("clears the previous project and issues no request for the demo project", async () => {
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    stubRecords();
    await useUsageHeaderStore.getState().setProject("星海列车");

    await useUsageHeaderStore.getState().setProject(null);

    const state = useUsageHeaderStore.getState();
    expect(state.summary).toBeNull();
    expect(state.recent).toEqual([]);
    expect(API.getUsageSummary).toHaveBeenCalledTimes(1);
  });

  it("discards a summary that resolves after the project has switched", async () => {
    const pending: {
      projectName: string | undefined;
      resolve: (summary: UsageSummary) => void;
    }[] = [];
    vi.spyOn(API, "getUsageSummary").mockImplementation(
      (query, options) =>
        new Promise((resolve, reject) => {
          options?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
          pending.push({ projectName: query?.projectName, resolve });
        }),
    );
    stubRecords();

    void useUsageHeaderStore.getState().setProject("星海列车");
    await vi.waitFor(() => expect(pending).toHaveLength(1));

    void useUsageHeaderStore.getState().setProject("雨夜侦探");
    await vi.waitFor(() => expect(pending).toHaveLength(2));

    // 迟到的第一轮响应属于已离开的项目，不得写回。
    pending[0].resolve(makeUsageSummary({ primary_currency: "JPY" }));
    pending[1].resolve(makeUsageSummary({ primary_currency: "USD" }));

    await vi.waitFor(() =>
      expect(useUsageHeaderStore.getState().summary?.primary_currency).toBe("USD"),
    );
  });

  it("keeps the last detail request when several rows are opened in a row", async () => {
    const first = createDeferred<ReturnType<typeof makeUsageRecord> & {
      prompt: null;
      inputs: null;
      last_provider_response: null;
    }>();
    const second = createDeferred<ReturnType<typeof makeUsageRecord> & {
      prompt: null;
      inputs: null;
      last_provider_response: null;
    }>();
    vi.spyOn(API, "getUsageRecord").mockImplementation((recordId) =>
      recordId === 7 ? first.promise : second.promise,
    );

    const firstRequest = useUsageHeaderStore.getState().openDetail(7);
    const secondRequest = useUsageHeaderStore.getState().openDetail(9);
    second.resolve({
      ...makeUsageRecord({ id: 9 }),
      prompt: null,
      inputs: null,
      last_provider_response: null,
    });
    await secondRequest;
    first.resolve({
      ...makeUsageRecord({ id: 7 }),
      prompt: null,
      inputs: null,
      last_provider_response: null,
    });
    await firstRequest;

    expect(useUsageHeaderStore.getState().detail?.id).toBe(9);

    useUsageHeaderStore.getState().closeDetail();
    expect(useUsageHeaderStore.getState().detail).toBeNull();
  });
});
