import { Download, Star } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MarketEntryAggregate } from "@/types";

/**
 * 官方服务的安装量与评分：安装量原样展示；有评分时显示平均分与人数，平均分为 null（人数不足）时只显示人数。
 * 数字旁的完整说明只给读屏与悬停提示，卡片上保持一行。
 */
export function MarketEntryStats({ aggregate }: { aggregate: MarketEntryAggregate }) {
  const { t, i18n } = useTranslation("dashboard");
  const installsLabel = t("market_installs_label");
  const { rating_count: count, rating_average: average } = aggregate;
  const ratingLabel =
    average === null
      ? t("market_rating_count_only", { count })
      : t("market_rating_summary", { average: average.toFixed(1), count });

  return (
    <div className="flex shrink-0 items-center gap-2.5 text-[11px] tabular-nums text-text-3">
      <span className="inline-flex items-center gap-1" title={installsLabel}>
        <Download className="h-3 w-3" aria-hidden />
        <span className="sr-only">{installsLabel}</span>
        {aggregate.installs.toLocaleString(i18n.language)}
      </span>
      {count > 0 && (
        <span className="inline-flex items-center gap-1" title={ratingLabel}>
          <Star
            className={`h-3 w-3 ${average === null ? "text-text-4" : "fill-amber-300 text-amber-300"}`}
            aria-hidden
          />
          <span className="sr-only">{ratingLabel}</span>
          <span aria-hidden>{average === null ? `(${count})` : `${average.toFixed(1)} (${count})`}</span>
        </span>
      )}
    </div>
  );
}
