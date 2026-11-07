import { Download, Star } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import type { MarketEntryAggregate } from "@/types";

/**
 * Installation counts and ratings from the official service: show installation counts as received; when ratings exist, show the average and count, or only the count when the average is null (too few ratings).
 * The full explanation beside each number is screen-reader-only, keeping the card to one line.
 */
export function MarketEntryStats({ aggregate }: { aggregate: MarketEntryAggregate }) {
  const { t, i18n } = useTranslation("dashboard");
  const { rating_count: count, rating_average: average } = aggregate;
  const ratingLabel =
    average === null
      ? t("market_rating_count_only", { count })
      : t("market_rating_summary", { average: average.toFixed(1), count });

  return (
    <div className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground tabular-nums">
      <span className="inline-flex items-center gap-1">
        <Download className="size-3" aria-hidden />
        <span className="sr-only">{t("market_installs_label")}</span>
        {aggregate.installs.toLocaleString(i18n.language)}
      </span>
      {count > 0 && (
        <span className="inline-flex items-center gap-1">
          <Star className={cn("size-3", average !== null && "fill-primary text-primary")} aria-hidden />
          <span className="sr-only">{ratingLabel}</span>
          <span aria-hidden>{average === null ? `(${count})` : `${average.toFixed(1)} (${count})`}</span>
        </span>
      )}
    </div>
  );
}
