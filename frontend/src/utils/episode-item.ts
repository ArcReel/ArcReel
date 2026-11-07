/**
 * Creator-facing storyboard/video-unit identity is current one-based episode-array position, never entry IDs.
 *
 * IDs allocate without reuse, becoming nonconsecutive after insert/delete, reserved for diagnosis/Agent tools.
 * Recompute positions from scripts, naturally following insertion/deletion/reordering.
 */

/** Storyboards (segment / scene / shot) or reference-video units. */
export type EpisodeItemKind = "shot" | "unit";

export interface EpisodeItemFacts {
  itemId: string;
  episodeId: number;
  kind: EpisodeItemKind;
  /** Current position in this episode's array, starting at 1. */
  position: number;
  /** Total episode entry counts. */
  total: number;
  /** First dozen-or-so body characters for references outside lists. */
  excerpt: string;
  /** Project-relative thumbnails, preferring video thumbnails then storyboards. */
  thumbnail: string | null;
}

/** Script entry arrays/ID fields, one per four skeletons. */
const ITEM_ARRAYS = [
  ["segments", "segment_id"],
  ["scenes", "scene_id"],
  ["shots", "shot_id"],
  ["video_units", "unit_id"],
] as const;

const EXCERPT_LENGTH = 16;

type Item = Record<string, unknown>;

function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function imagePromptScene(item: Item): string {
  const prompt = item.image_prompt;
  if (typeof prompt === "string") return prompt.trim();
  if (prompt && typeof prompt === "object") return str((prompt as Item).scene);
  return "";
}

/**
 * Bodies: narration source text, drama visual descriptions, ad/short-film spoken copy or descriptions if absent, video-unit bodies.
 * Match backend `lib/script/item_excerpt.py` `_body` rules.
 */
function itemBody(item: Item): string {
  if ("segment_id" in item) return str(item.novel_text);
  if ("unit_id" in item) return str(item.text).replace(/@\[([^\]]*)\]/g, "@$1");
  if ("shot_id" in item) return str(item.voiceover_text) || imagePromptScene(item);
  return str(item.scene_description) || imagePromptScene(item) || str(item.source_text);
}

/** First dozen-or-so codepoint-truncated characters, ending … when truncated. */
export function itemExcerpt(item: Item): string {
  const chars = [...itemBody(item).replace(/\s+/g, " ")];
  return chars.length > EXCERPT_LENGTH ? `${chars.slice(0, EXCERPT_LENGTH).join("")}…` : chars.join("");
}

function itemThumbnail(item: Item): string | null {
  const assets = item.generated_assets;
  if (!assets || typeof assets !== "object") return null;
  const { video_thumbnail: video, storyboard_image: storyboard } = assets as Item;
  return str(video) || str(storyboard) || null;
}

/** Storyboard/video-unit entry IDs (E3S05,E3U2,split E3S05_1); grids (E3G01) and other entries do not use positional references. */
export function isPositionedItemId(itemId: string): boolean {
  return /^E\d+[SU]\d/.test(itemId);
}

/** Entry ID kinds: `E3U2` is video units; `E3S05` is storyboards. */
export function episodeItemKind(itemId: string): EpisodeItemKind {
  return /^E\d+U\d/.test(itemId) ? "unit" : "shot";
}

/**
 * Find project-script entries, providing current positions, totals, body excerpts/thumbnails; null if no longer scripted.
 *
 * `scripts` contains all project scripts (`projects-store` `currentScripts`); recognize four skeletons structurally.
 */
export function findEpisodeItem(scripts: Readonly<Record<string, unknown>>, itemId: string): EpisodeItemFacts | null {
  for (const script of Object.values(scripts)) {
    if (!script || typeof script !== "object") continue;
    for (const [arrayKey, idKey] of ITEM_ARRAYS) {
      const items = (script as Item)[arrayKey];
      if (!Array.isArray(items)) continue;
      const index = items.findIndex((item) => (item as Item | null)?.[idKey] === itemId);
      if (index < 0) continue;
      const item = items[index] as Item;
      return {
        itemId,
        episodeId: Number((script as Item).episode),
        kind: arrayKey === "video_units" ? "unit" : "shot",
        position: index + 1,
        total: items.length,
        excerpt: itemExcerpt(item),
        thumbnail: itemThumbnail(item),
      };
    }
  }
  return null;
}
