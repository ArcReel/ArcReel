/**
 * 「分集」视图的数据：后端 `GET /projects/{name}/episodes-view`，与 `lib/episode/episode_layout.py` 的
 * dataclass 一一对应。段的 `start` / `end` 是规范化源文里的 Unicode 码位偏移，不是 JS 字符串下标。
 */

export type EpisodeSourceOrigin = "whole_source" | "own" | "none";

export interface EpisodesViewSegment {
  kind: "episode" | "unsplit";
  start: number;
  end: number;
  text: string;
  /** kind 为 episode 时是集 ID。 */
  episode: number | null;
  /** 未切分段排在按源文位置最后一个切出集之前（夹在切出集之间或在第一个切出集之前）。 */
  gap: boolean;
  units: number;
}

export interface EpisodesViewFile {
  source_file: string;
  name: string;
  /** 上传时的原始文件名；没有留原件备份时为 null。 */
  original_filename: string | null;
  /** 文件读不到（不存在、符号链接、非 UTF-8）。 */
  missing: boolean;
  units: number;
  cut_units: number;
  segments: EpisodesViewSegment[];
}

export interface EpisodesViewEpisode {
  episode: number;
  origin: EpisodeSourceOrigin;
  /** 原文段出现在整本源文里。 */
  placed: boolean;
  source_file: string | null;
  /** 读不到原文时为 null。 */
  units: number | null;
  spoken_seconds: number | null;
  first_sentence: string;
  last_sentence: string;
}

export interface UnregisteredSourceFile {
  name: string;
  size: number;
  /** 文件名能直接加入整本源文（非下划线前缀，也不是 episode_N.txt）。 */
  can_join_whole_source: boolean;
}

export interface EpisodesView {
  /** 体量的计量单位：按字（中文等）或按词（英文、越南文）。 */
  unit: "chars" | "words";
  units: number;
  cut_units: number;
  files: EpisodesViewFile[];
  episodes: EpisodesViewEpisode[];
  unregistered: UnregisteredSourceFile[];
}

export type AdoptSourceFileTarget =
  | { target: "whole_source" }
  | { target: "episode"; episode?: number | null };
