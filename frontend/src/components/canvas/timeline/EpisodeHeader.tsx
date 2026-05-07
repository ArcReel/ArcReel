import { useTranslation } from "react-i18next";
import type { EpisodeMeta } from "@/types";
import type { EpisodeCost } from "@/types";
import { totalBreakdown, formatCost } from "@/utils/cost-format";

interface EpisodeHeaderProps {
  ep: EpisodeMeta;
  segmentCount: number;
  totalDuration: number;
  episodeCost?: EpisodeCost;
}

/**
 * 集卡片头部：EP·xx 徽标 + 集名（display-serif）+ 状态行 + 三列费用统计（预估/已花/剩余）。
 */
export function EpisodeHeader({
  ep,
  segmentCount,
  totalDuration,
  episodeCost,
}: EpisodeHeaderProps) {
  const { t } = useTranslation("dashboard");
  const isActive = ep.status === "in_production";
  const progress =
    typeof ep.scenes_count === "number" && ep.scenes_count > 0 && ep.videos
      ? Math.round((ep.videos.completed / ep.scenes_count) * 100)
      : 0;

  // 费用：取 estimate / actual 总和（按货币聚合）
  const estimateBreakdown = episodeCost ? totalBreakdown(episodeCost.totals.estimate) : {};
  const actualBreakdown = episodeCost ? totalBreakdown(episodeCost.totals.actual) : {};
  const remainingBreakdown: Record<string, number> = {};
  for (const [c, v] of Object.entries(estimateBreakdown)) {
    remainingBreakdown[c] = Math.max(0, v - (actualBreakdown[c] ?? 0));
  }

  return (
    <div
      className="flex flex-wrap items-end justify-between gap-5 px-6 pb-4 pt-[18px]"
      style={{
        borderBottom: "1px solid var(--color-hairline)",
        background:
          "linear-gradient(180deg, oklch(0.22 0.014 290 / 0.4), oklch(0.20 0.012 250 / 0.15))",
      }}
    >
      <div className="min-w-0 flex-1" style={{ flexBasis: "240px" }}>
        <div className="mb-2 flex flex-wrap items-center gap-2.5">
          <span
            className="num rounded px-2 py-0.5 text-[10.5px] font-semibold uppercase"
            style={{
              color: "var(--color-accent-2)",
              background: "var(--color-accent-dim)",
              letterSpacing: "0.8px",
              fontFamily: "var(--font-mono)",
            }}
          >
            {t("episode_header_episode_chip", {
              number: String(ep.episode).padStart(2, "0"),
            })}
          </span>
          <span className="num text-[11px]" style={{ color: "var(--color-text-4)" }}>
            {t("episode_header_segment_count", {
              count: segmentCount,
              duration: totalDuration,
            })}
          </span>
          {isActive && (
            <>
              <span
                aria-hidden="true"
                className="h-[3px] w-[3px] rounded"
                style={{ background: "var(--color-hairline)" }}
              />
              <span
                className="inline-flex items-center gap-1.5 text-[11px]"
                style={{ color: "var(--color-text-3)" }}
              >
                <span
                  className="h-[5px] w-[5px] animate-shot-pulse rounded-full"
                  style={{ background: "var(--color-accent)" }}
                />
                {t("episode_header_progress_inline", { percent: progress })}
              </span>
            </>
          )}
        </div>
        <h1
          className="display-serif m-0 truncate text-[26px] font-medium"
          style={{ letterSpacing: "-0.4px", lineHeight: 1.15 }}
        >
          {ep.title}
        </h1>
      </div>

      {episodeCost && (
        <div className="flex shrink-0 items-stretch gap-0">
          <CostStat
            label={t("episode_header_cost_estimated")}
            value={formatCost(estimateBreakdown)}
          />
          <CostStat
            label={t("episode_header_cost_spent")}
            value={formatCost(actualBreakdown)}
            withBorder
          />
          <CostStat
            label={t("episode_header_cost_remaining")}
            value={formatCost(remainingBreakdown)}
            accent
            withBorder
          />
        </div>
      )}
    </div>
  );
}

function CostStat({
  label,
  value,
  accent,
  withBorder,
}: {
  label: string;
  value: string;
  accent?: boolean;
  withBorder?: boolean;
}) {
  return (
    <div
      className="px-3.5 py-1.5"
      style={{
        minWidth: 88,
        borderLeft: withBorder ? "1px solid var(--color-hairline-soft)" : "none",
      }}
    >
      <div
        className="text-[10px] font-semibold uppercase"
        style={{ color: "var(--color-text-4)", letterSpacing: "0.8px" }}
      >
        {label}
      </div>
      <div
        className="num mt-0.5 text-[14px] font-semibold"
        style={{ color: accent ? "var(--color-accent-2)" : "var(--color-text)" }}
      >
        {value}
      </div>
    </div>
  );
}
