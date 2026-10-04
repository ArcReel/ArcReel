import { useTranslation } from "react-i18next";
import { cn } from "cn";
import type { AssetStatus } from "@/types";

export type ShotStatus = "ready" | "storyboard" | "pending";

const CONFIG: Record<ShotStatus, { className: string; labelKey: string }> = {
  ready: { className: "bg-good/12 text-good", labelKey: "shot_status_ready" },
  storyboard: { className: "bg-primary/12 text-primary", labelKey: "shot_status_storyboard" },
  pending: { className: "bg-muted text-muted-foreground", labelKey: "shot_status_pending" },
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
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
        cfg.className,
      )}
    >
      <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
      {t(cfg.labelKey)}
    </span>
  );
}
