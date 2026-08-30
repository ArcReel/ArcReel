import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { UsageStatsSection } from "./UsageStatsSection";
import type { UsageStatsResponse } from "@/types";

const STATS_RESPONSE: UsageStatsResponse = {
  stats: [
    {
      provider: "custom-1",
      display_name: "我的自定义供应商",
      call_type: "image",
      total_calls: 10,
      success_calls: 9,
      total_cost_usd: 1.2,
      cost_by_currency: { USD: 1.2 },
    },
    {
      provider: "gemini",
      display_name: "Gemini",
      call_type: "video",
      total_calls: 5,
      success_calls: 5,
      total_cost_usd: 0.5,
      cost_by_currency: { USD: 0.5 },
    },
  ],
  period: { start: "2026-07-01", end: "2026-07-16" },
};

describe("UsageStatsSection provider filter dropdown", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/");
  });

  it("renders provider options by display_name while keeping provider id as value", async () => {
    vi.spyOn(API, "getUsageStatsGrouped").mockResolvedValue(STATS_RESPONSE);

    render(<UsageStatsSection />);
    await waitFor(() => expect(API.getUsageStatsGrouped).toHaveBeenCalled());

    const select = await screen.findByRole("combobox");
    const options = Array.from(select.querySelectorAll("option"));
    const byValue = Object.fromEntries(options.map((o) => [o.value, o.textContent]));

    // 自定义供应商显示用户配置的名称，而不是内部 custom-1 id
    expect(byValue["custom-1"]).toBe("我的自定义供应商");
    // 内置供应商显示注册表名称
    expect(byValue["gemini"]).toBe("Gemini");
    // value 保持 provider 原值，筛选行为不受影响
    expect(Object.keys(byValue).sort()).toEqual(["", "custom-1", "gemini"]);
  });

  it("falls back to the raw provider id when display_name is missing", async () => {
    vi.spyOn(API, "getUsageStatsGrouped").mockResolvedValue({
      stats: [
        {
          provider: "legacy-provider",
          call_type: "image",
          total_calls: 1,
          success_calls: 1,
          total_cost_usd: 0.1,
          cost_by_currency: { USD: 0.1 },
        },
      ],
      period: { start: "2026-07-01", end: "2026-07-16" },
    });

    render(<UsageStatsSection />);
    await waitFor(() => expect(API.getUsageStatsGrouped).toHaveBeenCalled());

    const select = await screen.findByRole("combobox");
    const option = select.querySelector('option[value="legacy-provider"]');
    expect(option?.textContent).toBe("legacy-provider");
  });

  it("shows the call selected by a spend-ledger deep link", async () => {
    window.history.replaceState({}, "", "/app/settings?section=usage&call_id=42#usage-call-42");
    vi.spyOn(API, "getUsageStatsGrouped").mockResolvedValue(STATS_RESPONSE);
    vi.spyOn(API, "getUsageCalls").mockResolvedValue({
      items: [
        {
          id: "42",
          project_name: "__endpoint_trial__",
          call_type: "video",
          model: "video-1",
          status: "success",
          cost_amount: 0.12,
          currency: "USD",
          provider: "custom-1",
          output_path: null,
          resolution: null,
          duration_seconds: 5,
          duration_ms: 1000,
          error_message: null,
          started_at: "2026-08-30T00:00:00Z",
          created_at: "2026-08-30T00:00:00Z",
          usage_tokens: null,
          input_tokens: null,
          output_tokens: null,
        },
      ],
      total: 1,
    });

    render(<UsageStatsSection />);

    await waitFor(() => expect(API.getUsageCalls).toHaveBeenCalledWith(
      { callId: 42, pageSize: 1 },
      { signal: expect.any(AbortSignal) },
    ));
    expect(await screen.findByText("费用账本 #42")).toBeInTheDocument();
    expect(screen.getByText("video-1")).toBeInTheDocument();
    expect(screen.getByText("成功")).toBeInTheDocument();
    expect(screen.queryByText("success")).not.toBeInTheDocument();
  });
});
