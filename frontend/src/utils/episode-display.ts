import type { TFunction } from "i18next";

import type { EpisodeItemRef, EpisodeMeta } from "@/types";
import { episodeItemKind, findEpisodeItem, isPositionedItemId, type EpisodeItemKind } from "@/utils/episode-item";

/** 项目的全部剧本（`projects-store` 的 `currentScripts`）。 */
export type ProjectScripts = Readonly<Record<string, unknown>>;

export type EpisodeLedger = readonly Pick<EpisodeMeta, "episode" | "title">[];

// 条目 ID 的集 ID 前缀：E3S01 / E3U02 / E3G01 里的 E3。
const ITEM_ID_EPISODE_PREFIX = /^E(\d+)(?=[A-Z]\d)/;

/** 一集在播出顺序（episodes[] 排列）中的位置，从 1 起；不在账本里时为 null。 */
export function episodePosition(episodes: EpisodeLedger, episodeId: number): number | null {
  const index = episodes.findIndex((entry) => entry.episode === episodeId);
  return index < 0 ? null : index + 1;
}

/** 创作者看到的集名：标题，没有标题时是播出位置。 */
export function episodeDisplayName(episodes: EpisodeLedger, episodeId: number, t: TFunction): string {
  const title = episodes.find((entry) => entry.episode === episodeId)?.title?.trim();
  if (title) return title;
  const position = episodePosition(episodes, episodeId);
  return position === null
    ? t("common:episode_unlisted_name")
    : t("common:episode_position_name", { position });
}

/** 发给 Agent 的集指称：集名与集 ID 一起给，Agent 调工具只用集 ID，对创作者复述用集名。 */
export function episodeAgentRef(episodes: EpisodeLedger, episodeId: number, t: TFunction): string {
  return t("common:episode_agent_ref", { name: episodeDisplayName(episodes, episodeId, t), id: episodeId });
}

/** 条目 ID 的集内部分：E3S01 → S01；没有集 ID 前缀时原样返回。只用于不是分镜或视频单元的条目（宫格）。 */
export function itemIdWithinEpisode(itemId: string): string {
  return itemId.replace(ITEM_ID_EPISODE_PREFIX, "");
}

/** 条目的类型名加位置：「分镜 5」「视频单元 5」；条目已不在剧本里时为「已删除」。 */
export function itemPositionLabel(kind: EpisodeItemKind, position: number | null, t: TFunction): string {
  return position === null ? t("common:deleted") : t(`common:item_position_${kind}`, { position });
}

/**
 * 集页面之外指称条目：「标题 · 5」，条目已不在剧本里时「标题 · 已删除」；已移出账本的集使用未命名集。
 * 宫格这类不按位置指称的条目保持「标题 · G01」。
 */
export function episodeItemLabel(
  itemId: string,
  scripts: ProjectScripts,
  episodes: EpisodeLedger,
  t: TFunction,
): string {
  const match = ITEM_ID_EPISODE_PREFIX.exec(itemId);
  const episodeId = match ? Number(match[1]) : null;
  if (episodeId === null) return itemId;
  const within = isPositionedItemId(itemId)
    ? String(findEpisodeItem(scripts, itemId)?.position ?? t("common:deleted"))
    : itemIdWithinEpisode(itemId);
  return `${episodeDisplayName(episodes, episodeId, t)} · ${within}`;
}

/**
 * 发给 Agent 的条目指称：位置与条目 ID 一起给，Agent 调工具用 ID，对创作者复述用位置。
 * `episodes` 非空时带上集名（集页面之外），否则只写「分镜 5（ID E3S05）」。
 */
export function episodeItemAgentRef(
  itemId: string,
  scripts: ProjectScripts,
  t: TFunction,
  episodes?: EpisodeLedger,
): string {
  const item = findEpisodeItem(scripts, itemId);
  const label = itemPositionLabel(episodeItemKind(itemId), item?.position ?? null, t);
  const match = ITEM_ID_EPISODE_PREFIX.exec(itemId);
  const named = episodes && match ? `${episodeDisplayName(episodes, Number(match[1]), t)} · ${label}` : label;
  return t("common:item_agent_ref", { label: named, id: itemId });
}

/**
 * 后端附上的条目指称拼成「标题 · 5」，后端判定条目已不在剧本里时「标题 · 已删除」。
 * 缺少指称时（项目读不到、集已移出账本）仍隐藏集 ID。
 */
export function episodeItemRefLabel(
  itemId: string,
  ref: EpisodeItemRef | null | undefined,
  t: TFunction,
): string {
  if (!ref) return episodeItemLabel(itemId, {}, [], t);
  const name = ref.episode_title.trim() || t("common:episode_position_name", { position: ref.episode_position });
  if (!isPositionedItemId(itemId)) return `${name} · ${ref.item_id}`;
  return `${name} · ${ref.item_position ?? t("common:deleted")}`;
}

/** 集页里的诊断原文只显示集内条目 ID，定位与交给 Agent 的原文保持不变。 */
export function itemIdsInEpisodeText(text: string): string {
  return text.replace(/(?<![A-Za-z0-9])E\d+([A-Z](?:\d+|##))/g, "$1");
}

/**
 * 服务端成文、列出正式剧本条目的文本（如覆盖确认的丢失清单）：分镜与视频单元的 ID 换成「分镜 5」这类
 * 位置称呼，已不在剧本里的写「已删除」；其余条目 ID 只去掉集前缀。只用于条目都在正式剧本里的文本，
 * 草稿诊断里的 ID 指的是草稿条目，不经这里。
 */
export function itemIdsAsPositionsInText(text: string, scripts: ProjectScripts, t: TFunction): string {
  const positioned = text.replace(/(?<![A-Za-z0-9])E\d+[SU]\d+(?:_\d+)?(?![A-Za-z0-9])/g, (itemId) =>
    itemPositionLabel(episodeItemKind(itemId), findEpisodeItem(scripts, itemId)?.position ?? null, t),
  );
  return itemIdsInEpisodeText(positioned);
}
