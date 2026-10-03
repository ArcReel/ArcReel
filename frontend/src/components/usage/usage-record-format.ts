import { AudioLines, FileText, Image, Video } from "lucide-react";

import type { TFunction } from "i18next";

import type { CallType, EpisodeItemRef, UsageRecordStatus, UsageSummary } from "@/types";
import { parseIsoTimestamp } from "@/utils/date-format";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { formatElapsedMs } from "@/utils/task-elapsed";
import type { ElapsedTranslate } from "@/utils/task-elapsed";

/** 媒体类型的字形与色调：`iconClass` 给图标着色，`swatchClass` 给色块，`color` 供图表填充，三者是同一个 `--media-*` token。 */
export const MEDIA_META: Record<
  CallType,
  { Icon: typeof Image; iconClass: string; swatchClass: string; color: string; labelKey: string }
> = {
  image: { Icon: Image, iconClass: "text-media-image", swatchClass: "bg-media-image", color: "var(--media-image)", labelKey: "usage_media_image" },
  video: { Icon: Video, iconClass: "text-media-video", swatchClass: "bg-media-video", color: "var(--media-video)", labelKey: "usage_media_video" },
  text: { Icon: FileText, iconClass: "text-media-text", swatchClass: "bg-media-text", color: "var(--media-text)", labelKey: "usage_media_text" },
  audio: { Icon: AudioLines, iconClass: "text-media-audio", swatchClass: "bg-media-audio", color: "var(--media-audio)", labelKey: "usage_media_audio" },
};

/** 无值时的占位。 */
const DASH = "—";

export const STATUS_LABEL_KEYS: Record<UsageRecordStatus, string> = {
  pending: "usage_status_pending",
  success: "usage_status_success",
  failed: "usage_status_failed",
  cancelled: "usage_status_cancelled",
};

/** 状态文字与状态点的颜色。 */
export const STATUS_TEXT_CLASSES: Record<UsageRecordStatus, string> = {
  pending: "text-primary",
  success: "text-good",
  failed: "text-destructive",
  cancelled: "text-muted-foreground",
};

export const STATUS_DOT_CLASSES: Record<UsageRecordStatus, string> = {
  pending: "bg-primary",
  success: "bg-good",
  failed: "bg-destructive",
  cancelled: "bg-muted-foreground",
};

/** 有翻译的失败短语。列表只显示短语，原始报错只在详情里显示。 */
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

/**
 * 目标列拆成两段：分镜号（集内 ID）作为不截断的前缀，集名在后、放不下时截断。
 * 没有分镜的调用按用途显示，前缀为空。
 */
export interface TargetParts {
  prefix: string | null;
  name: string;
}

export function targetParts(
  segmentId: string | null,
  segmentRef: EpisodeItemRef | null,
  purpose: string | null,
  t: TFunction,
): TargetParts {
  if (segmentId) {
    if (segmentRef) {
      const name =
        segmentRef.episode_title.trim() ||
        t("common:episode_position_name", { position: segmentRef.episode_position });
      return { prefix: segmentRef.item_id, name };
    }
    const itemId = itemIdWithinEpisode(segmentId);
    // 带集前缀却没有指称：集已移出账本，集名用「未命名集」，仍不显示集 ID。
    return itemId === segmentId
      ? { prefix: segmentId, name: "" }
      : { prefix: itemId, name: t("common:episode_unlisted_name") };
  }
  const key = purposeKey(purpose);
  return { prefix: null, name: key ? t(`dashboard:${key}`) : DASH };
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

/** 汇总里只有项目名，标题从 `filter_options.project_titles` 查。 */
export function projectTitleResolver(summary: UsageSummary | null): (name: string) => string | null {
  const titles = summary?.filter_options.project_titles ?? {};
  return (name) => titles[name] ?? null;
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

const countFormatters = new Map<string, Intl.NumberFormat>();

/**
 * 计数的千分位按界面语言渲染。设置页 KPI 条与悬浮层 KPI 行共用，与 `formatRatio` 同走
 * `intlLocale`：一行里的调用次数与成功率必须是一套分隔习惯，`toLocaleString()` 跟的是
 * 浏览器语言。
 */
export function formatCount(value: number, language: string): string {
  const locale = intlLocale(language);
  let formatter = countFormatters.get(locale);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale);
    countFormatters.set(locale, formatter);
  }
  return formatter.format(value);
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

/**
 * 已删除项目的记录改挂到墓碑名 `<项目名>#deleted-<UTC 时刻>`（见 `lib/db/repositories/project_records.py`），
 * 与同名新项目分开。界面显示原名与删除日期，筛选仍用墓碑名原值。
 */
const DELETED_PROJECT = /^(.+)#deleted-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/;

/**
 * 用量页的项目显示名：有标题显示标题；没有标题（项目已删除、读不到或未设标题）时回退到项目名，
 * 已删除的项目显示原名与删除日期；端点试跑的记录没有项目，给「未命名」。
 */
export function usageProjectLabel(
  name: string,
  t: (key: string, params?: Record<string, string>) => string,
  language: string,
  title?: string | null,
): string {
  if (title) return title;
  if (!name) return t("usage_project_untitled");
  const match = DELETED_PROJECT.exec(name);
  if (!match) return name;
  const [, project, ...parts] = match;
  const [year, month, day, hour, minute, second] = parts.map(Number);
  const deletedAt = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  const date = new Intl.DateTimeFormat(intlLocale(language), { dateStyle: "medium" }).format(deletedAt);
  return t("usage_project_deleted", { name: project, date });
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
