import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageRecordsStore } from "@/stores/usage-records-store";
import {
  makeUsageRecord,
  makeUsageRecordDetail,
  makeUsageSummary,
} from "./usage-fixtures";
import { renderUsageRecordsSection } from "./usage-test-utils";

describe("UsageRecordsSection detail", () => {
  beforeEach(() => {
    useUsageRecordsStore.setState(useUsageRecordsStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    // 进行中区的取数不带时间范围，只有记录表的那一次返回行。
    vi.spyOn(API, "getUsageRecords").mockImplementation(async (query) =>
      query?.since === undefined
        ? { items: [], next_cursor: null, total: 0 }
        : { items: [makeUsageRecord({ id: 42 })], next_cursor: null, total: 1 },
    );
  });

  it("opens the dialog straight from a record deep link", async () => {
    const detail = vi
      .spyOn(API, "getUsageRecord")
      .mockResolvedValue(makeUsageRecordDetail({ id: 42, segment_id: "E1S10" }));

    renderUsageRecordsSection("section=usage&record=42");

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(detail).toHaveBeenCalledWith(42, { signal: expect.any(AbortSignal) });
    expect(screen.getByText("Record · #42")).toBeInTheDocument();
  });

  it("renders every group of a finished text call", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42,
        media_type: "text",
        provider: "minimax",
        model: "hailuo-02",
        segment_id: null,
        purpose: "script_generation",
        prompt: "写一段月台戏",
        input_tokens: 120,
        output_tokens: 30,
        usage_tokens: 150,
        output_path: "星海列车/E1S10.txt",
        last_provider_response: { id: "resp-1" },
      }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(dialog.getByRole("heading", { name: "输入" })).toBeInTheDocument();
    expect(dialog.getByText("写一段月台戏")).toBeInTheDocument();
    expect(dialog.getByRole("heading", { name: "调用" })).toBeInTheDocument();
    expect(dialog.getByText("MiniMax")).toBeInTheDocument();
    expect(dialog.getByRole("heading", { name: "产出" })).toBeInTheDocument();
    expect(dialog.getByText("星海列车/E1S10.txt")).toBeInTheDocument();
    expect(dialog.getByRole("heading", { name: "用量" })).toBeInTheDocument();
    expect(dialog.getByText("150")).toBeInTheDocument();
    expect(dialog.getByText("按你配置的单价估算，只作参考")).toBeInTheDocument();
    expect(
      dialog.getByRole("button", { name: "供应商原始响应" }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("renders the production input field names for media calls", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42,
        media_type: "video",
        inputs: {
          reference_images: [
            { path: "characters/hero.png", label: "主角参考", role: "array" },
          ],
          start_image: "storyboards/E1S01.png",
          end_image: "storyboards/E1S02.png",
          reference_audio: ["characters/hero.wav"],
          parameters: { service_tier: "default" },
        },
      }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(dialog.getByRole("img", { name: "主角参考" })).toBeInTheDocument();
    expect(dialog.getByRole("img", { name: "首帧" })).toBeInTheDocument();
    expect(dialog.getByRole("img", { name: "尾帧" })).toBeInTheDocument();
    expect(dialog.getByText("characters/hero.wav")).toBeInTheDocument();
    expect(dialog.getByText(/"service_tier": "default"/)).toBeInTheDocument();
  });

  it("shows 无记录 for groups the record never captured", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, prompt: null, inputs: null, output_path: null }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    await screen.findByRole("dialog");

    // 输入与产出两组都无数据：显示「无记录」而不是消失或报错。
    expect(screen.getAllByText("无记录")).toHaveLength(2);
  });

  it("renders the failure phrase above the raw message", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42,
        status: "failed",
        error_code: "rate_limited",
        error_params: { retry_after_seconds: 30 },
        error_message: "HTTP 429",
      }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    await screen.findByRole("dialog");

    expect(screen.getByRole("heading", { name: "失败原因" })).toBeInTheDocument();
    expect(screen.getByText("限流")).toBeInTheDocument();
    expect(screen.getByText("重试等待")).toBeInTheDocument();
    expect(screen.getByText("30 s")).toBeInTheDocument();
    expect(screen.getByText("HTTP 429")).toBeInTheDocument();
  });

  it("does not reset pagination when opening or closing a record", async () => {
    vi.mocked(API.getUsageRecords).mockImplementation(async (query) => {
      if (query?.since === undefined) return { items: [], next_cursor: null, total: 0 };
      return query.cursor
        ? { items: [makeUsageRecord({ id: 42 })], next_cursor: null, total: 40 }
        : { items: [makeUsageRecord({ id: 1 })], next_cursor: "cursor-2", total: 40 };
    });
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(makeUsageRecordDetail({ id: 42 }));

    renderUsageRecordsSection("section=usage");
    await userEvent.click(await screen.findByRole("button", { name: "下一页" }));
    expect(await screen.findByText("21–21 / 40")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "详情" }));
    await screen.findByRole("dialog");
    expect(screen.getByText("21–21 / 40")).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("21–21 / 40")).toBeInTheDocument();
  });

  it("closes on Escape and drops the record param from the URL", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(makeUsageRecordDetail({ id: 42 }));

    const { location } = renderUsageRecordsSection("section=usage&record=42");
    await screen.findByRole("dialog");

    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(location.history.at(-1)).toBe("/app/settings?section=usage"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the dialog from the row action and writes the id into the URL", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(makeUsageRecordDetail({ id: 42 }));

    const { location } = renderUsageRecordsSection("section=usage");
    await screen.findByRole("button", { name: "详情" });

    await userEvent.click(screen.getByRole("button", { name: "详情" }));

    await waitFor(() =>
      expect(location.history.at(-1)).toBe("/app/settings?section=usage&record=42"),
    );
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
