import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageRecordsStore } from "@/stores/usage-records-store";
import {
  makeUsageAgentSession,
  makeUsageAgentSessionRecord,
  makeUsageSummary,
} from "./usage-fixtures";
import { renderUsageRecordsSection } from "./usage-test-utils";

const EMPTY_RECORDS = { items: [], next_cursor: null, total: 0 };

function lastQuery(history: readonly string[]): URLSearchParams {
  return new URLSearchParams(history.at(-1)?.split("?")[1] ?? "");
}

describe("UsageRecordsSection Agent sessions view", () => {
  beforeEach(() => {
    useUsageRecordsStore.setState(useUsageRecordsStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    vi.spyOn(API, "getUsageRecords").mockResolvedValue(EMPTY_RECORDS);
    vi.spyOn(API, "getUsageSessions").mockResolvedValue({
      items: [makeUsageAgentSession()],
      next_cursor: null,
      total: 1,
    });
  });

  it("starts on generation calls and only reads Agent sessions once the view is switched", async () => {
    const { location } = renderUsageRecordsSection();
    await waitFor(() => expect(API.getUsageRecords).toHaveBeenCalled());
    expect(vi.mocked(API.getUsageRecords).mock.calls.every(([query]) => query?.view === undefined)).toBe(true);
    expect(API.getUsageSessions).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "生成调用" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "Agent 会话" }));

    await waitFor(() => expect(lastQuery(location.history).get("u_view")).toBe("agent"));
    expect(await screen.findByText("分镜讨论")).toBeInTheDocument();
    expect(API.getUsageSummary).toHaveBeenLastCalledWith(
      expect.objectContaining({ view: "agent" }),
      expect.anything(),
    );
  });

  it("drops filters that do not exist in the other view when switching", async () => {
    const { location } = renderUsageRecordsSection("section=usage&u_provider=minimax&u_media=image");

    await userEvent.click(await screen.findByRole("button", { name: "Agent 会话" }));

    await waitFor(() => expect(lastQuery(location.history).get("u_view")).toBe("agent"));
    const query = lastQuery(location.history);
    expect(query.has("u_provider")).toBe(false);
    expect(query.has("u_media")).toBe(false);
    // Agent 记录都是文本，媒体类型下拉不再出现
    expect(screen.queryByRole("combobox", { name: "媒体类型" })).not.toBeInTheDocument();
  });

  it("shows one row per session with usage in the value and the priced total", async () => {
    renderUsageRecordsSection("section=usage&u_view=agent");

    const row = (await screen.findByText("分镜讨论")).closest("tr") as HTMLTableRowElement;
    const cells = within(row);
    expect(cells.getByText("deepseek-chat")).toBeInTheDocument();
    expect(cells.getByText("12.3 万 token")).toBeInTheDocument();
    expect(cells.getByText("$0.50")).toBeInTheDocument();
    expect(cells.queryByText("部分未定价")).not.toBeInTheDocument();
  });

  it("flags a session that has unpriced records under its reference cost", async () => {
    vi.mocked(API.getUsageSessions).mockResolvedValue({
      items: [makeUsageAgentSession({ unpriced_calls: 1 })],
      next_cursor: null,
      total: 1,
    });
    renderUsageRecordsSection("section=usage&u_view=agent");

    const row = (await screen.findByText("分镜讨论")).closest("tr") as HTMLTableRowElement;
    const cost = within(row).getByText("$0.50");
    expect(within(cost.closest("td") as HTMLElement).getByText("部分未定价")).toBeInTheDocument();
  });

  it("names a session whose metadata is gone", async () => {
    vi.mocked(API.getUsageSessions).mockResolvedValue({
      items: [makeUsageAgentSession({ title: null, session_deleted: true })],
      next_cursor: null,
      total: 1,
    });
    renderUsageRecordsSection("section=usage&u_view=agent");

    expect(await screen.findByText("已删除的会话")).toBeInTheDocument();
  });

  it("explains an empty period in terms of Agent sessions", async () => {
    vi.mocked(API.getUsageSessions).mockResolvedValue(EMPTY_RECORDS);
    renderUsageRecordsSection("section=usage&u_view=agent");

    expect(await screen.findByText("这段时间还没有 Agent 会话。")).toBeInTheDocument();
  });

  it("expands a session into per-record rows and marks the unpriced one", async () => {
    vi.spyOn(API, "getUsageSession").mockResolvedValue({
      session_id: "sess-1",
      items: [
        makeUsageAgentSessionRecord({
          id: 1,
          input_tokens: 1000,
          cache_read_tokens: 600,
          cache_write_5m_tokens: 100,
          cache_write_1h_tokens: 50,
          output_tokens: 200,
        }),
        makeUsageAgentSessionRecord({
          id: 2,
          model: "glm-5",
          cost_amount: null,
          unpriced_reason: "no_entry",
        }),
      ],
    });
    renderUsageRecordsSection("section=usage&u_view=agent");

    await userEvent.click(await screen.findByRole("button", { name: "展开「分镜讨论」的明细" }));

    const table = await screen.findByRole("table", { name: "「分镜讨论」的明细" });
    const rows = within(table).getAllByRole("row");
    // 表头 + 两条记录
    expect(rows).toHaveLength(3);
    const priced = within(rows[1]);
    // 「输入」不含缓存命中与缓存写入的部分：1000 - 600 - 100 - 50
    expect(priced.getByText("250")).toBeInTheDocument();
    expect(priced.getByText("600")).toBeInTheDocument();
    expect(priced.getByText("150")).toBeInTheDocument();
    expect(priced.getByText("200")).toBeInTheDocument();
    expect(priced.getByText("$0.0031")).toBeInTheDocument();
    expect(within(rows[2]).getByText("未定价")).toBeInTheDocument();
    // 原因放在悬停提示里
    await userEvent.hover(within(rows[2]).getByText("未定价"));
    expect(await screen.findByText("这个供应商模型没有对应的模型条目")).toBeInTheDocument();
    expect(API.getUsageSession).toHaveBeenCalledWith("sess-1", expect.anything());
  });

  it("keeps the two cache-write tiers in the single-row details", async () => {
    vi.spyOn(API, "getUsageSession").mockResolvedValue({
      session_id: "sess-1",
      items: [
        makeUsageAgentSessionRecord({ id: 1, cache_write_5m_tokens: 100, cache_write_1h_tokens: 50 }),
      ],
    });
    renderUsageRecordsSection("section=usage&u_view=agent");
    await userEvent.click(await screen.findByRole("button", { name: "展开「分镜讨论」的明细" }));
    const table = await screen.findByRole("table", { name: "「分镜讨论」的明细" });

    await userEvent.click(within(table).getByRole("button", { name: "详情" }));

    expect(within(table).getByText("5 分钟", { exact: false })).toBeInTheDocument();
    expect(within(table).getByText("1 小时", { exact: false })).toBeInTheDocument();
  });

  it("lists Agent sessions as one breakdown item that switches to the Agent view", async () => {
    vi.mocked(API.getUsageSummary).mockResolvedValue(
      makeUsageSummary({
        breakdown: {
          project: { rows: [], other: null },
          provider: {
            rows: [
              {
                provider: "",
                agent: true,
                calls: 9,
                success: 9,
                failed: 0,
                cancelled: 0,
                success_rate: 1,
                cost: { USD: 1.5 },
                unpriced_calls: 0,
              },
            ],
            other: null,
          },
          model: { rows: [], other: null },
        },
      }),
    );
    const { location } = renderUsageRecordsSection();
    const section = (await screen.findByRole("heading", { name: "构成" })).closest("section") as HTMLElement;

    await userEvent.click(await within(section).findByRole("button", { name: /Agent 会话/ }));

    await waitFor(() => expect(lastQuery(location.history).get("u_view")).toBe("agent"));
  });
});
