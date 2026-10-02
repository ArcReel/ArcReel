import type { TFunction } from "i18next";

import { WORKSPACE_ROUTE_EPISODES } from "@/app-routes";
import type { EpisodeMeta, EpisodesView, EpisodesViewEpisode, EpisodesViewFile } from "@/types";

/** 「分集」视图的查询参数：`upload` 打开上传对话框并预选方式，`episode` 选中一集，`create` 打开新建一集对话框。 */
export const EPISODES_VIEW_UPLOAD_PARAM = "upload";
export const EPISODES_VIEW_EPISODE_PARAM = "episode";
export const EPISODES_VIEW_CREATE_PARAM = "create";

export type SourceUploadMode = "whole_source" | "episode";

/** 「分集」视图的地址（工作区内的相对路由）。 */
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

/** 每集的身份色相：按集 ID 取，调序、插入都不换色。 */
export function episodeHue(episodeId: number): number {
  return (episodeId * 67) % 360;
}

export function episodeColor(episodeId: number, alpha = 1): string {
  return `oklch(0.72 0.1 ${episodeHue(episodeId)} / ${alpha})`;
}

/** 体量：「1,234 字」或「1,234 词」。 */
export function formatVolume(t: TFunction, units: number, unit: EpisodesView["unit"]): string {
  return t(unit === "words" ? "dashboard:episodes_view_words" : "dashboard:episodes_view_chars", {
    count: units,
    formatted: units.toLocaleString(),
  });
}

/** 约略朗读时长：不足 1 分钟、约 N 分钟。 */
export function formatSpoken(t: TFunction, seconds: number): string {
  const minutes = Math.round(seconds / 60);
  return minutes < 1
    ? t("dashboard:episodes_view_spoken_under_minute")
    : t("dashboard:episodes_view_spoken_minutes", { count: minutes });
}

export type RailRow =
  | { kind: "episode"; episode: EpisodeMeta; info: EpisodesViewEpisode }
  | { kind: "gap"; units: number; key: string; sourceFile: string; end: number };

export interface RailFileGroup {
  file: EpisodesViewFile;
  index: number;
  rows: RailRow[];
  /** 本文件在最后一个切出集之后尚未分集的原文体量。 */
  tailUnits: number;
}

/** 右栏「来自整本源文」：按文件分组，组内按源文位置列出切出集与夹在其间的未切分原文。 */
export function railFileGroups(view: EpisodesView, episodes: EpisodeMeta[]): RailFileGroup[] {
  const meta = new Map(episodes.map((episode) => [episode.episode, episode]));
  const info = new Map(view.episodes.map((episode) => [episode.episode, episode]));
  return view.files.flatMap((file, index) => {
    const rows: RailRow[] = [];
    let tailUnits = 0;
    for (const segment of file.segments) {
      if (segment.kind === "episode" && segment.episode !== null) {
        // 跨文件的集只列在起点所在文件的分组里
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

/** 右栏「其他集」：原文不在整本源文里的集（自带原文、无原文、原文位置没有记录的切出集），按播出顺序。 */
export function otherEpisodes(
  view: EpisodesView,
  episodes: EpisodeMeta[],
): { episode: EpisodeMeta; info: EpisodesViewEpisode | null }[] {
  const info = new Map(view.episodes.map((episode) => [episode.episode, episode]));
  return episodes
    .filter((episode) => !info.get(episode.episode)?.placed)
    .map((episode) => ({ episode, info: info.get(episode.episode) ?? null }));
}

/** 形如 episode_N 的文件名主干留给集原文文件，不能作为整本源文上传（与后端 `is_derived_episode_name` 同一口径）。 */
export function isReservedEpisodeFileName(filename: string): boolean {
  const stem = filename.replace(/\.[^.]*$/, "");
  return /^episode_[0-9]+$/.test(stem);
}
