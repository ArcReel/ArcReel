import { isActiveStatus } from "@/stores/tasks-store";
import type { ReferenceVideoUnit, TaskItem, UnitStatus } from "@/types";

export interface UnitStatusConf {
  i18nKey: string;
  /** Badge text/background. */
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

/** Unit status from canvas statusMap; without it, infer ready/pending from final-video presence. */
export function resolveUnitStatus(
  unit: ReferenceVideoUnit,
  statusMap?: Record<string, UnitStatus>,
): UnitStatus {
  return statusMap?.[unit.unit_id] ?? (unit.generated_assets?.video_clip ? "ready" : "pending");
}

export interface UnitStatusInput {
  /** Whether this unit has a final-video asset. */
  hasClip: boolean;
  /** Latest unit task row; selectLatestTaskByResource guarantees latest-row wins. */
  queueRow: TaskItem | undefined;
  /** Occupancy from actual tasks or enqueue-layer optimistic marks. */
  busy: boolean;
  /** Unit final-video upload in progress. */
  uploading?: boolean;
  /**
   * Whether canvases offer manual final-video upload. If so, final assets may be uploaded and historical failures must not
   * override playable assets. Without uploads, final videos only derive from generation; latest failed rows mean failed attempts and
   * must remain visible rather than suppressed to ready by older videos, or failure reasons disappear.
   */
  supportsManualUpload?: boolean;
}

/**
 * Unit display status, one derivation shared by both reference-to-video canvases.
 *
 * Priority: uploading → active queue rows → failed queue rows → optimistic occupancy.
 * Display uses isActiveStatus; disabled controls separately wire occupancy `busy`, independently of returned status.
 *
 * Optimistic branches require `!queueRow`; existing rows always determine status. Retries/regeneration retain rows,
 * so optimistic-window disabling cannot use these returns alone; see `busy`.
 */
export function deriveUnitStatus({
  hasClip,
  queueRow,
  busy,
  uploading = false,
  supportsManualUpload = false,
}: UnitStatusInput): UnitStatus {
  // Uploading counts as running: batches select pending units, otherwise uploads would be enqueued again
  // and generation would write the same final file.
  if (uploading) return "running";
  if (queueRow && isActiveStatus(queueRow.status)) return "running";
  // Failed rows persist in DB without expiration; whether final assets override failures depends on upload availability.
  if (queueRow?.status === "failed" && !(supportsManualUpload && hasClip)) return "failed";
  if (!queueRow && busy) return "running";
  return hasClip ? "ready" : "pending";
}
