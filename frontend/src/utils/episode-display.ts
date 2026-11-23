import type { TFunction } from "i18next";

import type { EpisodeItemRef, EpisodeMeta } from "@/types";
import { episodeItemKind, findEpisodeItem, isPositionedItemId, type EpisodeItemKind } from "@/utils/episode-item";

export type ProjectScripts = Readonly<Record<string, unknown>>;

export type EpisodeLedger = readonly Pick<EpisodeMeta, "episode" | "title">[];

const ITEM_ID_EPISODE_PREFIX = /^E(\d+)(?=[A-Z]\d)/;

export function episodePosition(episodes: EpisodeLedger, episodeId: number): number | null {
  const index = episodes.findIndex((entry) => entry.episode === episodeId);
  return index < 0 ? null : index + 1;
}

export function episodeDisplayName(episodes: EpisodeLedger, episodeId: number, t: TFunction): string {
  const title = episodes.find((entry) => entry.episode === episodeId)?.title?.trim();
  if (title) return title;
  const position = episodePosition(episodes, episodeId);
  return position === null
    ? t("common:episode_unlisted_name")
    : t("common:episode_position_name", { position });
}

export function episodeAgentRef(episodes: EpisodeLedger, episodeId: number, t: TFunction): string {
  return t("common:episode_agent_ref", { name: episodeDisplayName(episodes, episodeId, t), id: episodeId });
}

export function itemIdWithinEpisode(itemId: string): string {
  return itemId.replace(ITEM_ID_EPISODE_PREFIX, "");
}

export function itemPositionLabel(kind: EpisodeItemKind, position: number | null, t: TFunction): string {
  return position === null ? t("common:deleted") : t(`common:item_position_${kind}`, { position });
}

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

export function itemIdsInEpisodeText(text: string): string {
  return text.replace(/(?<![A-Za-z0-9])E\d+([A-Z](?:\d+|##))/g, "$1");
}

export function itemIdsAsPositionsInText(text: string, scripts: ProjectScripts, t: TFunction): string {
  const positioned = text.replace(/(?<![A-Za-z0-9])E\d+[SU]\d+(?:_\d+)?(?![A-Za-z0-9])/g, (itemId) =>
    itemPositionLabel(episodeItemKind(itemId), findEpisodeItem(scripts, itemId)?.position ?? null, t),
  );
  return itemIdsInEpisodeText(positioned);
}
