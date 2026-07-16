import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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

describe("UsageStatsSection provider filter dropdown (issue #1179)", () => {
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
});
