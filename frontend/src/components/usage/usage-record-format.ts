import { AudioLines, FileText, Image, Video } from "lucide-react";

import type { CallType, UsageRecordStatus, UsageSummary } from "@/types";
import { parseIsoTimestamp } from "@/utils/date-format";
import { formatElapsedMs } from "@/utils/task-elapsed";
import type { ElapsedTranslate } from "@/utils/task-elapsed";

/** 媒体类型的字形与色调，与 Darkroom 的媒体色板一致。 */
export const MEDIA_META: Record<
  CallType,
  { Icon: typeof Image; color: string; labelKey: string }
> = {
  image: { Icon: Image, color: "#248fcc", labelKey: "usage_media_image" },
  video: { Icon: Video, color: "#9565c7", labelKey: "usage_media_video" },
  text: { Icon: FileText, color: "#339c6d", labelKey: "usage_media_text" },
  audio: { Icon: AudioLines, color: "#c48225", labelKey: "usage_media_audio" },
};

/** 无值时的占位。 */
const DASH = "—";

export const STATUS_LABEL_KEYS: Record<UsageRecordStatus, string> = {
  pending: "usage_status_pending",
  success: "usage_status_success",
  failed: "usage_status_failed",
  cancelled: "usage_status_cancelled",
};

export const STATUS_COLORS: Record<UsageRecordStatus, string> = {
  pending: "var(--color-accent-2)",
  success: "var(--color-good)",
  failed: "var(--color-danger-2)",
  cancelled: "var(--color-text-4)",
};

/** 有翻译的失败短语；这之外的错误码退回原文。 */
const FAILURE_PHRASE_KEYS: Record<string, string> = {
  rate_limited: "usage_error_rate_limited",
  content_policy: "usage_error_content_policy",
  timeout: "usage_error_timeout",
  download_failed: "usage_error_download_failed",
  interrupted: "usage_error_interrupted",
};

export function failurePhraseKey(errorCode: string | null): string | null {
  if (!errorCode) return null;
  return FAILURE_PHRASE_KEYS[errorCode] ?? null;
}

const PURPOSE_KEYS: Record<string, string> = {
  generation_task: "usage_purpose_generation_task",
  script_generation: "usage_purpose_script_generation",
  episode_planning: "usage_purpose_episode_planning",
  project_overview: "usage_purpose_project_overview",
  style_analysis: "usage_purpose_style_analysis",
  assistant_session: "usage_purpose_assistant_session",
  endpoint_trial: "usage_purpose_endpoint_trial",
};

export function purposeKey(purpose: string | null): string | null {
  if (!purpose) return null;
  return PURPOSE_KEYS[purpose] ?? null;
}

/** 无错误码时表格里只放得下一小段原文，其余截断。 */
export function truncateReason(message: string, max = 40): string {
  const trimmed = message.trim();
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
}

/** 供应商显示名优先取筛选候选值里的 label，查不到回退 id。 */
export function providerLabelResolver(
  summary: UsageSummary | null,
): (provider: string | null) => string {
  const labels = new Map(
    (summary?.filter_options.providers ?? []).map((option) => [
      option.provider,
      option.label,
    ]),
  );
  return (provider) => (provider ? (labels.get(provider) ?? provider) : DASH);
}

/** i18n 语言码 → Intl locale；两者不同名，故显式映射，未知语言回落英文。 */
const INTL_LOCALES: Record<string, string> = {
  zh: "zh-CN",
  en: "en-US",
  vi: "vi-VN",
};

function intlLocale(language: string): string {
  return INTL_LOCALES[language.split("-")[0]] ?? "en-US";
}

const percentFormatters = new Map<string, Intl.NumberFormat>();

function percentFormatter(language: string): Intl.NumberFormat {
  const locale = intlLocale(language);
  let formatter = percentFormatters.get(locale);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, {
      style: "percent",
      maximumFractionDigits: 1,
    });
    percentFormatters.set(locale, formatter);
  }
  return formatter;
}

/**
 * 成功率与失败率共用的百分比渲染，KPI 条、构成表、趋势 tooltip、悬浮层同走这一处：
 * 同一个数在同一页出现多次，格式必须一致。分母为 0 时后端给 null，显示破折号。
 */
export function formatRatio(rate: number | null, language: string): string {
  return rate === null ? DASH : percentFormatter(language).format(rate);
}

const dayFormatters = new Map<string, Intl.DateTimeFormat>();

/**
 * 日历日 `YYYY-MM-DD` 按当前语言渲染。这个日期是后端算好的本地日，不是时刻，故按
 * 本地时区构造 Date——交给 `new Date(string)` 会当成 UTC 午夜，东西半球各挪一天。
 */
export function formatCalendarDay(
  day: string,
  language: string,
  options: Intl.DateTimeFormatOptions,
): string {
  const [year, month, date] = day.split("-").map(Number);
  if (!year || !month || !date) return day;
  const locale = intlLocale(language);
  const cacheKey = `${locale}|${JSON.stringify(options)}`;
  let formatter = dayFormatters.get(cacheKey);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(locale, options);
    dayFormatters.set(cacheKey, formatter);
  }
  return formatter.format(new Date(year, month - 1, date));
}

/** 耗时列；无时长可显示时给破折号。文案与任务读数共用 `formatElapsedMs`。 */
export function formatDurationMs(
  durationMs: number | null,
  t: ElapsedTranslate,
): string {
  if (durationMs === null || durationMs < 0) return DASH;
  return formatElapsedMs(durationMs, t);
}

/** 进行中行的实时耗时，起点为 ISO 时刻。 */
export function elapsedSince(startedAt: string, now: number, t: ElapsedTranslate): string {
  const start = parseIsoTimestamp(startedAt).getTime();
  if (Number.isNaN(start)) return DASH;
  return formatDurationMs(Math.max(0, now - start), t);
}
