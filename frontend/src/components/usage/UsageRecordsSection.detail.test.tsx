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

    expect(await screen.findByRole("dialog", { name: /S10\s*未命名集/ })).toBeInTheDocument();
    expect(detail).toHaveBeenCalledWith(42, { signal: expect.any(AbortSignal) });
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
    expect(dialog.getByText("星海列车/S10.txt")).toBeInTheDocument();
    expect(dialog.getByRole("heading", { name: "用量" })).toBeInTheDocument();
    expect(dialog.getByText("150")).toBeInTheDocument();
    expect(dialog.getByText("按你配置的单价估算，只作参考")).toBeInTheDocument();
    expect(
      dialog.getByRole("button", { name: "供应商原始响应" }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("links the model to its Sheet in the provider settings", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, provider: "custom-7", model: "relay/veo-3" }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(dialog.getByRole("link", { name: "relay/veo-3" })).toHaveAttribute(
      "href",
      expect.stringContaining("section=providers&custom=7&model=relay%2Fveo-3"),
    );
  });

  it("lists the four token counts with input excluding the cached parts", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42,
        media_type: "text",
        segment_id: null,
        purpose: "script_generation",
        input_tokens: 1_000,
        output_tokens: 30,
        cache_read_tokens: 600,
        cache_write_5m_tokens: 100,
        cache_write_1h_tokens: 50,
      }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    const valueOf = (label: string) =>
      (dialog.getByText(label, { selector: "span" }).closest("div") as HTMLElement).textContent?.replace(label, "");
    // 记录里的总输入 1000 含缓存命中 600 与缓存写入 150，「输入」只剩没缓存的 250。
    expect(valueOf("输入")).toBe("250");
    expect(valueOf("缓存命中")).toBe("600");
    expect(valueOf("缓存写入")).toBe("150");
    expect(valueOf("5 分钟")).toBe("100");
    expect(valueOf("1 小时")).toBe("50");
    expect(valueOf("输出")).toBe("30");

    await userEvent.hover(dialog.getByText("输入", { selector: "span" }));
    expect(await screen.findByText("不含缓存命中与缓存写入的部分")).toBeInTheDocument();
  });

  it("shows dashes for cache counts the provider never reported", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, media_type: "text", input_tokens: 120, output_tokens: 30 }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    const valueOf = (label: string) =>
      (dialog.getByText(label, { selector: "span" }).closest("div") as HTMLElement).textContent?.replace(label, "");
    expect(valueOf("输入")).toBe("120");
    expect(valueOf("缓存命中")).toBe("—");
    expect(valueOf("缓存写入")).toBe("—");
    expect(dialog.queryByText("5 分钟")).not.toBeInTheDocument();
  });

  it("shows 未定价 with its reason instead of a zero amount", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, cost_amount: null, unpriced_reason: "no_price_table" }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(dialog.getByText("未定价")).toBeInTheDocument();
    expect(dialog.getByText("未定价原因")).toBeInTheDocument();
    expect(dialog.getByText("模型条目没有价目表")).toBeInTheDocument();
    expect(dialog.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("shows a zero amount, with no unpriced reason, for a call priced at zero", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, cost_amount: 0 }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(await dialog.findByText("$0.00")).toBeInTheDocument();
    expect(dialog.queryByText("未定价")).not.toBeInTheDocument();
    expect(dialog.queryByText("未定价原因")).not.toBeInTheDocument();
  });

  it("resolves a project-relative image output as a thumbnail", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42,
        project_name: "demo",
        media_type: "image",
        output_path: "storyboards/scene_E1S10.png",
      }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(dialog.getByRole("img", { name: "storyboards/scene_S10.png" })).toHaveAttribute(
      "src",
      "/api/v1/files/demo/storyboards/scene_E1S10.png?w=320",
    );
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
