import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageHeaderStore } from "@/stores/usage-header-store";
import { makeTask } from "@/test/factories";
import type { UsageRecord, UsageSummary } from "@/types";
import { makeUsageRecord, makeUsageRecordDetail, makeUsageSummary } from "./usage-fixtures";
import {
  HEADER_PROJECT,
  renderUsageHeaderEntry,
  resetHeaderStores,
} from "./usage-header-test-utils";

const EMPTY_KPI = {
  calls: 0,
  success: 0,
  failed: 0,
  cancelled: 0,
  success_rate: null,
  cost: {},
};

function stubUsageApi() {
  vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
  vi.spyOn(API, "getUsageRecords").mockResolvedValue({
    items: [],
    next_cursor: null,
    total: 0,
  });
}

/** 打开悬浮层并把当前项目的数据灌进 store。 */
function openPopover(state: {
  summary?: UsageSummary;
  recent?: UsageRecord[];
  pending?: UsageRecord[];
}) {
  useUsageHeaderStore.setState({
    projectName: HEADER_PROJECT,
    summary: state.summary ?? makeUsageSummary(),
    recent: state.recent ?? [],
    pending: state.pending ?? [],
  });
  useAppStore.setState({ usagePanelOpen: true });
  return renderUsageHeaderEntry();
}

describe("UsagePopover", () => {
  beforeEach(() => {
    resetHeaderStores();
    vi.restoreAllMocks();
    stubUsageApi();
  });

  afterEach(async () => {
    await i18n.changeLanguage("zh");
  });

  it("shows only the guidance line and the records link when nothing was ever called", () => {
    openPopover({ summary: makeUsageSummary({ kpi: EMPTY_KPI, primary_currency: null }) });

    expect(
      screen.getByText("本项目还没有使用记录。开始生成后，这里会显示进行中与已结束的调用。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看全部记录" })).toBeInTheDocument();
    expect(screen.queryByText("调用次数")).not.toBeInTheDocument();
    expect(screen.queryByText("进行中")).not.toBeInTheDocument();
  });

  it("keeps a placeholder in the finished column while only tasks are running", () => {
    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "t-1",
          project_name: HEADER_PROJECT,
          status: "running",
          task_type: "storyboard",
          media_type: "image",
          resource_id: "E1S10",
        }),
      ],
    });
    openPopover({ summary: makeUsageSummary({ kpi: EMPTY_KPI }) });

    expect(screen.getByText("分镜 E1S10")).toBeInTheDocument();
    expect(screen.getByText("还没有已结束的调用。")).toBeInTheDocument();
    expect(screen.getByText("本项目 · 全部")).toBeInTheDocument();
    expect(screen.getByText("0 成功")).toBeInTheDocument();
  });

  it("keeps a placeholder in the in-progress column while only finished calls exist", () => {
    openPopover({ recent: [makeUsageRecord({ id: 7, segment_id: "E1S11" })] });

    expect(screen.getByText("当前没有进行中的调用。")).toBeInTheDocument();
    expect(screen.getByText("分镜 E1S11")).toBeInTheDocument();
  });

  it("opens the same detail modal as the settings page from a finished row", async () => {
    const detailSpy = vi
      .spyOn(API, "getUsageRecord")
      .mockResolvedValue(makeUsageRecordDetail({ id: 7, prompt: "一只在雨里的猫" }));
    openPopover({ recent: [makeUsageRecord({ id: 7, segment_id: "E1S11" })] });

    fireEvent.click(screen.getByText("分镜 E1S11"));

    await waitFor(() => expect(detailSpy).toHaveBeenCalledWith(7, expect.anything()));
    expect(await screen.findByText("一只在雨里的猫")).toBeInTheDocument();
  });

  it("puts a failed-call phrase beside its target", () => {
    openPopover({
      recent: [
        makeUsageRecord({ id: 7, segment_id: "E1S11", status: "failed", error_code: "timeout" }),
      ],
    });

    expect(screen.getByText("分镜 E1S11").parentElement).toHaveTextContent("超时");
  });

  it("renders the success rate in the same format as the settings page", async () => {
    await i18n.changeLanguage("vi");

    openPopover({});

    // 悬浮层与设置页 KPI 同走 formatRatio，vi 下都是逗号小数点。
    expect(screen.getByText("90,3%")).toBeInTheDocument();
  });

  it("renders the counts with the language's thousands separator", async () => {
    // 悬浮层 KPI 行与设置页同一条约束：调用次数、失败数跟界面语言，不跟浏览器语言。
    for (const [language, calls, failed] of [
      ["zh", "12,340", "1,205"],
      ["en", "12,340", "1,205"],
      ["vi", "12.340", "1.205"],
    ] as const) {
      await i18n.changeLanguage(language);
      const base = makeUsageSummary();
      const summary = makeUsageSummary({
        kpi: { ...base.kpi, calls: 12_340, failed: 1_205 },
      });
      // 顶栏入口挂载时会重取一轮 summary，让它落回同一份计数。
      vi.mocked(API.getUsageSummary).mockResolvedValue(summary);

      const { unmount } = openPopover({ summary });

      expect(screen.getByText(calls)).toBeInTheDocument();
      expect(screen.getByText(failed)).toBeInTheDocument();
      unmount();
    }
  });

  it("says so when a refresh fails, and clears the notice once a retry succeeds", async () => {
    vi.mocked(API.getUsageSummary).mockRejectedValueOnce(new Error("network down"));

    openPopover({});

    const notice = await screen.findByText(
      "部分数据加载失败，显示的可能不是最新结果。",
    );
    expect(notice).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));

    await waitFor(() =>
      expect(
        screen.queryByText("部分数据加载失败，显示的可能不是最新结果。"),
      ).not.toBeInTheDocument(),
    );
  });

  it("navigates to the settings records section prefilled with this project", () => {
    const { location } = openPopover({});

    fireEvent.click(screen.getByRole("button", { name: "查看全部记录" }));

    expect(location.history.at(-1)).toBe(
      `/app/settings?section=usage&u_project=${encodeURIComponent(HEADER_PROJECT)}`,
    );
    expect(useAppStore.getState().usagePanelOpen).toBe(false);
  });

  it("retries a failed download through the tasks API and refetches the records", async () => {
    const failedRecord = makeUsageRecord({
      id: 8,
      segment_id: "E1S12",
      status: "failed",
      error_code: "download_failed",
      task_id: "t-9",
    });
    const retrySpy = vi
      .spyOn(API, "retryTaskDownload")
      .mockResolvedValue({ task: makeTask({ task_id: "t-9", status: "running" }) });
    vi.mocked(API.getUsageRecords).mockImplementation(async (query) => ({
      items: query?.statuses?.includes("failed") ? [failedRecord] : [],
      next_cursor: null,
      total: query?.statuses?.includes("failed") ? 1 : 0,
    }));
    openPopover({
      recent: [failedRecord],
    });

    await waitFor(() => expect(API.getUsageSummary).toHaveBeenCalledTimes(1));
    const callsBeforeRetry = vi.mocked(API.getUsageSummary).mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "重试下载" }));

    await waitFor(() => expect(retrySpy).toHaveBeenCalledWith("t-9"));
    await waitFor(() =>
      expect(API.getUsageSummary).toHaveBeenCalledTimes(callsBeforeRetry + 1),
    );
  });
});
