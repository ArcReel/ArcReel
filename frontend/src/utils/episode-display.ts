import type { TFunction } from "i18next";

import type { EpisodeItemRef, EpisodeMeta, ItemLocation, ItemLocationStatus } from "@/types";
import { episodeItemKind, findEpisodeItem, isPositionedItemId, type EpisodeItemKind } from "@/utils/episode-item";

export type ProjectScripts = Readonly<Record<string, unknown>>;

export type EpisodeLedger = readonly Pick<EpisodeMeta, "episode" | "title">[];

export interface EpisodeItemLocationFact {
  episode?: number | null;
  item_id?: string | null;
  position?: number | null;
  status?: ItemLocationStatus;
}

const ITEM_ID_EPISODE_PREFIX = /^E(\d+)(?=[A-Z]\d)/;

export function parseItemIdEpisode(itemId: string): number | null {
  const match = ITEM_ID_EPISODE_PREFIX.exec(itemId);
  return match ? Number(match[1]) : null;
}

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

export function formatEpisodeItemLocation(
  itemId: string,
  location: EpisodeItemLocationFact | null | undefined,
  episodes: EpisodeLedger,
  t: TFunction,
): string {
  const effectiveId = location?.item_id || itemId;
  const episodeId = location?.episode ?? parseItemIdEpisode(effectiveId);
  if (episodeId === null) {
    if (typeof location?.position === "number") {
      return itemPositionLabel(episodeItemKind(effectiveId), location.position, t);
    }
    return itemId;
  }
  const episodeName = episodeDisplayName(episodes, episodeId, t);
  if (typeof location?.position === "number") {
    return `${episodeName} · ${location.position}`;
  }
  if (!isPositionedItemId(effectiveId)) {
    return `${episodeName} · ${itemIdWithinEpisode(effectiveId)}`;
  }
  if (location?.status === "unloaded" || location?.status === "ambiguous") {
    return `${episodeName} · ${itemIdWithinEpisode(effectiveId)}`;
  }
  return `${episodeName} · ${t("common:deleted")}`;
}

export function episodeItemLabel(
  itemId: string,
  scripts: ProjectScripts,
  episodes: EpisodeLedger,
  t: TFunction,
  locations?: ReadonlyMap<string, ItemLocation>,
): string {
  const inScript = findEpisodeItem(scripts, itemId);
  if (inScript) {
    return formatEpisodeItemLocation(
      itemId,
      { episode: inScript.episodeId, item_id: inScript.itemId, position: inScript.position, status: "found" },
      episodes,
      t,
    );
  }
  const located = locations?.get(itemId);
  if (located) {
    return formatEpisodeItemLocation(itemId, located, episodes, t);
  }
  const episodeId = parseItemIdEpisode(itemId);
  if (episodeId === null) return itemId;
  const relyOnLoadedScript = locations === undefined && Object.values(scripts).some((script) =>
    typeof script === "object" && script !== null && "episode" in script && script.episode === episodeId);
  const within = relyOnLoadedScript && isPositionedItemId(itemId)
    ? t("common:deleted")
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
  const episodeId = parseItemIdEpisode(itemId);
  const named = episodes && episodeId !== null ? `${episodeDisplayName(episodes, episodeId, t)} · ${label}` : label;
  return t("common:item_agent_ref", { label: named, id: itemId });
}

export function episodeItemRefLabel(
  itemId: string,
  ref: EpisodeItemRef | null | undefined,
  t: TFunction,
  episodes?: EpisodeLedger,
): string {
  if (!ref) return episodeItemLabel(itemId, {}, episodes ?? [], t);
  const episodeId = parseItemIdEpisode(itemId);
  const ledgerName =
    episodes && episodeId !== null && episodes.some((entry) => entry.episode === episodeId)
      ? episodeDisplayName(episodes, episodeId, t)
      : null;
  const name = ledgerName ?? (ref.episode_title.trim() || t("common:episode_position_name", { position: ref.episode_position }));
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
