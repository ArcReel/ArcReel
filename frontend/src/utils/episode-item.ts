
export type EpisodeItemKind = "shot" | "unit";

export interface EpisodeItemFacts {
  itemId: string;
  episodeId: number;
  kind: EpisodeItemKind;
  position: number;
  total: number;
  excerpt: string;
  thumbnail: string | null;
}

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

function itemBody(item: Item): string {
  if ("segment_id" in item) return str(item.novel_text);
  if ("unit_id" in item) return str(item.text).replace(/@\[([^\]]*)\]/g, "@$1");
  if ("shot_id" in item) return str(item.voiceover_text) || imagePromptScene(item);
  return str(item.scene_description) || imagePromptScene(item) || str(item.source_text);
}

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

export function isPositionedItemId(itemId: string): boolean {
  return /^E\d+[SU]\d/.test(itemId);
}

export function episodeItemKind(itemId: string): EpisodeItemKind {
  return /^E\d+U\d/.test(itemId) ? "unit" : "shot";
}

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
