
import type { TFunction } from "i18next";

import { parseIsoTimestamp } from "@/utils/date-format";
import { itemPositionLabel } from "@/utils/episode-display";
import { episodeItemKind } from "@/utils/episode-item";
import type { EditTimelineIssue, EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";

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

export function formatClock(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const rest = safe - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

export function formatSeconds(seconds: number): string {
  return String(Number(seconds.toFixed(2)));
}

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

export function unitPositions(script: unknown): Map<string, number> {
  return new Map(itemsOf(script).map(({ id }, index) => [id, index + 1]));
}

export function unitPositionLabel(positions: ReadonlyMap<string, number>, unitId: string, t: TFunction): string {
  return itemPositionLabel(episodeItemKind(unitId), positions.get(unitId) ?? null, t);
}

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

export function unitVideoPath(referenceVideo: boolean, unitId: string): string {
  return referenceVideo ? `reference_videos/${unitId}.mp4` : `videos/scene_${unitId}.mp4`;
}

export function unitHue(unitId: string): number {
  let hash = 0;
  for (const char of unitId) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return (hash * 67 + 210) % 360;
}
