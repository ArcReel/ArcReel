import { afterEach, describe, expect, it } from "vitest";

import i18n from "@/i18n";
import { formatElapsedMs } from "@/utils/task-elapsed";
import {
  formatCalendarDay,
  formatCount,
  formatDurationMs,
  formatRatio,
  formatTokenAmount,
  usageProjectLabel,
} from "./usage-record-format";

/** Current-language dashboard translators, matching component `useTranslation("dashboard")` behavior. */
function dashboardT() {
  return i18n.getFixedT(null, "dashboard");
}

afterEach(async () => {
  await i18n.changeLanguage("zh");
});

describe("formatRatio", () => {
  it("renders the percentage by language so one page never mixes two formats", () => {
    expect(formatRatio(0.9028, "zh")).toBe("90.3%");
    expect(formatRatio(0.9028, "en")).toBe("90.3%");
    // vi uses comma decimals; fixed `90.3%` contradicts localized Vietnamese KPIs.
    expect(formatRatio(0.9028, "vi")).toBe("90,3%");
  });

  it("keeps one decimal and shows a dash when the denominator is zero", () => {
    expect(formatRatio(0.5, "en")).toBe("50%");
    expect(formatRatio(0.12345, "en")).toBe("12.3%");
    expect(formatRatio(null, "en")).toBe("—");
  });
});

describe("formatCount", () => {
  it("renders the thousands separator by language", () => {
    expect(formatCount(12_340, "zh")).toBe("12,340");
    expect(formatCount(12_340, "en")).toBe("12,340");
    // vi uses grouping periods; browser-language `toLocaleString()` would show `12,340` in Vietnamese UIs.
    expect(formatCount(12_340, "vi")).toBe("12.340");
  });

  it("leaves small numbers unseparated", () => {
    expect(formatCount(0, "vi")).toBe("0");
    expect(formatCount(73, "vi")).toBe("73");
  });
});

describe("formatCalendarDay", () => {
  const options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "short",
    day: "numeric",
  };

  it("renders the calendar day by language", () => {
    expect(formatCalendarDay("2026-02-14", "zh", options)).toBe("2026年2月14日");
    expect(formatCalendarDay("2026-02-14", "en", options)).toBe("Feb 14, 2026");
    expect(formatCalendarDay("2026-02-14", "vi", options)).toBe("14 thg 2, 2026");
  });

  it("reads the day in local time so the date never shifts", () => {
    // `new Date("2026-02-14")` is UTC midnight, rendering day 14 in UTC+8 and 13 in UTC-5.
    expect(formatCalendarDay("2026-02-14", "en", { day: "numeric" })).toBe("14");
  });
});

describe("formatDurationMs", () => {
  it("translates the units", async () => {
    expect(formatDurationMs(12_400, dashboardT())).toBe("12秒");
    expect(formatDurationMs(185_000, dashboardT())).toBe("3分5秒");

    await i18n.changeLanguage("en");
    expect(formatDurationMs(12_400, dashboardT())).toBe("12s");
    expect(formatDurationMs(185_000, dashboardT())).toBe("3m 5s");

    await i18n.changeLanguage("vi");
    expect(formatDurationMs(12_400, dashboardT())).toBe("12 giây");
    expect(formatDurationMs(185_000, dashboardT())).toBe("3 phút 5 giây");
  });

  it("reads the same as the task elapsed readout, hours included", () => {
    // Record/task durations share `common:elapsed_*`, ensuring one sitewide duration representation,
    // and hours/minutes rather than 90 minutes 0 seconds beyond one hour.
    expect(formatDurationMs(5_400_000, dashboardT())).toBe("1时30分");
    expect(formatDurationMs(5_400_000, dashboardT())).toBe(
      formatElapsedMs(5_400_000, dashboardT()),
    );
  });

  it("shows a dash when there is no duration to show", () => {
    expect(formatDurationMs(null, dashboardT())).toBe("—");
    expect(formatDurationMs(-1, dashboardT())).toBe("—");
  });
});

describe("usageProjectLabel", () => {
  it("shows a deleted project by its old name and deletion date, not the stored tombstone", () => {
    const t = dashboardT();
    expect(usageProjectLabel("demo#deleted-20261002T120000Z", t, "zh")).toBe("demo（2026年10月2日删除）");
  });

  it("keeps live project names as they are and labels endpoint trials as untitled", async () => {
    await i18n.changeLanguage("en");
    const t = dashboardT();
    expect(usageProjectLabel("demo", t, "en")).toBe("demo");
    expect(usageProjectLabel("", t, "en")).toBe("Untitled");
    expect(usageProjectLabel("demo#deleted-20261002T120000Z", t, "en")).toBe("demo (deleted Oct 2, 2026)");
  });

  it("shows the project title when there is one and falls back to the stored name otherwise", () => {
    const t = dashboardT();
    expect(usageProjectLabel("rain-detective", t, "zh", "雨夜侦探")).toBe("雨夜侦探");
    expect(usageProjectLabel("rain-detective", t, "zh", null)).toBe("rain-detective");
    // Deleted projects lack readable titles, falling back to original names/deletion dates.
    expect(usageProjectLabel("demo#deleted-20261002T120000Z", t, "zh", null)).toBe("demo（2026年10月2日删除）");
  });
});

describe("formatTokenAmount", () => {
  it("writes the unit into the value, carrying to 万 and 亿 in Chinese", () => {
    const t = dashboardT();
    expect(formatTokenAmount(950, "zh", t)).toBe("950 token");
    expect(formatTokenAmount(123_000, "zh", t)).toBe("12.3 万 token");
    expect(formatTokenAmount(120_000, "zh", t)).toBe("12 万 token");
    expect(formatTokenAmount(250_000_000, "zh", t)).toBe("2.5 亿 token");
  });

  it("uses compact notation in the other languages", async () => {
    await i18n.changeLanguage("en");

    expect(formatTokenAmount(123_000, "en", dashboardT())).toBe("123K tokens");
  });
});
