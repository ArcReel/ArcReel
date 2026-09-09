import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import i18n from "@/i18n";
import { UsageKpiStrip } from "./UsageKpiStrip";
import { makeUsageSummary } from "./usage-fixtures";

afterEach(async () => {
  await i18n.changeLanguage("zh");
});

describe("UsageKpiStrip", () => {
  it("renders the success rate with the same formatter as the rest of the page", async () => {
    await i18n.changeLanguage("vi");

    render(<UsageKpiStrip summary={makeUsageSummary()} />);

    // 构成表、趋势 tooltip 与悬浮层同走 formatRatio，vi 下一律逗号小数点。
    expect(screen.getByText("90,3%")).toBeInTheDocument();
  });

  it("renders the range subline as dates in the current language", async () => {
    // 原样直出的 `2026-02-14 – 2026-03-15` 三语一个样，这里逐语言各挂一次。
    for (const [language, expected] of [
      ["zh", "2026年2月14日 – 2026年3月15日"],
      ["en", "Feb 14, 2026 – Mar 15, 2026"],
      ["vi", "14 thg 2, 2026 – 15 thg 3, 2026"],
    ] as const) {
      await i18n.changeLanguage(language);
      const { unmount } = render(<UsageKpiStrip summary={makeUsageSummary()} />);
      expect(screen.getByText(expected)).toBeInTheDocument();
      unmount();
    }
  });

  it("falls back to dashes with no summary", () => {
    render(<UsageKpiStrip summary={null} />);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});
