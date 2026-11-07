/** Editing-view presentation helpers: default timeline selection, invalid clip markers, time formatting, unit positions/thumbnails. */

import type { TFunction } from "i18next";

import { parseIsoTimestamp } from "@/utils/date-format";
import { itemPositionLabel } from "@/utils/episode-display";
import { episodeItemKind } from "@/utils/episode-item";
import type { EditTimelineIssue, EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";

/** Default to most recently edited; equal modification times prefer later list entries, created later. */
export function pickDefaultTimeline(timelines: readonly EditTimelineSummary[]): string | null {
  let best: EditTimelineSummary | null = null;
  let bestTime = -Infinity;
  for (const timeline of timelines) {
    const time = parseIsoTimestamp(timeline.updated_at).getTime();
    const comparable = Number.isNaN(time) ? -Infinity : time;
    if (best === null || comparable >= bestTime) {
      best = timeline;
      bestTime = comparable;
    }
  }
  return best?.id ?? null;
}

export function issueClipIds(readout: EditTimelineReadout, code: EditTimelineIssue["code"]): Set<string> {
  return new Set(readout.issues.filter((issue) => issue.code === code).flatMap((issue) => issue.clip_ids));
}

export function unusedUnitIds(readout: EditTimelineReadout): string[] {
  return readout.issues
    .filter((issue) => issue.code === "unit_unused" && issue.unit_id)
    .map((issue) => issue.unit_id as string);
}

/** `mm:ss.s`, shared by player and clip positions. */
export function formatClock(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const rest = safe - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

/** Remove redundant seconds zeros: 1.25 → “1.25”, 4 → “4”. */
export function formatSeconds(seconds: number): string {
  return String(Number(seconds.toFixed(2)));
}

/** Ruler tick intervals keep the whole timeline within roughly a dozen ticks. */
export function rulerStep(duration: number): number {
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300];
  return steps.find((step) => duration / step <= 12) ?? 600;
}

type UnitAssets = { video_thumbnail?: string | null; storyboard_image?: string | null };

function itemsOf(script: unknown): { id: string; assets: UnitAssets | undefined }[] {
  if (!script || typeof script !== "object") return [];
  const shapes: [string, string][] = [
    ["video_units", "unit_id"],
    ["segments", "segment_id"],
    ["scenes", "scene_id"],
    ["shots", "shot_id"],
  ];
  for (const [field, idField] of shapes) {
    const items = (script as Record<string, unknown>)[field];
    if (!Array.isArray(items)) continue;
    return items.flatMap((item: Record<string, unknown>) =>
      typeof item?.[idField] === "string"
        ? [{ id: item[idField], assets: item.generated_assets as UnitAssets | undefined }]
        : [],
    );
  }
  return [];
}

/** Video-unit ID → one-based current episode position; units absent from scripts (deleted) have no entries. */
export function unitPositions(script: unknown): Map<string, number> {
  return new Map(itemsOf(script).map(({ id }, index) => [id, index + 1]));
}

/** Unit labels: shot 5 / video unit 5, or deleted when absent from scripts. */
export function unitPositionLabel(positions: ReadonlyMap<string, number>, unitId: string, t: TFunction): string {
  return itemPositionLabel(episodeItemKind(unitId), positions.get(unitId) ?? null, t);
}

/** Video-unit ID → project-relative thumbnail path: prefer first video frame, then storyboard. */
export function unitThumbnails(script: unknown): Map<string, string> {
  const thumbnails = new Map<string, string>();
  for (const { id, assets } of itemsOf(script)) {
    const path = assets?.video_thumbnail || assets?.storyboard_image;
    if (path) thumbnails.set(id, path);
  }
  return thumbnails;
}

export function isReferenceVideoScript(script: unknown): boolean {
  return Boolean(script && typeof script === "object" && "video_units" in script);
}

/** Reference-to-video units use `reference_videos/{id}.mp4`; others use `videos/scene_{id}.mp4`. */
export function unitVideoPath(referenceVideo: boolean, unitId: string): string {
  return referenceVideo ? `reference_videos/${unitId}.mp4` : `videos/scene_${unitId}.mp4`;
}

/** Clips sharing a video unit share hue to identify reused units. */
export function unitHue(unitId: string): number {
  let hash = 0;
  for (const char of unitId) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return (hash * 67 + 210) % 360;
}
