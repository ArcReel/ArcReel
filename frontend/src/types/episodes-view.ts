/**
 * 「分集」视图的数据：后端 `GET /projects/{name}/episodes-view`，与 `lib/episode/episode_layout.py` 的
 * dataclass 一一对应。段的 `start` / `end` 是规范化源文里的 Unicode 码位偏移，不是 JS 字符串下标。
 */

export type EpisodeSourceOrigin = "whole_source" | "own" | "none";

/** 源文件类型：只有剧情演绎项目有。 */
export type SourceKind = "novel" | "screenplay";

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
  /** 规范化全文的码位数，是文件内偏移的上界；读不到时为 0。 */
  length: number;
  units: number;
  cut_units: number;
  segments: EpisodesViewSegment[];
  /** 非剧情演绎项目为 null。 */
  source_kind: SourceKind | null;
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
  /** 自带原文的集取自身记录，切出集取原文范围起点所在文件；无原文的集与非剧情演绎项目为 null。 */
  source_kind: SourceKind | null;
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

/** 手工切分的动作：切分、拆分、移动分界、与下一集合并、清除之后的切分。偏移是文件内的码位偏移。 */
export type ManualSplitAction =
  | { action: "cut"; source_file: string; end: number; title?: string }
  | { action: "split"; episode: number; at: number }
  | { action: "move_boundary"; episode: number; at: number }
  | { action: "merge_next"; episode: number }
  | { action: "clear_after"; episode: number };

/** 一次手工切分波及的集（集 ID）。 */
export interface ManualSplitImpact {
  /** 原文范围变了且有产物，标 stale。 */
  restaled: number[];
  /** 有产物，转为无原文的集并移到播出顺序末尾。 */
  retired: number[];
  /** 没有产物，直接移除。 */
  removed: number[];
}

export type ManualSplitResponse =
  | { status: "applied"; episode: number | null; impact: ManualSplitImpact }
  /** `impact.text` 是服务端成文的确认清单。 */
  | { status: "confirmation_required"; impact: ManualSplitImpact & { text: string } };

/** AI 规划分集的提交结果：首窗的生成批次。之后每一窗由执行中的上一窗排进队列。 */
export interface EpisodePlanningResponse {
  batch: { batch_id: string; members: { unit_id: string; task_id: string | null; deduped?: boolean }[] };
}

/** 停止分集规划：`cancelled` 是取消掉的排队窗口，`running` 是正在执行、会照常完成的窗口。 */
export interface StopEpisodePlanningResponse {
  cancelled: string[];
  running: string[];
}

/** 改整本源文文件类型的结果：`needs_confirmation` 时没有写入，`affected_episodes` 是会让脚本规划判 stale 的集。 */
export interface SourceKindChangeResult {
  success: boolean;
  applied: boolean;
  needs_confirmation: boolean;
  affected_episodes: number[];
}

/** 集页写本集原文的结果；`needs_confirmation` 时原文与类型都没有写入。 */
export interface EpisodeSourceWriteResult extends SourceKindChangeResult {
  episode: number;
  source_origin: EpisodeSourceOrigin;
}
