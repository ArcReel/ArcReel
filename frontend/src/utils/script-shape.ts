/**
 * Script-shape dispatch, matching backend SCRIPT_SHAPES: content_mode → entry-ID / character-reference fields.
 *
 * Timeline lists/split views/details/reference sections share this, avoiding drift among duplicate ternaries.
 */

import type {
  AdEpisodeScript,
  AdShot,
  DramaEpisodeScript,
  DramaScene,
  EpisodeScript,
  NarrationEpisodeScript,
  NarrationSegment,
} from "@/types";

export type EditorContentMode = "narration" | "drama" | "ad";
export type ScriptItem = NarrationSegment | DramaScene | AdShot;

export type CharactersField =
  | "characters_in_segment"
  | "characters_in_scene"
  | "characters_in_shot";

/** Extract entry IDs (segment_id / scene_id / shot_id). */
export function getScriptItemId(item: ScriptItem, mode: EditorContentMode): string {
  if (mode === "narration") return (item as NarrationSegment).segment_id;
  if (mode === "ad") return (item as AdShot).shot_id;
  return (item as DramaScene).scene_id;
}

/**
 * Episode entry arrays for modes: segments / shots / scenes.
 *
 * Unregistered modes return empty arrays; rendering alternate shapes would dispatch saves to wrong endpoints.
 */
export function getScriptItems(
  script: EpisodeScript | null | undefined,
  mode: EditorContentMode,
): ScriptItem[] {
  if (!script) return [];
  if (mode === "narration") return (script as NarrationEpisodeScript).segments ?? [];
  if (mode === "ad") return (script as AdEpisodeScript).shots ?? [];
  if (mode === "drama") return (script as DramaEpisodeScript).scenes ?? [];
  return [];
}

/** Character-reference field names for mode entries. */
export function charactersFieldFor(mode: EditorContentMode): CharactersField {
  if (mode === "drama") return "characters_in_scene";
  if (mode === "ad") return "characters_in_shot";
  return "characters_in_segment";
}

/**
 * Episode total duration = sum of entry `duration_seconds`.
 *
 * Derived values never persist in scripts (servers compute on read from summaries with identical rules). All canvases share
 * this implementation, avoiding duplicate timeline/grid calculations with divergent robustness.
 */
export function sumItemDuration(
  items: readonly { duration_seconds?: number | null }[],
): number {
  return items.reduce((sum, item) => {
    const d = item.duration_seconds;
    return sum + (typeof d === "number" && Number.isFinite(d) ? d : 0);
  }, 0);
}
