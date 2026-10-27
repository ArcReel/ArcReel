/**
 * 分镜与视频单元在创作者面前的身份：本集数组里的当前位置（从 1 起），不是条目 ID。
 *
 * 条目 ID 只分配不复用，删除或插入后会不连续，只给排障与 Agent 调工具用。
 * 位置每次按剧本现算，插入、删除、改序之后自然跟着变。
 */

/** 分镜（segment / scene / shot）或参考生视频的视频单元。 */
export type EpisodeItemKind = "shot" | "unit";

export interface EpisodeItemFacts {
  itemId: string;
  episodeId: number;
  kind: EpisodeItemKind;
  /** 本集数组里的当前位置，从 1 起。 */
  position: number;
  /** 本集条目总数。 */
  total: number;
  /** 正文前十几个字，供列表之外指认条目。 */
  excerpt: string;
  /** 缩略图的项目内相对路径：视频缩略图优先，其次分镜图。 */
  thumbnail: string | null;
}

/** 剧本条目数组与它的 ID 字段，四种骨架各一。 */
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
 * 条目正文：旁白取原文，剧情演绎取画面描述，广告/短片取口播（没有口播取画面描述），视频单元取正文。
 * 与后端 `lib/script/item_excerpt.py` 的 `_body` 同口径。
 */
function itemBody(item: Item): string {
  if ("segment_id" in item) return str(item.novel_text);
  if ("unit_id" in item) return str(item.text).replace(/@\[([^\]]*)\]/g, "@$1");
  if ("shot_id" in item) return str(item.voiceover_text) || imagePromptScene(item);
  return str(item.scene_description) || imagePromptScene(item) || str(item.source_text);
}

/** 正文前十几个字，按码点截断，截断时以「…」收尾。 */
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

/** 分镜与视频单元的条目 ID（E3S05、E3U2、拆分出的 E3S05_1）；宫格（E3G01）等其他条目不按位置指称。 */
export function isPositionedItemId(itemId: string): boolean {
  return /^E\d+[SU]\d/.test(itemId);
}

/** 条目 ID 的种类：`E3U2` 是视频单元，`E3S05` 是分镜。 */
export function episodeItemKind(itemId: string): EpisodeItemKind {
  return /^E\d+U\d/.test(itemId) ? "unit" : "shot";
}

/**
 * 在项目剧本里找条目，给出它的当前位置、总数、正文摘录与缩略图；条目已不在剧本里时为 null。
 *
 * `scripts` 是项目的全部剧本（`projects-store` 的 `currentScripts`），四种骨架按结构识别。
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
