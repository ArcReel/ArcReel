import type { TFunction } from "i18next";

import type { EpisodeItemRef, EpisodeMeta } from "@/types";

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

/** 条目 ID 的集内部分：E3S01 → S01；没有集 ID 前缀时原样返回。 */
export function itemIdWithinEpisode(itemId: string): string {
  return itemId.replace(ITEM_ID_EPISODE_PREFIX, "");
}

/** 集页面之外指称条目：「标题 · S01」；已移出账本的集使用未命名集。 */
export function episodeItemLabel(itemId: string, episodes: EpisodeLedger, t: TFunction): string {
  const match = ITEM_ID_EPISODE_PREFIX.exec(itemId);
  const episodeId = match ? Number(match[1]) : null;
  if (episodeId === null) return itemId;
  return `${episodeDisplayName(episodes, episodeId, t)} · ${itemIdWithinEpisode(itemId)}`;
}

/** 后端附上的条目指称拼成「标题 · S01」；缺少指称时仍隐藏集 ID。 */
export function episodeItemRefLabel(
  itemId: string,
  ref: EpisodeItemRef | null | undefined,
  t: TFunction,
): string {
  if (!ref) return episodeItemLabel(itemId, [], t);
  const name = ref.episode_title.trim() || t("common:episode_position_name", { position: ref.episode_position });
  return `${name} · ${ref.item_id}`;
}

/** 集页里的诊断原文只显示集内条目 ID，定位与交给 Agent 的原文保持不变。 */
export function itemIdsInEpisodeText(text: string): string {
  return text.replace(/(?<![A-Za-z0-9])E\d+([A-Z](?:\d+|##))/g, "$1");
}
