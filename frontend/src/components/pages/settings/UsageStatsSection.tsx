
import { useState, useEffect, useMemo, useCallback, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { API } from "@/api";
import type { UsageStat } from "@/types";

const currencyFmt = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

const CARD_STYLE: CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.55), oklch(0.16 0.010 265 / 0.55))",
};

export function UsageStatsSection() {
  const { t, i18n } = useTranslation("dashboard");
  const [stats, setStats] = useState<UsageStat[]>([]);
  const [loading, setLoading] = useState(true);
  const [timeRange, setTimeRange] = useState(7);
  const [providerFilter, setProviderFilter] = useState<string>("");

  const percentFmt = useMemo(() => {
    const lang = i18n.language.split("-")[0];
    const localeMap: Record<string, string> = { zh: "zh-CN", en: "en-US", vi: "vi-VN" };
    const locale = localeMap[lang] ?? "en-US";
    return new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 });
  }, [i18n.language]);

  const TIME_RANGES = useMemo(
    () => [
      { label: t("last_7_days"), days: 7 },
      { label: t("last_30_days"), days: 30 },
      { label: t("all"), days: 0 },
    ],
    [t],
  );

  const fetchStats = useCallback(async () => {
    setLoading(true);
    const params: { provider?: string; start?: string; end?: string } = {};
    if (providerFilter) params.provider = providerFilter;
    if (timeRange > 0) {
      const start = new Date();
      start.setDate(start.getDate() - timeRange);
      params.start = start.toISOString().split("T")[0];
      params.end = new Date().toISOString().split("T")[0];
    }
    try {
      const res = await API.getUsageStatsGrouped(params);
      setStats(res.stats || []);
    } catch {
      setStats([]);
    }
    setLoading(false);
  }, [timeRange, providerFilter]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 依赖变化时重新获取统计数据，fetchStats 内部有 setState
    void fetchStats();
  }, [fetchStats]);

  const providers = useMemo(
    () => Array.from(new Set(stats.map((s) => s.provider))).sort(),
    [stats],
  );

  // Aggregate totals — used for the editorial header summary card
  const totals = useMemo(() => {
    let cost = 0;
    let calls = 0;
    let success = 0;
    for (const s of stats) {
      cost += s.total_cost_usd;
      calls += s.total_calls;
      success += s.success_calls;
    }
    return { cost, calls, success };
  }, [stats]);

  return (
    <div className="space-y-7">
      {/* Heading */}
      <div>
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
          Spend Ledger
        </div>
        <h3
          className="font-editorial mt-1"
          style={{
            fontWeight: 400,
            fontSize: 22,
            lineHeight: 1.1,
            letterSpacing: "-0.012em",
            color: "var(--color-text)",
          }}
        >
          {t("usage_stats")}
        </h3>
        <p className="mt-1.5 text-[12.5px] leading-[1.6] text-text-3">
          {t("usage_stats_by_provider")}
        </p>
      </div>

      {/* Totals strip */}
      <div
        className="grid grid-cols-3 overflow-hidden rounded-[10px] border border-hairline"
        style={CARD_STYLE}
      >
        {[
          { label: "Total Cost", value: currencyFmt.format(totals.cost) },
          { label: "Total Calls", value: totals.calls.toLocaleString() },
          {
            label: "Success Rate",
            value:
              totals.calls > 0 ? percentFmt.format(totals.success / totals.calls) : "—",
          },
        ].map((kpi, i) => (
          <div
            key={kpi.label}
            className={"px-5 py-4" + (i > 0 ? " border-l border-hairline-soft" : "")}
          >
            <div className="font-mono text-[9.5px] font-bold uppercase tracking-[0.18em] text-text-4">
              {kpi.label}
            </div>
            <div
              className="font-editorial mt-1"
              style={{
                fontSize: 22,
                fontWeight: 400,
                letterSpacing: "-0.01em",
                lineHeight: 1.1,
                color: "var(--color-text)",
              }}
            >
              {kpi.value}
            </div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        {TIME_RANGES.map((r) => {
          const active = timeRange === r.days;
          return (
            <button
              key={r.days}
              type="button"
              onClick={() => setTimeRange(r.days)}
              aria-pressed={active}
              className={
                "rounded-[7px] border px-3 py-1.5 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
                (active
                  ? "border-accent/45 bg-accent-dim text-accent-2"
                  : "border-hairline-soft bg-bg-grad-a/45 text-text-3 hover:border-hairline hover:text-text")
              }
              style={
                active
                  ? {
                      boxShadow: "0 0 18px -8px var(--color-accent-glow)",
                    }
                  : undefined
              }
            >
              {r.label}
            </button>
          );
        })}
        {providers.length > 0 && (
          <select
            value={providerFilter}
            onChange={(e) => setProviderFilter(e.target.value)}
            aria-label={t("filter_by_provider")}
            className="rounded-[7px] border border-hairline-soft bg-bg-grad-a/45 px-3 py-1.5 text-[12px] text-text-2 transition-colors hover:border-hairline focus:border-accent/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <option value="">{t("all_providers")}</option>
            {providers.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Stats */}
      {loading ? (
        <div className="flex items-center gap-2 px-1 text-text-3">
          <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
          <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
            {t("common:loading")}
          </span>
        </div>
      ) : stats.length === 0 ? (
        <div className="rounded-[10px] border border-hairline-soft bg-bg-grad-a/45 px-5 py-10 text-center text-[12.5px] text-text-3">
          {t("no_data")}
        </div>
      ) : (
        <div className="space-y-2.5">
          {stats.map((s) => {
            const successRate =
              s.total_calls > 0 ? s.success_calls / s.total_calls : 0;
            return (
              <div
                key={`${s.provider}-${s.call_type}`}
                className="rounded-[10px] border border-hairline px-5 py-4"
                style={CARD_STYLE}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4">
                      {s.call_type}
                    </div>
                    <div className="mt-0.5 truncate text-[14px] font-medium text-text">
                      {s.display_name ?? s.provider}
                    </div>
                  </div>
                  <div
                    className="font-editorial shrink-0 tabular-nums"
                    style={{
                      fontSize: 18,
                      fontWeight: 400,
                      letterSpacing: "-0.01em",
                      color: "var(--color-text)",
                    }}
                  >
                    {currencyFmt.format(s.total_cost_usd)}
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 font-mono text-[11px] tabular-nums text-text-3">
                  <span>
                    <span className="text-text-4">CALLS </span>
                    {s.total_calls}
                  </span>
                  <span>
                    <span className="text-text-4">OK </span>
                    {s.success_calls}
                  </span>
                  <span>
                    <span className="text-text-4">RATE </span>
                    <span className={successRate >= 0.95 ? "text-good" : "text-warm"}>
                      {s.total_calls > 0 ? percentFmt.format(successRate) : "0%"}
                    </span>
                  </span>
                  {s.call_type === "text"
                    ? s.total_calls > 0 && (
                        <span>
                          <span className="text-text-4">TYPE </span>
                          {t("text_generation")}
                        </span>
                      )
                    : s.total_duration_seconds !== undefined && (
                        <span>
                          <span className="text-text-4">DUR </span>
                          {s.total_duration_seconds}s
                        </span>
                      )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
