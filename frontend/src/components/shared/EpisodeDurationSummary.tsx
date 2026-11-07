import { cn } from "cn";
import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";

/**
 * Episode total X seconds / target N seconds comparison in script-planning review panels.
 *
 * Targets are soft: exceeding changes hint styling and explains overage only; confirmation/subsequent generation remain available.
 * Products never hard-truncate creative dimensions (see repository `.out-of-scope/product-enforced-creative-limits.md`).
 * Render nothing without targets (`targetSeconds` null): no target means no comparison fact.
 *
 * Totals sum current draft entry durations, including unsaved edits, so changing any duration immediately updates comparisons.
 */
export interface EpisodeDurationSummaryProps {
  /** Sum of episode entry durations (seconds). */
  totalSeconds: number;
  /** Project-level episode target seconds; null means no target, rendering nothing. */
  targetSeconds: number | null;
  className?: string;
}

export function EpisodeDurationSummary({
  totalSeconds,
  targetSeconds,
  className,
}: EpisodeDurationSummaryProps) {
  const { t } = useTranslation("dashboard");
  if (targetSeconds == null) return null;

  const over = totalSeconds - targetSeconds;
  const exceeded = over > 0;

  return (
    <p
      className={cn("flex items-center gap-1.5 text-xs", exceeded ? "text-warn" : "text-muted-foreground", className)}
    >
      {exceeded && <AlertTriangle aria-hidden className="size-3.5 shrink-0 text-warn" />}
      {exceeded
        ? t("episode_duration_over_target", { total: totalSeconds, target: targetSeconds, over })
        : t("episode_duration_vs_target", { total: totalSeconds, target: targetSeconds })}
    </p>
  );
}
