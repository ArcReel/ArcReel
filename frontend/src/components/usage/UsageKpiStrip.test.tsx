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

    // Breakdowns/trend tooltips/popovers share formatRatio, always comma decimals in vi.
    expect(screen.getByText("90,3%")).toBeInTheDocument();
  });

  it("renders the counts with the language's thousands separator", async () => {
    // Call counts/failures/success rates share KPI rows; separators follow UI languages, not browser languages.
    for (const [language, calls, failed] of [
      ["zh", "12,340", "1,205"],
      ["en", "12,340", "1,205"],
      ["vi", "12.340", "1.205"],
    ] as const) {
      await i18n.changeLanguage(language);
      const summary = makeUsageSummary();
      const { unmount } = render(
        <UsageKpiStrip
          summary={{ ...summary, kpi: { ...summary.kpi, calls: 12_340, failed: 1_205 } }}
        />,
      );
      expect(screen.getByText(calls)).toBeInTheDocument();
      expect(screen.getByText(failed)).toBeInTheDocument();
      unmount();
    }
  });

  it("renders the range subline as dates in the current language", async () => {
    // Raw `2026-02-14 – 2026-03-15` would be identical across three languages; mount each language separately.
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

  it("lists the unpriced call count apart from the cost total", () => {
    const summary = makeUsageSummary();
    render(<UsageKpiStrip summary={{ ...summary, kpi: { ...summary.kpi, unpriced_calls: 3 } }} />);

    expect(screen.getByText(/另 3 次未定价/)).toBeInTheDocument();
  });

  it("falls back to dashes with no summary", () => {
    render(<UsageKpiStrip summary={null} />);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});
