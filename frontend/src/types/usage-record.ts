import type { EpisodeItemRef } from "./project";
import type { CallType } from "./provider";

export type UsageRecordStatus = "pending" | "success" | "failed" | "cancelled";

export interface UsageRecord {
  id: number;
  project_name: string;
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
  error_message: string | null;
  segment_id: string | null;
  segment_ref?: EpisodeItemRef | null;
  output_path: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  cost_amount: number | null;
  unpriced_reason: string | null;
  currency: string;
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

export interface UsageRecordInputImage {
  path: string;
  label?: string | null;
  role?: string | null;
}

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
  provider_in_settings: boolean;
}

export interface UsageRecordPage {
  items: UsageRecord[];
  next_cursor: string | null;
  total: number;
}

export interface UsageStatsBlock {
  calls: number;
  success: number;
  failed: number;
  cancelled: number;
  success_rate: number | null;
  cost: Record<string, number>;
  unpriced_calls: number;
}

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
  agent?: boolean;
}

export interface UsageModelRow extends UsageStatsBlock {
  provider: string;
  model: string;
  agent?: boolean;
}

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

export interface UsageFilterOptions {
  projects: string[];
  project_titles?: Record<string, string>;
  providers: { provider: string; label: string }[];
  models: { provider: string; model: string }[];
}

export interface UsageSummary {
  range: UsageSummaryRange | null;
  primary_currency: string | null;
  kpi: UsageStatsBlock;
  daily: UsageDailyBucket[];
  breakdown: UsageBreakdown;
  attention: UsageAttention[];
  filter_options: UsageFilterOptions;
}

export type UsageView = "generation" | "agent";

export interface UsageAgentSession {
  session_id: string;
  title: string | null;
  session_deleted: boolean;
  project_name: string;
  project_title?: string | null;
  models: string[];
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost: Record<string, number>;
  unpriced_calls: number;
  first_started_at: string;
  last_started_at: string;
}

export interface UsageAgentSessionPage {
  items: UsageAgentSession[];
  next_cursor: string | null;
  total: number;
}

export interface UsageAgentSessionRecord {
  id: number;
  started_at: string;
  status: UsageRecordStatus;
  prompt: string | null;
  provider: string;
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_5m_tokens: number | null;
  cache_write_1h_tokens: number | null;
  cost_amount: number | null;
  unpriced_reason: string | null;
  currency: string;
}

export interface UsageAgentSessionDetail {
  session_id: string;
  items: UsageAgentSessionRecord[];
}
