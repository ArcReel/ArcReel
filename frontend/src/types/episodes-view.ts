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
  /** 集段接着上一个文件里的同一集（这一集跨文件，起点在前面的文件里）。 */
  continued: boolean;
  /** 集段在下一个文件里接着（这一集跨文件，终点在后面的文件里）。 */
  continues: boolean;
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
  /** 文件在 ArcReel 之外被改动过、还没有更新分集账本：替换、编辑、调序、改类型与切分都暂停，删除仍可用。 */
  changed_outside: boolean;
}

/**
 * 一个在 ArcReel 之外被改动过的文件：按快照对齐算出的受影响集清单。`revision` 原样带给 `acceptExternalSourceChange`
 * 即确认这份清单。算不出清单时 `impact` 与 `revision` 为 null，`problem` 是服务端成文的原因。
 */
export interface ExternalSourceChange {
  source_file: string;
  impact: (SourceFileImpact & { text: string }) | null;
  revision: string | null;
  problem: string | null;
}

export interface EpisodesViewEpisode {
  episode: number;
  origin: EpisodeSourceOrigin;
  /** 原文段出现在整本源文里。 */
  placed: boolean;
  /** 原文范围起点所在的整本源文文件（仅 placed）。 */
  source_file: string | null;
  /** 原文范围终点所在的整本源文文件（仅 placed）；不跨文件时与 source_file 相同。 */
  end_file: string | null;
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
  /** 尚未采纳或放弃的重新规划候选；没有时为 null。 */
  replan: ReplanSummary | null;
  /** 在 ArcReel 之外被改动过的文件，按文件顺序。 */
  external_changes: ExternalSourceChange[];
}

/** 整本源文里的一个位置：文件与文件内的码位偏移。 */
export interface SourcePoint {
  source_file: string;
  offset: number;
}

/** 候选里的一集。`same_as` 是原文范围一模一样的现有集，`overlaps` 是原文范围与它重叠的被替换集。 */
export interface ReplanCandidateEpisode {
  title: string;
  hook: string;
  source_file: string;
  start: number;
  end: number;
  units: number;
  first_sentence: string;
  last_sentence: string;
  same_as: number | null;
  overlaps: number[];
}

/**
 * 「新的分集方案」：重新规划生成的候选与采纳后的变化。分集账本在采纳前不变，集 ID 都是现有集的。
 * `stale` 非 null 时生成之后分集或源文有改动，候选只能放弃，变化清单为空。
 */
export interface ReplanSummary {
  id: string;
  /** 从哪一集开始重新规划。 */
  episode: number;
  instructions: string | null;
  /** 已生成到整本源文结尾。 */
  complete: boolean;
  /** 生成中途停止的原因：AI 找不到切分点、出错；创作者主动停止或还在生成时为 null。 */
  interrupted: "no_cut_point" | "failed" | null;
  stale: "ledger_changed" | "source_changed" | null;
  start: SourcePoint;
  /** 已生成到的位置。 */
  end: SourcePoint;
  /** 被替换的切出集数；候选过时或还没有集时为 null。 */
  old_count: number | null;
  new_count: number;
  units: number;
  average_units: number | null;
  /** 已开始制作，采纳后保留为无原文的集。 */
  retired: number[];
  /** 没有产物，采纳后移除。 */
  removed: number[];
  /** 保留为无原文、且新方案里找不到原文范围一模一样的集。 */
  needs_review: number[];
  /** 原文超出新方案覆盖范围、采纳时同样按被替换处理的集（方案没有生成到整本源文结尾时）。 */
  uncovered: number[];
  /** 播出位置会变的其他集。 */
  moved: { episode: number; from: number; to: number }[];
  episodes: ReplanCandidateEpisode[];
}

/** 重新规划的范围：`replaced` 是会被替换的切出集，`started` 是其中已开始制作的集。 */
export interface ReplanPreview {
  status: "preview";
  episode: number;
  source_file: string;
  offset: number;
  /** 分集规划之后源文有改动或有旧的切出集，只能从第一个切出集起重新规划。 */
  from_beginning: boolean;
  source_replaced: boolean;
  replaced: number[];
  started: number[];
}

/** 采纳的后果。`text` 与 `delete_text` 只在等待确认时出现，是服务端成文的确认文本。 */
export interface ReplanAdoptionImpact {
  candidate: string;
  episode: number;
  old_count: number;
  new_count: number;
  retired: number[];
  removed: number[];
  needs_review: number[];
  uncovered: number[];
  moved: { episode: number; from: number; to: number }[];
  revision: string;
}

export type ReplanAdoptionResponse =
  | { status: "adopted"; episodes: number[]; deleted: number[] }
  | { status: "confirmation_required"; impact: ReplanAdoptionImpact & { text: string; delete_text: string } };

export type AdoptSourceFileTarget =
  | { target: "whole_source" }
  | { target: "episode"; episode?: number | null };

/**
 * 手工切分的动作：切分、拆分、移动分界、与下一集合并、清除之后的切分。偏移是 `source_file` 内的码位偏移；
 * 缺省 `source_file` 时，拆分取这一集起点所在的文件，移动分界取这一集终点所在的文件。
 */
export type ManualSplitAction =
  | { action: "cut"; source_file: string; end: number; title?: string }
  | { action: "split"; episode: number; at: number; source_file?: string }
  | { action: "move_boundary"; episode: number; at: number; source_file?: string }
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
  /** 与下一集合并时并入的两集之间未切分原文的体量（阅读单位）；其余动作为 0。 */
  merged_units: number;
}

export type ManualSplitResponse =
  | { status: "applied"; episode: number | null; impact: ManualSplitImpact }
  /** `impact.text` 是服务端成文的确认清单。 */
  | { status: "confirmation_required"; impact: ManualSplitImpact & { text: string } };

/** 整本源文文件的改动（插入、替换、编辑、删除、调序）波及的集（集 ID）。 */
export interface SourceFileImpact {
  /** 原文没变，只平移位置。 */
  shifted: number[];
  /** 原文有变化、已有产物，标 stale。 */
  changed_with_products: number[];
  /** 原文有变化、还没有产物。 */
  changed_without_products: number[];
  /** 原文全部删掉、已有产物，转为无原文的集并移到播出顺序末尾。 */
  retired: number[];
  /** 原文全部删掉、没有产物，直接移除。 */
  removed: number[];
  /** 源文件类型改变，脚本规划会过期。 */
  kind_stale: number[];
}

/**
 * 整本源文文件改动的结果。`confirmation_required` 时没有写入：`impact.text` 是服务端成文的受影响集清单，
 * 带上 `revision` 重新提交即执行；清单在此期间变了时服务端再次要求确认。
 */
export type SourceFileChangeResponse =
  | { status: "applied"; impact: SourceFileImpact }
  | { status: "confirmation_required"; impact: SourceFileImpact & { text: string }; revision: string };

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

/** 「规划这段未切分的原文」：以这一点为终点的那段未切分原文。`end` 是文件内的码位偏移。 */
export interface PlanningGap {
  source_file: string;
  end: number;
}

export interface CreateEpisodeBody {
  /** 插在哪一集之后（集 ID）；缺省放在播出顺序末尾。 */
  after?: number | null;
  title?: string;
  hook?: string;
  /** 集原文；空白视同没有原文。 */
  source_text?: string | null;
  /** 集原文的源文件类型，只对剧情演绎项目生效。 */
  source_kind?: SourceKind | null;
}

/** 删除一集会丢失的内容。`text` 只在等待确认时出现，是服务端成文的确认文本。 */
export interface EpisodeDeletionImpact {
  episode: number;
  /** 删除不丢失任何无法在项目内重建的内容。 */
  recoverable: boolean;
  revision: string;
  text?: string;
}

export type EpisodeDeletionResponse =
  | { status: "deleted"; impact: EpisodeDeletionImpact }
  | { status: "confirmation_required"; impact: EpisodeDeletionImpact & { text: string } };
