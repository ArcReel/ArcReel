import type { UsageRecord, UsageRecordDetail, UsageSummary } from "@/types";

/** 一行使用记录；未覆写的列取一组不影响筛选与渲染的中性值。 */
export function makeUsageRecord(overrides: Partial<UsageRecord> = {}): UsageRecord {
  return {
    id: 1,
    project_name: "星海列车",
    purpose: "generation_task",
    task_id: null,
    task_type: null,
    media_type: "image",
    provider: "google",
    model: "imagen-4",
    status: "success",
    error_code: null,
    error_params: null,
    error_message: null,
    segment_id: "E1S10",
    output_path: null,
    started_at: "2026-03-15T08:00:00+00:00",
    finished_at: "2026-03-15T08:00:10+00:00",
    duration_ms: 10_000,
    cost_amount: 0.031,
    currency: "USD",
    input_tokens: null,
    output_tokens: null,
    usage_tokens: null,
    image_input_tokens: null,
    image_output_tokens: null,
    text_input_tokens: null,
    text_output_tokens: null,
    resolution: null,
    duration_seconds: null,
    aspect_ratio: null,
    session_id: null,
    ...overrides,
  };
}

export function makeUsageRecordDetail(
  overrides: Partial<UsageRecordDetail> = {},
): UsageRecordDetail {
  return {
    ...makeUsageRecord(),
    prompt: null,
    inputs: null,
    last_provider_response: null,
    ...overrides,
  };
}

export function makeUsageSummary(overrides: Partial<UsageSummary> = {}): UsageSummary {
  return {
    range: { since: "2026-02-14", until: "2026-03-15" },
    primary_currency: "CNY",
    kpi: {
      calls: 73,
      success: 65,
      failed: 7,
      cancelled: 1,
      success_rate: 0.9028,
      cost: { CNY: 75.3, USD: 4.59 },
    },
    daily: [],
    breakdown: {
      project: { rows: [], other: null },
      provider: { rows: [], other: null },
      model: { rows: [], other: null },
    },
    attention: [],
    filter_options: {
      projects: ["星海列车", "雨夜侦探"],
      providers: [
        { provider: "minimax", label: "MiniMax" },
        { provider: "google", label: "Google" },
      ],
      models: [
        { provider: "minimax", model: "hailuo-02" },
        { provider: "google", model: "imagen-4" },
      ],
    },
    ...overrides,
  };
}
