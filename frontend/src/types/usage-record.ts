import type { CallType } from "./provider";

/** 调用状态词汇，与后端 `CallStatus` 一致。 */
export type UsageRecordStatus = "pending" | "success" | "failed" | "cancelled";

/** 一次供应商调用在列表里的投影；`user_id` 不出现在响应中。 */
export interface UsageRecord {
  id: number;
  /** 端点试跑记录为空串。 */
  project_name: string;
  purpose: string | null;
  task_id: string | null;
  task_type: string | null;
  media_type: CallType;
  provider: string;
  model: string;
  status: UsageRecordStatus;
  error_code: string | null;
  error_params: Record<string, unknown> | null;
  /** 供应商返回的失败原文，无错误码时按它渲染。 */
  error_message: string | null;
  segment_id: string | null;
  output_path: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  cost_amount: number;
  currency: string;
  input_tokens: number | null;
  output_tokens: number | null;
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

/** 参考图 / 首帧等输入素材，路径为项目内相对路径。 */
export interface UsageRecordInputImage {
  path: string;
  label?: string | null;
  role?: string | null;
}

/** 详情比列表多三项重载荷。 */
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
}

/** keyset 分页的一页；`next_cursor` 为空表示已到末页。 */
export interface UsageRecordPage {
  items: UsageRecord[];
  next_cursor: string | null;
  /** 筛选后的总数，与当前页无关。 */
  total: number;
}

/** 一组调用的计数与分币种参考费用。 */
export interface UsageStatsBlock {
  calls: number;
  success: number;
  failed: number;
  cancelled: number;
  /** 分母为 0 时为 null。 */
  success_rate: number | null;
  cost: Record<string, number>;
}

/** 趋势覆盖的本地日区间，两端均含。 */
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
}

export interface UsageModelRow extends UsageStatsBlock {
  provider: string;
  model: string;
}

/** 构成表列表外的余量：被合并的分组数与它们的合计。 */
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
  count: number;
  first_failed_at: string;
  last_failed_at: string;
  last_error_code: string | null;
}

export type UsageAttention =
  | UsageFailureRateAttention
  | UsageConsecutiveFailuresAttention;

/** 筛选候选值取全表 distinct，不随筛选变化。 */
export interface UsageFilterOptions {
  projects: string[];
  providers: { provider: string; label: string }[];
  models: { provider: string; model: string }[];
}

export interface UsageSummary {
  /** 无记录时为 null。 */
  range: UsageSummaryRange | null;
  /** 期间内汇总金额最大的币种；无记录时为 null。 */
  primary_currency: string | null;
  kpi: UsageStatsBlock;
  daily: UsageDailyBucket[];
  breakdown: UsageBreakdown;
  attention: UsageAttention[];
  filter_options: UsageFilterOptions;
}
