import { afterEach, describe, expect, it } from "vitest";

import i18n from "@/i18n";
import { formatElapsedMs } from "@/utils/task-elapsed";
import { formatCalendarDay, formatDurationMs, formatRatio } from "./usage-record-format";

/** 取当前语言的 dashboard 取词器，行为与组件里的 `useTranslation("dashboard")` 一致。 */
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
    // vi 用逗号作小数点；固定的 `90.3%` 在越南语界面里与按语言渲染的 KPI 对不上。
    expect(formatRatio(0.9028, "vi")).toBe("90,3%");
  });

  it("keeps one decimal and shows a dash when the denominator is zero", () => {
    expect(formatRatio(0.5, "en")).toBe("50%");
    expect(formatRatio(0.12345, "en")).toBe("12.3%");
    expect(formatRatio(null, "en")).toBe("—");
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
    // `new Date("2026-02-14")` 是 UTC 午夜，东八区渲染出来会是 14 日、西五区是 13 日。
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
    // 记录行的耗时与任务读数共用 `common:elapsed_*`：同一段时长全站只有一种写法，
    // 且超过一小时后给时分而不是「90 分 0 秒」。
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
