import { isActiveStatus } from "@/stores/tasks-store";
import type { ReferenceVideoUnit, TaskItem, UnitStatus } from "@/types";

export interface UnitStatusConf {
  i18nKey: string;
  badgeClass: string;
  dotClass: string;
}

export const STATUS_CONF: Record<UnitStatus, UnitStatusConf> = {
  pending: {
    i18nKey: "reference_status_pending",
    badgeClass: "bg-muted text-muted-foreground",
    dotClass: "bg-muted-foreground",
  },
  running: {
    i18nKey: "reference_status_running",
    badgeClass: "bg-warn/15 text-warn",
    dotClass: "animate-breath bg-warn",
  },
  ready: {
    i18nKey: "reference_status_ready",
    badgeClass: "bg-good/15 text-good",
    dotClass: "bg-good",
  },
  failed: {
    i18nKey: "reference_status_failed",
    badgeClass: "bg-destructive/10 text-destructive",
    dotClass: "bg-destructive",
  },
};

export function resolveUnitStatus(
  unit: ReferenceVideoUnit,
  statusMap?: Record<string, UnitStatus>,
): UnitStatus {
  return statusMap?.[unit.unit_id] ?? (unit.generated_assets?.video_clip ? "ready" : "pending");
}

export interface UnitStatusInput {
  hasClip: boolean;
  queueRow: TaskItem | undefined;
  busy: boolean;
  uploading?: boolean;
  supportsManualUpload?: boolean;
}

export function deriveUnitStatus({
  hasClip,
  queueRow,
  busy,
  uploading = false,
  supportsManualUpload = false,
}: UnitStatusInput): UnitStatus {
  if (uploading) return "running";
  if (queueRow && isActiveStatus(queueRow.status)) return "running";
  if (queueRow?.status === "failed" && !(supportsManualUpload && hasClip)) return "failed";
  if (!queueRow && busy) return "running";
  return hasClip ? "ready" : "pending";
}
