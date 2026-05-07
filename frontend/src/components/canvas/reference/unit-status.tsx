import { useTranslation } from "react-i18next";
import type { ReferenceVideoUnit, UnitStatus } from "@/types";

export interface UnitStatusConf {
  i18nKey: string;
  textClass: string;
  bgClass: string;
  dotClass: string;
  pulse: boolean;
}

export const STATUS_CONF: Record<UnitStatus, UnitStatusConf> = {
  pending: {
    i18nKey: "reference_status_pending",
    textClass: "text-[var(--color-text-4)]",
    bgClass: "bg-[oklch(0.30_0.01_250_/_0.4)]",
    dotClass: "bg-[var(--color-text-4)]",
    pulse: false,
  },
  running: {
    i18nKey: "reference_status_running",
    textClass: "text-amber-300",
    bgClass: "bg-amber-500/15",
    dotClass: "bg-amber-400",
    pulse: true,
  },
  ready: {
    i18nKey: "reference_status_ready",
    textClass: "text-emerald-300",
    bgClass: "bg-emerald-500/15",
    dotClass: "bg-emerald-400",
    pulse: false,
  },
  failed: {
    i18nKey: "reference_status_failed",
    textClass: "text-red-300",
    bgClass: "bg-red-500/15",
    dotClass: "bg-red-400",
    pulse: false,
  },
};

export function deriveUnitStatus(
  unit: ReferenceVideoUnit,
  statusMap?: Record<string, UnitStatus>,
): UnitStatus {
  return statusMap?.[unit.unit_id] ?? (unit.generated_assets.video_clip ? "ready" : "pending");
}

export interface StatusBadgeProps {
  status: UnitStatus;
  /** sm = 4px dot + 10px label, md = 5px dot + 10.5px label. */
  size?: "sm" | "md";
}

export function StatusBadge({ status, size = "sm" }: StatusBadgeProps) {
  const { t } = useTranslation("dashboard");
  const conf = STATUS_CONF[status];
  const dotSize = size === "sm" ? "h-1 w-1" : "h-[5px] w-[5px]";
  const fontSize = size === "sm" ? "text-[10px]" : "text-[10.5px]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 font-medium ${fontSize} ${conf.textClass} ${conf.bgClass}`}
    >
      <span
        aria-hidden="true"
        className={`${dotSize} rounded-full ${conf.dotClass} ${conf.pulse ? "motion-safe:animate-pulse" : ""}`}
      />
      {t(conf.i18nKey)}
    </span>
  );
}
