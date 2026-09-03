// PROTOTYPE — wayfinder #2290 假数据与聚合。形状对齐 #2288 / #2289 的决议：
// 终态行来自 UsageRecord，进行中区 = 活跃任务 ∪ 无任务 pending 调用；summary 四块在此按同一口径客户端算出，
// 让构成表点击→筛选→全页重算的闭环在原型里可交互。评审后整目录删除。

export type MediaType = "image" | "video" | "text" | "audio";
export type RecordStatus = "pending" | "success" | "failed" | "cancelled";
export type Purpose =
  | "script_generation"
  | "episode_planning"
  | "project_overview"
  | "style_analysis"
  | "assistant_session"
  | "endpoint_trial"
  | "generation_task";

export interface UsageRecord {
  id: number;
  project_name: string;
  purpose: Purpose;
  task_id: string | null;
  task_type: string | null;
  media_type: MediaType;
  provider: string;
  model: string;
  status: RecordStatus;
  error_code: string | null;
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
}

export type TaskStatus = "queued" | "running" | "cancelling";

/** 进行中区的一行：来自任务 store 的活跃任务（无费用、无模型解析时 model 为 null）。 */
export interface ActiveTask {
  task_id: string;
  project_name: string;
  media_type: Exclude<MediaType, "text">;
  task_type: string;
  provider: string;
  model: string | null;
  status: TaskStatus;
  segment_id: string;
  queued_at: string;
  started_at: string | null;
  warnings: number;
}

export interface Stats {
  calls: number;
  success: number;
  failed: number;
  cancelled: number;
  success_rate: number | null;
  cost: Record<string, number>;
}

export interface DailyBucket {
  date: string;
  success: number;
  failed: number;
  cancelled: number;
  cost_by_media_type: Record<MediaType, number>;
}

export type AttentionItem =
  | {
      type: "failure_rate";
      provider: string;
      model: string | null;
      success: number;
      failed: number;
      failure_rate: number;
      overall_failure_rate: number;
    }
  | {
      type: "consecutive_failures";
      project_name: string;
      media_type: MediaType;
      segment_id: string;
      count: number;
      first_failed_at: string;
      last_failed_at: string;
      last_error_code: string | null;
    };

export interface BreakdownRow extends Stats {
  project_name?: string;
  provider?: string;
  model?: string;
}

export interface Summary {
  range: { since: string; until: string } | null;
  primary_currency: string | null;
  kpi: Stats;
  daily: DailyBucket[];
  breakdown: {
    project: BreakdownRow[];
    provider: BreakdownRow[];
    model: BreakdownRow[];
  };
  attention: AttentionItem[];
}

export interface Filters {
  project: string | null;
  provider: string | null;
  model: string | null;
  media: MediaType | null;
  status: RecordStatus | null;
  /** 天数；0 = 全部 */
  range: number;
  segment: string | null;
}

export const EMPTY_FILTERS: Filters = {
  project: null,
  provider: null,
  model: null,
  media: null,
  status: null,
  range: 30,
  segment: null,
};

// ---------------------------------------------------------------------------
// 目录（供应商显示名、模型、项目）
// ---------------------------------------------------------------------------

export const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  google: "Google",
  minimax: "MiniMax",
  volcengine: "火山引擎",
  elevenlabs: "ElevenLabs",
};

export const PROJECTS = ["星海列车", "雨夜侦探", "盐城纪", ""] as const;

const MODELS: Record<MediaType, Array<{ provider: string; model: string; currency: string; unit: number }>> = {
  text: [
    { provider: "openai", model: "gpt-5.1", currency: "USD", unit: 0.018 },
    { provider: "google", model: "gemini-3-pro", currency: "USD", unit: 0.011 },
  ],
  image: [
    { provider: "google", model: "imagen-4", currency: "USD", unit: 0.04 },
    { provider: "volcengine", model: "seedream-4.0", currency: "CNY", unit: 0.2 },
  ],
  video: [
    { provider: "minimax", model: "hailuo-02", currency: "CNY", unit: 3.6 },
    { provider: "google", model: "veo-3", currency: "USD", unit: 0.75 },
    { provider: "volcengine", model: "seedance-1.0-pro", currency: "CNY", unit: 2.4 },
  ],
  audio: [{ provider: "elevenlabs", model: "eleven_v3", currency: "USD", unit: 0.03 }],
};

export const MEDIA_LABELS: Record<MediaType, string> = {
  image: "图片",
  video: "视频",
  text: "文本",
  audio: "配音",
};

export const PURPOSE_LABELS: Record<Purpose, string> = {
  script_generation: "剧本生成",
  episode_planning: "分集规划",
  project_overview: "项目概览",
  style_analysis: "风格分析",
  assistant_session: "助手会话",
  endpoint_trial: "端点试跑",
  generation_task: "生成任务",
};

export const ERROR_LABELS: Record<string, string> = {
  provider_rate_limited: "供应商限流，已重试 3 次后放弃",
  content_policy: "供应商内容策略拒绝了该提示词",
  provider_timeout: "供应商在 600 秒内未返回结果",
  download_failed: "生成成功但结果下载失败，可重试下载",
};

// ---------------------------------------------------------------------------
// 假数据生成（固定种子，刷新不变）
// ---------------------------------------------------------------------------

function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const NOW = new Date();
const DAY_MS = 86_400_000;

function buildRecords(): UsageRecord[] {
  const rand = mulberry32(2290);
  const pick = <T,>(xs: readonly T[]): T => xs[Math.floor(rand() * xs.length)];
  const out: UsageRecord[] = [];
  let id = 4000;
  const days = 180;
  for (let d = days; d >= 0; d--) {
    // 项目脉冲：有些天几乎没用量，有些天集中生成
    const burst = rand() < 0.35 ? 1 + Math.floor(rand() * 12) : Math.floor(rand() * 3);
    const dayProject = pick(PROJECTS.slice(0, 3));
    for (let i = 0; i < burst; i++) {
      const media: MediaType = pick(["image", "image", "video", "video", "text", "audio"]);
      const spec = pick(MODELS[media]);
      const isTrial = rand() < 0.03;
      const project = isTrial ? "" : rand() < 0.8 ? dayProject : pick(PROJECTS.slice(0, 3));
      const start = new Date(NOW.getTime() - d * DAY_MS - rand() * 14 * 3600_000 - 3600_000);
      let status: RecordStatus = "success";
      let failP = 0.06;
      if (spec.provider === "minimax") failP = 0.34; // 触发「失败率偏高」
      if (rand() < failP) status = "failed";
      else if (rand() < 0.03) status = "cancelled";
      const durationMs =
        media === "video" ? 60_000 + rand() * 240_000 : media === "image" ? 4_000 + rand() * 20_000 : media === "audio" ? 2_000 + rand() * 6_000 : 1_500 + rand() * 9_000;
      const seg = media === "text" ? null : `E${1 + Math.floor(rand() * 3)}S${1 + Math.floor(rand() * 24)}`;
      const purpose: Purpose =
        media === "text"
          ? pick(["script_generation", "episode_planning", "assistant_session", "style_analysis"])
          : isTrial
            ? "endpoint_trial"
            : "generation_task";
      const errorCode = status === "failed" ? pick(Object.keys(ERROR_LABELS)) : null;
      out.push({
        id: id++,
        project_name: project,
        purpose,
        task_id: purpose === "generation_task" ? `t_${id.toString(36)}` : null,
        task_type: purpose === "generation_task" ? `${media}_generation` : null,
        media_type: media,
        provider: spec.provider,
        model: spec.model,
        status,
        error_code: errorCode,
        error_message: errorCode ? `HTTP 429 from upstream: {"code":"${errorCode}"}` : null,
        segment_id: seg,
        output_path: status === "success" && media !== "text" ? `${project || "trial"}/${seg}.${media === "video" ? "mp4" : media === "image" ? "png" : "mp3"}` : null,
        started_at: start.toISOString(),
        finished_at: new Date(start.getTime() + durationMs).toISOString(),
        duration_ms: Math.round(durationMs),
        cost_amount: status === "cancelled" ? 0 : Math.round(spec.unit * (0.6 + rand()) * 1000) / 1000,
        currency: spec.currency,
        input_tokens: media === "text" ? 800 + Math.floor(rand() * 6000) : null,
        output_tokens: media === "text" ? 200 + Math.floor(rand() * 3000) : null,
      });
    }
  }
  // 同一目标连续失败：昨天与今天对 雨夜侦探 / video / E2S07 连失 3 次
  for (let k = 0; k < 3; k++) {
    const start = new Date(NOW.getTime() - (26 - k * 9) * 3600_000);
    out.push({
      id: id++,
      project_name: "雨夜侦探",
      purpose: "generation_task",
      task_id: `t_seq${k}`,
      task_type: "video_generation",
      media_type: "video",
      provider: "minimax",
      model: "hailuo-02",
      status: "failed",
      error_code: "content_policy",
      error_message: 'HTTP 400: {"code":"content_policy"}',
      segment_id: "E2S07",
      output_path: null,
      started_at: start.toISOString(),
      finished_at: new Date(start.getTime() + 41_000).toISOString(),
      duration_ms: 41_000,
      cost_amount: 3.6,
      currency: "CNY",
      input_tokens: null,
      output_tokens: null,
    });
  }
  // 无任务的 pending 文本调用（流式剧本生成正在进行）
  out.push({
    id,
    project_name: "星海列车",
    purpose: "script_generation",
    task_id: null,
    task_type: null,
    media_type: "text",
    provider: "openai",
    model: "gpt-5.1",
    status: "pending",
    error_code: null,
    error_message: null,
    segment_id: null,
    output_path: null,
    started_at: new Date(NOW.getTime() - 48_000).toISOString(),
    finished_at: null,
    duration_ms: null,
    cost_amount: 0,
    currency: "USD",
    input_tokens: null,
    output_tokens: null,
  });
  return out.sort((a, b) => (a.started_at < b.started_at ? 1 : a.started_at > b.started_at ? -1 : b.id - a.id));
}

export const RECORDS: UsageRecord[] = buildRecords();

export const ACTIVE_TASKS: ActiveTask[] = [
  {
    task_id: "t_run1",
    project_name: "星海列车",
    media_type: "video",
    task_type: "video_generation",
    provider: "google",
    model: "veo-3",
    status: "running",
    segment_id: "E1S12",
    queued_at: new Date(NOW.getTime() - 200_000).toISOString(),
    started_at: new Date(NOW.getTime() - 137_000).toISOString(),
    warnings: 0,
  },
  {
    task_id: "t_run2",
    project_name: "星海列车",
    media_type: "image",
    task_type: "image_generation",
    provider: "google",
    model: "imagen-4",
    status: "running",
    segment_id: "E1S13",
    queued_at: new Date(NOW.getTime() - 30_000).toISOString(),
    started_at: new Date(NOW.getTime() - 9_000).toISOString(),
    warnings: 1,
  },
  {
    task_id: "t_q1",
    project_name: "雨夜侦探",
    media_type: "video",
    task_type: "video_generation",
    provider: "minimax",
    model: null,
    status: "queued",
    segment_id: "E2S08",
    queued_at: new Date(NOW.getTime() - 12_000).toISOString(),
    started_at: null,
    warnings: 0,
  },
];

// ---------------------------------------------------------------------------
// 筛选与聚合（口径对齐 #2289）
// ---------------------------------------------------------------------------

export function rangeSince(range: number): Date | null {
  if (range === 0) return null;
  const d = new Date(NOW);
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - (range - 1));
  return d;
}

function matchDims(r: { project_name: string; provider: string; model: string | null; media_type: MediaType; segment_id: string | null }, f: Filters) {
  if (f.project !== null && r.project_name !== f.project) return false;
  if (f.provider && r.provider !== f.provider) return false;
  if (f.model && r.model !== f.model) return false;
  if (f.media && r.media_type !== f.media) return false;
  if (f.segment && r.segment_id !== f.segment) return false;
  return true;
}

/** 终态记录：summary 用（不收 status），记录表用（收 status）。 */
export function filterTerminal(f: Filters, withStatus: boolean): UsageRecord[] {
  const since = rangeSince(f.range);
  return RECORDS.filter((r) => {
    if (r.status === "pending") return false;
    if (!matchDims(r, f)) return false;
    if (since && new Date(r.started_at) < since) return false;
    if (withStatus && f.status && r.status !== f.status) return false;
    return true;
  });
}

export type ActiveRow =
  | { kind: "task"; task: ActiveTask; at: string }
  | { kind: "call"; record: UsageRecord; at: string };

/** 进行中区：任务 store ∪ 无任务 pending 调用，不受时间范围影响。 */
export function activeRows(f: Filters): ActiveRow[] {
  const rows: ActiveRow[] = [];
  for (const t of ACTIVE_TASKS) {
    if (!matchDims({ ...t, segment_id: t.segment_id }, f)) continue;
    if (f.status && f.status !== "pending") continue;
    rows.push({ kind: "task", task: t, at: t.started_at ?? t.queued_at });
  }
  for (const r of RECORDS) {
    if (r.status !== "pending" || r.task_id) continue;
    if (!matchDims(r, f)) continue;
    if (f.status && f.status !== "pending") continue;
    rows.push({ kind: "call", record: r, at: r.started_at });
  }
  return rows.sort((a, b) => (a.at < b.at ? 1 : -1));
}

function emptyStats(): Stats {
  return { calls: 0, success: 0, failed: 0, cancelled: 0, success_rate: null, cost: {} };
}

function addStat(s: Stats, r: UsageRecord) {
  s.calls++;
  if (r.status === "success") s.success++;
  else if (r.status === "failed") s.failed++;
  else if (r.status === "cancelled") s.cancelled++;
  if (r.cost_amount > 0) s.cost[r.currency] = (s.cost[r.currency] ?? 0) + r.cost_amount;
}

function finishStat(s: Stats): Stats {
  const denom = s.success + s.failed;
  s.success_rate = denom === 0 ? null : s.success / denom;
  for (const k of Object.keys(s.cost)) s.cost[k] = Math.round(s.cost[k] * 1000) / 1000;
  return s;
}

function localDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function wilsonLower(k: number, n: number, z = 1.96): number {
  if (n === 0) return 0;
  const p = k / n;
  const denom = 1 + (z * z) / n;
  const centre = p + (z * z) / (2 * n);
  const margin = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  return (centre - margin) / denom;
}

export function computeSummary(f: Filters): Summary {
  const rows = filterTerminal(f, false);
  const kpi = emptyStats();
  const byProject = new Map<string, Stats>();
  const byProvider = new Map<string, Stats>();
  const byModel = new Map<string, Stats>();
  const daily = new Map<string, DailyBucket>();
  const groupStat = (m: Map<string, Stats>, key: string, r: UsageRecord) => {
    let s = m.get(key);
    if (!s) m.set(key, (s = emptyStats()));
    addStat(s, r);
  };
  for (const r of rows) {
    addStat(kpi, r);
    groupStat(byProject, r.project_name, r);
    groupStat(byProvider, r.provider, r);
    groupStat(byModel, `${r.provider} ${r.model}`, r);
  }
  finishStat(kpi);
  let primary: string | null = null;
  let best = -1;
  for (const [cur, amt] of Object.entries(kpi.cost).sort(([a], [b]) => (a === "USD" ? -1 : b === "USD" ? 1 : a.localeCompare(b)))) {
    if (amt > best) {
      best = amt;
      primary = cur;
    }
  }
  // 日桶：补零；只画主币种
  const since = rangeSince(f.range) ?? (rows.length ? new Date(localDate(rows[rows.length - 1].started_at)) : null);
  if (since) {
    const until = new Date(NOW);
    until.setHours(0, 0, 0, 0);
    for (let d = new Date(since); d <= until; d = new Date(d.getTime() + DAY_MS)) {
      const key = localDate(d.toISOString());
      daily.set(key, { date: key, success: 0, failed: 0, cancelled: 0, cost_by_media_type: { image: 0, video: 0, text: 0, audio: 0 } });
    }
  }
  for (const r of rows) {
    const b = daily.get(localDate(r.started_at));
    if (!b) continue;
    if (r.status === "success") b.success++;
    else if (r.status === "failed") b.failed++;
    else b.cancelled++;
    if (r.currency === primary && r.cost_amount > 0) b.cost_by_media_type[r.media_type] += r.cost_amount;
  }
  const toRows = (m: Map<string, Stats>, mk: (k: string) => Partial<BreakdownRow>): BreakdownRow[] =>
    [...m.entries()]
      .map(([k, s]) => ({ ...mk(k), ...finishStat(s) }))
      .sort((a, b) => b.calls - a.calls);
  // 需要关注 a：失败率偏高（供应商优先，命中则不再报其下模型）
  const overallFail = kpi.success + kpi.failed ? kpi.failed / (kpi.success + kpi.failed) : 0;
  const attention: AttentionItem[] = [];
  const flaggedProviders = new Set<string>();
  const failItem = (provider: string, model: string | null, s: Stats): AttentionItem | null => {
    const n = s.success + s.failed;
    if (s.failed < 2 || n === 0) return null;
    if (wilsonLower(s.failed, n) <= overallFail) return null;
    return { type: "failure_rate", provider, model, success: s.success, failed: s.failed, failure_rate: s.failed / n, overall_failure_rate: overallFail };
  };
  for (const [p, s] of byProvider) {
    const it = failItem(p, null, s);
    if (it) {
      attention.push(it);
      flaggedProviders.add(p);
    }
  }
  for (const [k, s] of byModel) {
    const [p, m] = k.split(" ");
    if (flaggedProviders.has(p)) continue;
    const it = failItem(p, m, s);
    if (it) attention.push(it);
  }
  // 需要关注 d：同一目标末尾连续失败 ≥ 2
  const byTarget = new Map<string, UsageRecord[]>();
  for (const r of rows) {
    if (!r.segment_id) continue;
    const key = `${r.project_name}|${r.media_type}|${r.segment_id}`;
    let xs = byTarget.get(key);
    if (!xs) byTarget.set(key, (xs = []));
    xs.push(r);
  }
  for (const [, xs] of byTarget) {
    // rows 已按时间倒序：从最新往回数 failed，cancelled 跳过，遇 success 停止
    const run: UsageRecord[] = [];
    for (const r of xs) {
      if (r.status === "cancelled") continue;
      if (r.status !== "failed") break;
      run.push(r);
    }
    if (run.length >= 2) {
      const last = run[0];
      attention.push({
        type: "consecutive_failures",
        project_name: last.project_name,
        media_type: last.media_type,
        segment_id: last.segment_id!,
        count: run.length,
        first_failed_at: run[run.length - 1].started_at,
        last_failed_at: last.started_at,
        last_error_code: last.error_code,
      });
    }
  }
  attention.sort((a, b) => (b.type === "failure_rate" ? b.failed : b.count) - (a.type === "failure_rate" ? a.failed : a.count));
  return {
    range: since ? { since: localDate(since.toISOString()), until: localDate(NOW.toISOString()) } : null,
    primary_currency: primary,
    kpi,
    daily: [...daily.values()],
    breakdown: {
      project: toRows(byProject, (k) => ({ project_name: k })),
      provider: toRows(byProvider, (k) => ({ provider: k })),
      model: toRows(byModel, (k) => {
        const [provider, model] = k.split(" ");
        return { provider, model };
      }),
    },
    attention: attention.slice(0, 20),
  };
}

export const FILTER_OPTIONS = {
  projects: PROJECTS.slice(0, 3) as string[],
  providers: Object.keys(PROVIDER_LABELS),
  models: (Object.keys(MODELS) as MediaType[]).flatMap((m) => MODELS[m].map((x) => ({ provider: x.provider, model: x.model }))),
};

// ---------------------------------------------------------------------------
// 格式化
// ---------------------------------------------------------------------------

const CURRENCY_SYMBOL: Record<string, string> = { USD: "$", CNY: "¥" };

export function money(currency: string, amount: number, digits = 2): string {
  const sym = CURRENCY_SYMBOL[currency] ?? `${currency} `;
  return `${sym}${amount.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function moneyAll(cost: Record<string, number>, primary: string | null): { main: string; others: string[] } {
  const entries = Object.entries(cost).filter(([, v]) => v > 0);
  if (entries.length === 0) return { main: "—", others: [] };
  const main = entries.find(([c]) => c === primary) ?? entries[0];
  return { main: money(main[0], main[1]), others: entries.filter((e) => e !== main).map(([c, v]) => money(c, v)) };
}

export function pct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(v >= 0.995 || v === 0 ? 0 : 1)}%`;
}

export function durationLabel(ms: number | null): string {
  if (ms === null) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${String(s % 60).padStart(2, "0")}s`;
}

export function shortTime(iso: string): string {
  const d = new Date(iso);
  const sameDay = d.toDateString() === NOW.toDateString();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return sameDay ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

export function providerLabel(id: string): string {
  return PROVIDER_LABELS[id] ?? id;
}

export function projectLabel(name: string): string {
  return name === "" ? "未命名（端点试跑）" : name;
}
