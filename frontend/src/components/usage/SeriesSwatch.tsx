import { cn } from "cn";

import type { TrendSeries } from "./usage-trend";

export function SeriesSwatch({ series }: { series: TrendSeries }) {
  return <span aria-hidden="true" className={cn("size-2.5 shrink-0 rounded-xs", series.swatchClass)} />;
}
