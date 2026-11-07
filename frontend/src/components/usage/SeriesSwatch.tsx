import { cn } from "cn";

import type { TrendSeries } from "./usage-trend";

/** Trend-series color swatch shared by legends and chart tooltips; independent of recharts so legends need not wait for the chart chunk. */
export function SeriesSwatch({ series }: { series: TrendSeries }) {
  return <span aria-hidden="true" className={cn("size-2.5 shrink-0 rounded-xs", series.swatchClass)} />;
}
