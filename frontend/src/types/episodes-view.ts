
export type EpisodeSourceOrigin = "whole_source" | "own" | "none";

export type SourceKind = "novel" | "screenplay";

export interface EpisodesViewSegment {
  kind: "episode" | "unsplit";
  start: number;
  end: number;
  text: string;
  episode: number | null;
  gap: boolean;
  units: number;
  continued: boolean;
  continues: boolean;
}

export interface EpisodesViewFile {
  source_file: string;
  name: string;
  original_filename: string | null;
  missing: boolean;
  length: number;
  units: number;
  cut_units: number;
  segments: EpisodesViewSegment[];
  source_kind: SourceKind | null;
  changed_outside: boolean;
}

export interface ExternalSourceChange {
  source_file: string;
  impact: (SourceFileImpact & { text: string }) | null;
  revision: string | null;
  problem: string | null;
}

export interface EpisodesViewEpisode {
  episode: number;
  origin: EpisodeSourceOrigin;
  placed: boolean;
  source_file: string | null;
  end_file: string | null;
  units: number | null;
  spoken_seconds: number | null;
  first_sentence: string;
  last_sentence: string;
  source_kind: SourceKind | null;
}

export interface UnregisteredSourceFile {
  name: string;
  size: number;
  can_join_whole_source: boolean;
}

export interface EpisodesView {
  unit: "chars" | "words";
  units: number;
  cut_units: number;
  files: EpisodesViewFile[];
  episodes: EpisodesViewEpisode[];
  unregistered: UnregisteredSourceFile[];
  replan: ReplanSummary | null;
  external_changes: ExternalSourceChange[];
}

export interface SourcePoint {
  source_file: string;
  offset: number;
}

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

export interface ReplanSummary {
  id: string;
  episode: number;
  instructions: string | null;
  complete: boolean;
  interrupted: "no_cut_point" | "failed" | null;
  stale: "ledger_changed" | "source_changed" | null;
  start: SourcePoint;
  end: SourcePoint;
  old_count: number | null;
  new_count: number;
  units: number;
  average_units: number | null;
  retired: number[];
  removed: number[];
  needs_review: number[];
  uncovered: number[];
  moved: { episode: number; from: number; to: number }[];
  episodes: ReplanCandidateEpisode[];
}

export interface ReplanPreview {
  status: "preview";
  episode: number;
  source_file: string;
  offset: number;
  from_beginning: boolean;
  source_replaced: boolean;
  replaced: number[];
  started: number[];
}

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

export type ManualSplitAction =
  | { action: "cut"; source_file: string; end: number; title?: string }
  | { action: "split"; episode: number; at: number; source_file?: string }
  | { action: "move_boundary"; episode: number; at: number; source_file?: string }
  | { action: "merge_next"; episode: number }
  | { action: "clear_after"; episode: number };

export interface ManualSplitImpact {
  restaled: number[];
  retired: number[];
  removed: number[];
  merged_units: number;
}

export type ManualSplitResponse =
  | { status: "applied"; episode: number | null; impact: ManualSplitImpact }
  | { status: "confirmation_required"; impact: ManualSplitImpact & { text: string } };

export interface SourceFileImpact {
  shifted: number[];
  changed_with_products: number[];
  changed_without_products: number[];
  retired: number[];
  removed: number[];
  kind_stale: number[];
}

export type SourceFileChangeResponse =
  | { status: "applied"; impact: SourceFileImpact }
  | { status: "confirmation_required"; impact: SourceFileImpact & { text: string }; revision: string };

export interface EpisodePlanningResponse {
  batch: { batch_id: string; members: { unit_id: string; task_id: string | null; deduped?: boolean }[] };
}

export interface StopEpisodePlanningResponse {
  cancelled: string[];
  running: string[];
}

export interface SourceKindChangeResult {
  success: boolean;
  applied: boolean;
  needs_confirmation: boolean;
  affected_episodes: number[];
}

export interface EpisodeSourceWriteResult extends SourceKindChangeResult {
  episode: number;
  source_origin: EpisodeSourceOrigin;
}

export interface PlanningGap {
  source_file: string;
  end: number;
}

export interface CreateEpisodeBody {
  after?: number | null;
  title?: string;
  hook?: string;
  source_text?: string | null;
  source_kind?: SourceKind | null;
}

export interface EpisodeDeletionImpact {
  episode: number;
  recoverable: boolean;
  revision: string;
  text?: string;
}

export type EpisodeDeletionResponse =
  | { status: "deleted"; impact: EpisodeDeletionImpact }
  | { status: "confirmation_required"; impact: EpisodeDeletionImpact & { text: string } };
