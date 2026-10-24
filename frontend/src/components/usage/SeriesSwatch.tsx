import { cn } from "cn";

import type { TrendSeries } from "./usage-trend";

/** 趋势图序列的色块，图例与图表提示共用；不依赖 recharts，图例不必等图表 chunk。 */
export function SeriesSwatch({ series }: { series: TrendSeries }) {
  return <span aria-hidden="true" className={cn("size-2.5 shrink-0 rounded-xs", series.swatchClass)} />;
}
