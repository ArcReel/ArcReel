
import { useState, useEffect, useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { UsageStat } from "@/types";

const currencyFmt = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

export function UsageStatsSection() {
  const { t, i18n } = useTranslation("dashboard");
  const [stats, setStats] = useState<UsageStat[]>([]);
  const [loading, setLoading] = useState(true);
  const [timeRange, setTimeRange] = useState(7);
  const [providerFilter, setProviderFilter] = useState<string>("");

  const percentFmt = useMemo(
    () => new Intl.NumberFormat(i18n.language === "zh" ? "zh-CN" : "en-US", { style: "percent", maximumFractionDigits: 0 }),
    [i18n.language],
  );

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
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 依赖变化时重新获取统计数据，fetchStats 内部有 setState，属于正常异步数据获取模式
    void fetchStats();
  }, [fetchStats]);

  const providers = useMemo(
    () => Array.from(new Set(stats.map((s) => s.provider))).sort(),
    [stats],
  );

  return (
    <div className="space-y-6 p-6">
      <div>
        <h3 className="text-lg font-semibold text-gray-100">{t("usage_stats")}</h3>
        <p className="mt-1 text-sm text-gray-500">{t("usage_stats_by_provider")}</p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        {TIME_RANGES.map((r) => (
          <button
            key={r.days}
            type="button"
            onClick={() => setTimeRange(r.days)}
            className={`rounded-lg px-3 py-1.5 text-sm focus-ring ${
              timeRange === r.days
                ? "bg-indigo-600 text-white"
                : "border border-gray-700 text-gray-400 hover:text-gray-200"
            }`}
          >
            {r.label}
          </button>
        ))}
        {providers.length > 0 && (
          <select
            value={providerFilter}
            onChange={(e) => setProviderFilter(e.target.value)}
            aria-label={t("filter_by_provider")}
            className="rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-sm text-gray-300 focus:border-indigo-500/60 focus-ring"
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
        <div className="text-sm text-gray-500">{t("common:loading")}</div>
      ) : stats.length === 0 ? (
        <div className="text-sm text-gray-500">{t("no_data")}</div>
      ) : (
        <div className="space-y-3">
          {stats.map((s) => (
            <div key={`${s.provider}-${s.call_type}`} className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-sm font-medium text-gray-100">{s.display_name ?? s.provider}</span>
                  <span className="ml-2 text-xs text-gray-500">{s.call_type}</span>
                </div>
                <span className="text-sm text-gray-300">
                  {currencyFmt.format(s.total_cost_usd)}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-6 text-xs tabular-nums text-gray-400">
                <span>{t("calls")}: {s.total_calls}</span>
                <span>{t("success_count")}: {s.success_calls}</span>
                <span>
                  {t("success_rate")}:{" "}
                  {s.total_calls > 0
                    ? percentFmt.format(s.success_calls / s.total_calls)
                    : "0%"}
                </span>
                {s.call_type === "text" ? (
                  s.total_calls > 0 && <span>{t("type")}: {t("text_generation")}</span>
                ) : s.total_duration_seconds !== undefined && (
                  <span>{t("duration")}: {s.total_duration_seconds}s</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
