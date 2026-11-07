/**
 * Data for the Episodes view: backend `GET /projects/{name}/episodes-view`, matching the dataclasses in `lib/episode/episode_layout.py`
 * exactly. Segment `start` / `end` are Unicode code point offsets in normalized source text, not JavaScript string indices.
 */

export type EpisodeSourceOrigin = "whole_source" | "own" | "none";

/** Source kind: present only for drama projects. */
export type SourceKind = "novel" | "screenplay";

export interface EpisodesViewSegment {
  kind: "episode" | "unsplit";
  start: number;
  end: number;
  text: string;
  /** Episode ID when kind is episode. */
  episode: number | null;
  /** The unsplit segment precedes the last split episode by source position (between split episodes or before the first one). */
  gap: boolean;
  units: number;
  /** This episode segment continues the same episode from the previous file (the episode spans files, starting in an earlier file). */
  continued: boolean;
  /** This episode segment continues in the next file (the episode spans files, ending in a later file). */
  continues: boolean;
}

export interface EpisodesViewFile {
  source_file: string;
  name: string;
  /** Original filename at upload; null when no original backup was retained. */
  original_filename: string | null;
  /** The file is unreadable (missing, a symlink, or not UTF-8). */
  missing: boolean;
  /** Code point count of the full normalized text, the upper bound for file-local offsets; 0 when unreadable. */
  length: number;
  units: number;
  cut_units: number;
  segments: EpisodesViewSegment[];
  /** Null for non-drama projects. */
  source_kind: SourceKind | null;
  /** The file changed outside ArcReel and the episode ledger has not been updated: replacement, editing, reordering, type changes, and splitting are paused; deletion remains available. */
  changed_outside: boolean;
}

/**
 * A file changed outside ArcReel: affected episodes calculated by snapshot alignment. Pass `revision` unchanged to `acceptExternalSourceChange`
 * to confirm this list. If it cannot be calculated, `impact` and `revision` are null and `problem` is the server-rendered reason.
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
  /** The source segment appears in the whole source. */
  placed: boolean;
  /** Whole-source file containing the source range start (placed only). */
  source_file: string | null;
  /** Whole-source file containing the source range end (placed only); equal to source_file when not spanning files. */
  end_file: string | null;
  /** Null when the source cannot be read. */
  units: number | null;
  spoken_seconds: number | null;
  first_sentence: string;
  last_sentence: string;
  /** Episodes with their own source use their own record; split episodes use the file containing the source range start. Null for sourceless episodes and non-drama projects. */
  source_kind: SourceKind | null;
}

export interface UnregisteredSourceFile {
  name: string;
  size: number;
  /** The filename may join the whole source directly (no underscore prefix and not episode_N.txt). */
  can_join_whole_source: boolean;
}

export interface EpisodesView {
  /** Content size unit: characters (Chinese, etc.) or words (English, Vietnamese). */
  unit: "chars" | "words";
  units: number;
  cut_units: number;
  files: EpisodesViewFile[];
  episodes: EpisodesViewEpisode[];
  unregistered: UnregisteredSourceFile[];
  /** Replanning candidate not yet adopted or discarded; null when none exists. */
  replan: ReplanSummary | null;
  /** Files changed outside ArcReel, in file order. */
  external_changes: ExternalSourceChange[];
}

/** A position in the whole source: file and code point offset within that file. */
export interface SourcePoint {
  source_file: string;
  offset: number;
}

/** One candidate episode. `same_as` is an existing episode with an identical source range; `overlaps` lists replaced episodes whose source ranges overlap it. */
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
 * New episode plan: replanning candidates and changes after adoption. The episode ledger remains unchanged until adoption; episode IDs refer to existing episodes.
 * Non-null `stale` means episodes or source changed after generation; the candidate can only be discarded and the change list is empty.
 */
export interface ReplanSummary {
  id: string;
  /** Episode from which replanning starts. */
  episode: number;
  instructions: string | null;
  /** Generation has reached the end of the whole source. */
  complete: boolean;
  /** Reason generation stopped partway: the AI found no cut point or failed; null when the creator stopped it or generation is ongoing. */
  interrupted: "no_cut_point" | "failed" | null;
  stale: "ledger_changed" | "source_changed" | null;
  start: SourcePoint;
  /** Position reached by generation. */
  end: SourcePoint;
  /** Number of split episodes to replace; null when the candidate is stale or has no episodes yet. */
  old_count: number | null;
  new_count: number;
  units: number;
  average_units: number | null;
  /** Production has started; retained as sourceless episodes after adoption. */
  retired: number[];
  /** No artifacts; removed after adoption. */
  removed: number[];
  /** Retained as sourceless episodes with no identical source range in the new plan. */
  needs_review: number[];
  /** Episodes whose source lies outside the new plan's coverage, also treated as replaced on adoption (when the plan did not reach the whole-source end). */
  uncovered: number[];
  /** Other episodes whose broadcast positions change. */
  moved: { episode: number; from: number; to: number }[];
  episodes: ReplanCandidateEpisode[];
}

/** Replanning scope: `replaced` lists split episodes to replace; `started` lists those whose production has begun. */
export interface ReplanPreview {
  status: "preview";
  episode: number;
  source_file: string;
  offset: number;
  /** The source changed after episode planning or legacy split episodes exist; replanning can start only from the first split episode. */
  from_beginning: boolean;
  source_replaced: boolean;
  replaced: number[];
  started: number[];
}

/** Consequences of adoption. `text` and `delete_text` appear only while awaiting confirmation and contain server-rendered confirmation text. */
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
 * Manual splitting actions: cut, split, move boundary, merge with next episode, clear subsequent cuts. Offsets are code point offsets within `source_file`;
 * when `source_file` is omitted, split uses the file containing the episode start, and move boundary uses the file containing its end.
 */
export type ManualSplitAction =
  | { action: "cut"; source_file: string; end: number; title?: string }
  | { action: "split"; episode: number; at: number; source_file?: string }
  | { action: "move_boundary"; episode: number; at: number; source_file?: string }
  | { action: "merge_next"; episode: number }
  | { action: "clear_after"; episode: number };

/** Episodes affected by one manual split action (episode IDs). */
export interface ManualSplitImpact {
  /** The source range changed and artifacts exist; marked stale. */
  restaled: number[];
  /** Artifacts exist; converted to sourceless episodes and moved to the end of broadcast order. */
  retired: number[];
  /** No artifacts; removed directly. */
  removed: number[];
  /** Amount of unsplit source between the two episodes included when merging with the next episode (reading units); 0 for other actions. */
  merged_units: number;
}

export type ManualSplitResponse =
  | { status: "applied"; episode: number | null; impact: ManualSplitImpact }
  /** `impact.text` is the server-rendered confirmation list. */
  | { status: "confirmation_required"; impact: ManualSplitImpact & { text: string } };

/** Episodes affected by whole-source file changes (insert, replace, edit, delete, reorder), identified by episode ID. */
export interface SourceFileImpact {
  /** Source text unchanged; only positions shift. */
  shifted: number[];
  /** Source text changed and artifacts exist; marked stale. */
  changed_with_products: number[];
  /** Source text changed; no artifacts yet. */
  changed_without_products: number[];
  /** All source text deleted and artifacts exist; converted to sourceless episodes and moved to the end of broadcast order. */
  retired: number[];
  /** All source text deleted and no artifacts exist; removed directly. */
  removed: number[];
  /** Source kind changed; script planning becomes stale. */
  kind_stale: number[];
}

/**
 * Whole-source file change result. `confirmation_required` means nothing was written: `impact.text` is the server-rendered affected episode list,
 * and resubmitting with `revision` applies the change; if the list changes meanwhile, the server requests confirmation again.
 */
export type SourceFileChangeResponse =
  | { status: "applied"; impact: SourceFileImpact }
  | { status: "confirmation_required"; impact: SourceFileImpact & { text: string }; revision: string };

/** AI episode planning submission result: the first window's generation batch. Each later window is enqueued by the preceding running window. */
export interface EpisodePlanningResponse {
  batch: { batch_id: string; members: { unit_id: string; task_id: string | null; deduped?: boolean }[] };
}

/** Stop episode planning: `cancelled` lists cancelled queued windows; `running` lists executing windows that will finish normally. */
export interface StopEpisodePlanningResponse {
  cancelled: string[];
  running: string[];
}

/** Whole-source kind change result: `needs_confirmation` means nothing was written; `affected_episodes` lists episodes whose script plans become stale. */
export interface SourceKindChangeResult {
  success: boolean;
  applied: boolean;
  needs_confirmation: boolean;
  affected_episodes: number[];
}

/** Result of writing episode source on the episode page; `needs_confirmation` means neither source nor kind was written. */
export interface EpisodeSourceWriteResult extends SourceKindChangeResult {
  episode: number;
  source_origin: EpisodeSourceOrigin;
}

/** Plan this unsplit source: the unsplit range ending at this point. `end` is a code point offset within the file. */
export interface PlanningGap {
  source_file: string;
  end: number;
}

export interface CreateEpisodeBody {
  /** Insert after this episode (episode ID); defaults to the end of broadcast order. */
  after?: number | null;
  title?: string;
  hook?: string;
  /** Episode source text; whitespace is treated as no source. */
  source_text?: string | null;
  /** Source kind for episode source, effective only for drama projects. */
  source_kind?: SourceKind | null;
}

/** Content lost by deleting an episode. `text` appears only while awaiting confirmation and contains server-rendered confirmation text. */
export interface EpisodeDeletionImpact {
  episode: number;
  /** Deletion loses no content that cannot be recreated within the project. */
  recoverable: boolean;
  revision: string;
  text?: string;
}

export type EpisodeDeletionResponse =
  | { status: "deleted"; impact: EpisodeDeletionImpact }
  | { status: "confirmation_required"; impact: EpisodeDeletionImpact & { text: string } };
