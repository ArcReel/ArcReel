import type { TFunction } from "i18next";

import { WORKSPACE_ROUTE_EPISODES } from "@/app-routes";
import type { EpisodeMeta, EpisodesView, EpisodesViewEpisode, EpisodesViewFile } from "@/types";

/** Episodes-view queries: `upload` opens/prefills uploads, `episode` selects an episode, `create` opens creation. */
export const EPISODES_VIEW_UPLOAD_PARAM = "upload";
export const EPISODES_VIEW_EPISODE_PARAM = "episode";
export const EPISODES_VIEW_CREATE_PARAM = "create";

export type SourceUploadMode = "whole_source" | "episode";

/** Episodes-view URL as a workspace-relative route. */
export function episodesViewPath(
  options: { upload?: SourceUploadMode; episode?: number; create?: boolean } = {},
): string {
  const params = new URLSearchParams();
  if (options.upload) params.set(EPISODES_VIEW_UPLOAD_PARAM, options.upload);
  if (options.create) params.set(EPISODES_VIEW_CREATE_PARAM, "1");
  if (options.episode !== undefined) params.set(EPISODES_VIEW_EPISODE_PARAM, String(options.episode));
  const query = params.toString();
  return `/${WORKSPACE_ROUTE_EPISODES}${query ? `?${query}` : ""}`;
}

/** Episode identity hue by ID, stable across insertion/reordering. Set `--episode-hue` so descendants use `bg-episode`, `text-episode`, `border-episode` for its identity color. */
export function episodeHue(episodeId: number): number {
  return (episodeId * 67) % 360;
}

/** Volume: 1,234 characters or 1,234 words. */
export function formatVolume(t: TFunction, units: number, unit: EpisodesView["unit"]): string {
  return t(unit === "words" ? "dashboard:episodes_view_words" : "dashboard:episodes_view_chars", {
    count: units,
    formatted: units.toLocaleString(),
  });
}

/** Approximate reading duration: under 1 minute or about N minutes. */
export function formatSpoken(t: TFunction, seconds: number): string {
  const minutes = Math.round(seconds / 60);
  return minutes < 1
    ? t("dashboard:episodes_view_spoken_under_minute")
    : t("dashboard:episodes_view_spoken_minutes", { count: minutes });
}

export type OutlineRow =
  | { kind: "episode"; episode: EpisodeMeta; info: EpisodesViewEpisode }
  | { kind: "gap"; units: number; key: string; sourceFile: string; end: number };

export interface OutlineFileGroup {
  file: EpisodesViewFile;
  index: number;
  rows: OutlineRow[];
  /** Unplanned text volume after this file's last cut episode. */
  tailUnits: number;
}

/** From-whole-source directory: file groups list cut episodes/intervening unsplit text by source position. */
export function outlineFileGroups(view: EpisodesView, episodes: EpisodeMeta[]): OutlineFileGroup[] {
  const meta = new Map(episodes.map((episode) => [episode.episode, episode]));
  const info = new Map(view.episodes.map((episode) => [episode.episode, episode]));
  return view.files.flatMap((file, index) => {
    const rows: OutlineRow[] = [];
    let tailUnits = 0;
    for (const segment of file.segments) {
      if (segment.kind === "episode" && segment.episode !== null) {
        // Cross-file episodes appear only in their start-file group.
        if (segment.continued) continue;
        const episode = meta.get(segment.episode);
        const episodeInfo = info.get(segment.episode);
        if (episode && episodeInfo) rows.push({ kind: "episode", episode, info: episodeInfo });
      } else if (segment.gap) {
        rows.push({
          kind: "gap",
          units: segment.units,
          key: `${file.source_file}:${segment.start}`,
          sourceFile: file.source_file,
          end: segment.end,
        });
      } else {
        tailUnits += segment.units;
      }
    }
    return rows.some((row) => row.kind === "episode") ? [{ file, index, rows, tailUnits }] : [];
  });
}

/** Other episodes: own-source, sourceless, or cut episodes lacking position records, outside whole-source text, in airing order. */
export function otherEpisodes(
  view: EpisodesView,
  episodes: EpisodeMeta[],
): { episode: EpisodeMeta; info: EpisodesViewEpisode | null }[] {
  const info = new Map(view.episodes.map((episode) => [episode.episode, episode]));
  return episodes
    .filter((episode) => !info.get(episode.episode)?.placed)
    .map((episode) => ({ episode, info: info.get(episode.episode) ?? null }));
}

/** episode_N filename stems are reserved for episode sources, not whole-source uploads, matching backend `is_derived_episode_name`. */
export function isReservedEpisodeFileName(filename: string): boolean {
  const stem = filename.replace(/\.[^.]*$/, "");
  return /^episode_[0-9]+$/.test(stem);
}
