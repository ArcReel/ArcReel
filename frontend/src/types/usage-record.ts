import type { EpisodeItemRef } from "./project";
import type { CallType } from "./provider";

/** Call state vocabulary, matching backend `CallStatus`. */
export type UsageRecordStatus = "pending" | "success" | "failed" | "cancelled";

/** List projection of one provider call; `user_id` is excluded from the response. */
export interface UsageRecord {
  id: number;
  /** An empty string for endpoint trial-run records. */
  project_name: string;
  /** Project title; null when deleted, unreadable, or untitled, in which case the UI falls back to the project name. */
  project_title?: string | null;
  purpose: string | null;
  task_id: string | null;
  task_type: string | null;
  media_type: CallType;
  provider: string;
  model: string;
  status: UsageRecordStatus;
  error_code: string | null;
  error_params: Record<string, unknown> | null;
  /** Original failure text from the provider, rendered when no error code exists. */
  error_message: string | null;
  segment_id: string | null;
  /** Title and broadcast position of the episode containing segment_id; null when the ID has no episode prefix or the episode is no longer in the ledger. */
  segment_ref?: EpisodeItemRef | null;
  output_path: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  /** null means unpriced (see `unpriced_reason`); 0 is a price of zero. */
  cost_amount: number | null;
  /** Unpriced reason code (backend `UnpricedReason`); null for priced records. */
  unpriced_reason: string | null;
  currency: string;
  /** Total input, including cache reads and writes; uncached input = input_tokens − cache reads − both cache-write tiers. */
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_5m_tokens: number | null;
  cache_write_1h_tokens: number | null;
  usage_tokens: number | null;
  image_input_tokens: number | null;
  image_output_tokens: number | null;
  text_input_tokens: number | null;
  text_output_tokens: number | null;
  resolution: string | null;
  duration_seconds: number | null;
  aspect_ratio: string | null;
  session_id: string | null;
}

/** Input media such as reference images / first frames, using project-relative paths. */
export interface UsageRecordInputImage {
  path: string;
  label?: string | null;
  role?: string | null;
}

/** Details add three heavy payloads to the list entry, plus whether the provider remains in provider settings. */
export interface UsageRecordDetail extends UsageRecord {
  prompt: string | null;
  inputs: {
    reference_images?: UsageRecordInputImage[];
    start_image?: string | null;
    end_image?: string | null;
    reference_audio?: string[];
    voice?: string | null;
    parameters?: Record<string, unknown>;
  } | null;
  last_provider_response: unknown;
  /** The called provider remains in provider settings (a built-in or an undeleted custom provider); when true, the model name links to provider model settings. */
  provider_in_settings: boolean;
}

/** One page of keyset pagination; an empty `next_cursor` means the last page. */
export interface UsageRecordPage {
  items: UsageRecord[];
  next_cursor: string | null;
  /** Filtered total, independent of the current page. */
  total: number;
}

/** Counts and reference costs by currency for a group of calls. */
export interface UsageStatsBlock {
  calls: number;
  success: number;
  failed: number;
  cancelled: number;
  /** Null when the denominator is zero. */
  success_rate: number | null;
  /** Totals only priced calls. */
  cost: Record<string, number>;
  /** Count of unpriced calls (null cost), excluded from `cost`. */
  unpriced_calls: number;
}

/** Local-date range covered by the trend, inclusive at both ends. */
export interface UsageSummaryRange {
  since: string;
  until: string;
}

export interface UsageDailyBucket {
  date: string;
  success: number;
  failed: number;
  cancelled: number;
  cost_by_media_type: Record<string, number>;
}

export interface UsageProjectRow extends UsageStatsBlock {
  project_name: string;
}

export interface UsageProviderRow extends UsageStatsBlock {
  provider: string;
  /** An aggregate of Agent records: `provider` is empty. */
  agent?: boolean;
}

export interface UsageModelRow extends UsageStatsBlock {
  provider: string;
  model: string;
  /** An aggregate of Agent records by model: `provider` is empty. */
  agent?: boolean;
}

/** Remainder outside the breakdown table list: number of merged groups and their totals. */
export interface UsageBreakdownOther extends UsageStatsBlock {
  groups: number;
}

export interface UsageBreakdown {
  project: { rows: UsageProjectRow[]; other: UsageBreakdownOther | null };
  provider: { rows: UsageProviderRow[]; other: UsageBreakdownOther | null };
  model: { rows: UsageModelRow[]; other: UsageBreakdownOther | null };
}

export interface UsageFailureRateAttention {
  type: "failure_rate";
  provider: string;
  model: string | null;
  success: number;
  failed: number;
  failure_rate: number;
  overall_failure_rate: number;
}

export interface UsageConsecutiveFailuresAttention {
  type: "consecutive_failures";
  project_name: string;
  media_type: CallType;
  segment_id: string;
  segment_ref?: EpisodeItemRef | null;
  count: number;
  first_failed_at: string;
  last_failed_at: string;
  last_error_code: string | null;
}

export type UsageAttention =
  | UsageFailureRateAttention
  | UsageConsecutiveFailuresAttention;

/** Filter options are distinct values from the entire table and do not change with filtering. */
export interface UsageFilterOptions {
  projects: string[];
  /** Candidate projects with readable titles: project name → title. */
  project_titles?: Record<string, string>;
  providers: { provider: string; label: string }[];
  models: { provider: string; model: string }[];
}

export interface UsageSummary {
  /** Null when there are no records. */
  range: UsageSummaryRange | null;
  /** Currency with the largest aggregated amount during the period; null when there are no records. */
  primary_currency: string | null;
  kpi: UsageStatsBlock;
  daily: UsageDailyBucket[];
  breakdown: UsageBreakdown;
  attention: UsageAttention[];
  filter_options: UsageFilterOptions;
}

/** Two usage record views: "Generation calls" excludes Agent records; "Agent sessions" contains only Agent records. */
export type UsageView = "generation" | "agent";

/** One Agent session list row; totals include only records matching the filters. */
export interface UsageAgentSession {
  /** SDK session ID. */
  session_id: string;
  /** Session title; null when session metadata was deleted, and an empty string when not yet named. */
  title: string | null;
  session_deleted: boolean;
  project_name: string;
  project_title?: string | null;
  /** Models used by the session, in order of first appearance. */
  models: string[];
  calls: number;
  /** Total input, including cache reads and writes. */
  input_tokens: number;
  output_tokens: number;
  /** Costs of priced records, totaled separately by currency. */
  cost: Record<string, number>;
  /** Count of unpriced records (null cost), excluded from `cost`. */
  unpriced_calls: number;
  first_started_at: string;
  last_started_at: string;
}

export interface UsageAgentSessionPage {
  items: UsageAgentSession[];
  next_cursor: string | null;
  total: number;
}

/** One session detail record: usage and cost for a model in one turn. */
export interface UsageAgentSessionRecord {
  id: number;
  started_at: string;
  status: UsageRecordStatus;
  /** User message for this turn, truncated when too long. */
  prompt: string | null;
  provider: string;
  model: string;
  /** Total input, including cache reads and writes. */
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_5m_tokens: number | null;
  cache_write_1h_tokens: number | null;
  /** null means unpriced; see `unpriced_reason`. */
  cost_amount: number | null;
  unpriced_reason: string | null;
  currency: string;
}

export interface UsageAgentSessionDetail {
  session_id: string;
  items: UsageAgentSessionRecord[];
}
