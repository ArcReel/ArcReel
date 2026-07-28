import { useTranslation } from "react-i18next";
import type { AssetStatus } from "@/types";

export type ShotStatus = "ready" | "storyboard" | "pending";

const CONFIG: Record<ShotStatus, { color: string; bg: string; labelKey: string }> = {
  ready: {
    color: "var(--color-good)",
    bg: "oklch(0.74 0.08 155 / 0.12)",
    labelKey: "shot_status_ready",
  },
  storyboard: {
    color: "var(--color-accent-2)",
    bg: "var(--color-accent-dim)",
    labelKey: "shot_status_storyboard",
  },
  pending: {
    color: "var(--color-text-4)",
    bg: "oklch(0.30 0.01 250 / 0.4)",
    labelKey: "shot_status_pending",
  },
};

export function statusFromAssets(assetStatus: AssetStatus | undefined | null): ShotStatus {
  if (assetStatus === "completed") return "ready";
  if (assetStatus === "storyboard_ready") return "storyboard";
  return "pending";
}

export function StatusBadge({ status }: { status: ShotStatus }) {
  const { t } = useTranslation("dashboard");
  const cfg = CONFIG[status];
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[10.5px] font-medium"
      style={{
        color: cfg.color,
        background: cfg.bg,
        border: "1px solid transparent",
      }}
    >
      <span
        aria-hidden="true"
        className="h-[5px] w-[5px] rounded-full"
        style={{ background: cfg.color }}
      />
      {t(cfg.labelKey)}
    </span>
  );
}
