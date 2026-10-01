/** 剪辑视图的展示辅助：默认选中哪条剪辑时间线、片段的失效标记、时间格式与视频单元的缩略图。 */

import { parseIsoTimestamp } from "@/utils/date-format";
import type { EditTimelineIssue, EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";

/** 默认选最近剪辑过的那条；修改时间相同时取列表里靠后（创建较晚）的一条。 */
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

/** `mm:ss.s`，播放器与片段位置共用。 */
export function formatClock(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const rest = safe - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

/** 秒数去掉多余的零：1.25 → 「1.25」，4 → 「4」。 */
export function formatSeconds(seconds: number): string {
  return String(Number(seconds.toFixed(2)));
}

/** 标尺刻度间隔：让整条时间线落在十来个刻度以内。 */
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

/** 视频单元 ID → 缩略图的项目内路径：优先视频首帧，其次分镜图。 */
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

/** 参考生视频的单元放在 `reference_videos/{id}.mp4`，其余视频单元放在 `videos/scene_{id}.mp4`。 */
export function unitVideoPath(referenceVideo: boolean, unitId: string): string {
  return referenceVideo ? `reference_videos/${unitId}.mp4` : `videos/scene_${unitId}.mp4`;
}

/** 同一个视频单元的片段用同一种色相，方便认出重复使用的单元。 */
export function unitHue(unitId: string): number {
  let hash = 0;
  for (const char of unitId) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return (hash * 67 + 210) % 360;
}
