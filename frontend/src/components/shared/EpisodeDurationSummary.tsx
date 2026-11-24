import { cn } from "cn";
import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";

export interface EpisodeDurationSummaryProps {
  totalSeconds: number;
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
