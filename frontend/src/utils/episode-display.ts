import type { TFunction } from "i18next";

import type { EpisodeItemRef, EpisodeMeta } from "@/types";
import { episodeItemKind, findEpisodeItem, isPositionedItemId, type EpisodeItemKind } from "@/utils/episode-item";

/** All project scripts (`projects-store` `currentScripts`). */
export type ProjectScripts = Readonly<Record<string, unknown>>;

export type EpisodeLedger = readonly Pick<EpisodeMeta, "episode" | "title">[];

// Episode ID prefixes: E3 from E3S01 / E3U02 / E3G01.
const ITEM_ID_EPISODE_PREFIX = /^E(\d+)(?=[A-Z]\d)/;

/** One-based broadcast position in episodes[]; null outside ledgers. */
export function episodePosition(episodes: EpisodeLedger, episodeId: number): number | null {
  const index = episodes.findIndex((entry) => entry.episode === episodeId);
  return index < 0 ? null : index + 1;
}

/** Creator-visible episode names: titles, otherwise broadcast positions. */
export function episodeDisplayName(episodes: EpisodeLedger, episodeId: number, t: TFunction): string {
  const title = episodes.find((entry) => entry.episode === episodeId)?.title?.trim();
  if (title) return title;
  const position = episodePosition(episodes, episodeId);
  return position === null
    ? t("common:episode_unlisted_name")
    : t("common:episode_position_name", { position });
}

/** Agent episode references include names/IDs: tools use IDs only, creator-facing retelling uses names. */
export function episodeAgentRef(episodes: EpisodeLedger, episodeId: number, t: TFunction): string {
  return t("common:episode_agent_ref", { name: episodeDisplayName(episodes, episodeId, t), id: episodeId });
}

/** Intraepisode IDs: E3S01 → S01, unchanged without prefixes. Only for nonstoryboard/nonvideo-unit entries (grids). */
export function itemIdWithinEpisode(itemId: string): string {
  return itemId.replace(ITEM_ID_EPISODE_PREFIX, "");
}

/** Entry type plus position, such as Storyboard 5 / Video unit 5; Deleted if absent from scripts. */
export function itemPositionLabel(kind: EpisodeItemKind, position: number | null, t: TFunction): string {
  return position === null ? t("common:deleted") : t(`common:item_position_${kind}`, { position });
}

/**
 * Entry references outside episode pages: Title · 5, or Title · Deleted when absent from scripts; episodes removed from ledgers use Untitled episode.
 * Non-positional entries such as grids retain Title · G01.
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
 * Agent entry references include positions/IDs together: Agent tools use IDs, while creator-facing recaps use positions.
 * Nonempty `episodes` include episode titles (outside episode pages); otherwise only Storyboard 5 (ID E3S05).
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
 * Backend-supplied entry references form Title · 5, or Title · Deleted when backends determine entries are absent from scripts.
 * Missing references (unreadable projects/episodes removed from ledgers) still hide episode IDs.
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

/** Episode diagnostic source text displays only episode-local entry IDs; location targets/Agent source text stay unchanged. */
export function itemIdsInEpisodeText(text: string): string {
  return text.replace(/(?<![A-Za-z0-9])E\d+([A-Z](?:\d+|##))/g, "$1");
}

/**
 * Server-written texts listing formal-script entries (e.g. overwrite-confirmation loss manifests): replace storyboard/video-unit IDs with
 * positional names such as Storyboard 5, or Deleted when no longer scripted; other entry IDs only lose episode prefixes. Use only when all entries belong to formal scripts;
 * draft-diagnostic IDs denote draft entries and bypass this function.
 */
export function itemIdsAsPositionsInText(text: string, scripts: ProjectScripts, t: TFunction): string {
  const positioned = text.replace(/(?<![A-Za-z0-9])E\d+[SU]\d+(?:_\d+)?(?![A-Za-z0-9])/g, (itemId) =>
    itemPositionLabel(episodeItemKind(itemId), findEpisodeItem(scripts, itemId)?.position ?? null, t),
  );
  return itemIdsInEpisodeText(positioned);
}
