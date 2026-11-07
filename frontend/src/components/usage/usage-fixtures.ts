import type {
  UsageAgentSession,
  UsageAgentSessionRecord,
  UsageDailyBucket,
  UsageRecord,
  UsageRecordDetail,
  UsageSummary,
} from "@/types";

/** One usage-record row; unspecified columns use neutral values that do not affect filtering or rendering. */
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
    unpriced_reason: null,
    currency: "USD",
    input_tokens: null,
    output_tokens: null,
    cache_read_tokens: null,
    cache_write_5m_tokens: null,
    cache_write_1h_tokens: null,
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
    provider_in_settings: true,
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
      unpriced_calls: 0,
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

/** Daily buckets for `days` consecutive days from `from`; `fill` sets each day's counts and per-media costs. */
export function makeUsageDaily(
  days: number,
  from = "2026-03-01",
  fill: (index: number) => Partial<UsageDailyBucket> = () => ({}),
): UsageDailyBucket[] {
  const start = new Date(`${from}T00:00:00Z`);
  return Array.from({ length: days }, (_, index) => {
    const day = new Date(start);
    day.setUTCDate(day.getUTCDate() + index);
    return {
      date: day.toISOString().slice(0, 10),
      success: 0,
      failed: 0,
      cancelled: 0,
      cost_by_media_type: { image: 0, video: 0, text: 0, audio: 0 },
      ...fill(index),
    };
  });
}

/** One session-list row; unspecified columns represent a priced session with no unpriced records. */
export function makeUsageAgentSession(overrides: Partial<UsageAgentSession> = {}): UsageAgentSession {
  return {
    session_id: "sess-1",
    title: "分镜讨论",
    session_deleted: false,
    project_name: "星海列车",
    project_title: null,
    models: ["deepseek-chat"],
    calls: 2,
    input_tokens: 100_000,
    output_tokens: 23_000,
    cost: { USD: 0.5 },
    unpriced_calls: 0,
    first_started_at: "2026-03-15T08:00:00+00:00",
    last_started_at: "2026-03-15T09:00:00+00:00",
    ...overrides,
  };
}

/** One session-detail record; unspecified columns represent a priced, uncached record. */
export function makeUsageAgentSessionRecord(
  overrides: Partial<UsageAgentSessionRecord> = {},
): UsageAgentSessionRecord {
  return {
    id: 1,
    started_at: "2026-03-15T08:00:00+00:00",
    status: "success",
    prompt: "把第三幕的分镜改短一些",
    provider: "agent-1",
    model: "deepseek-chat",
    input_tokens: 1000,
    output_tokens: 200,
    cache_read_tokens: null,
    cache_write_5m_tokens: null,
    cache_write_1h_tokens: null,
    cost_amount: 0.0031,
    unpriced_reason: null,
    currency: "USD",
    ...overrides,
  };
}
