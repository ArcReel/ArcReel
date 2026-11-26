import { AudioLines, FileText, Image, Video } from "lucide-react";

import type { TFunction } from "i18next";

import type { CallType, EpisodeItemRef, UsageRecordStatus, UsageSummary } from "@/types";
import { parseIsoTimestamp } from "@/utils/date-format";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { episodeItemKind, isPositionedItemId, type EpisodeItemKind } from "@/utils/episode-item";
import { formatElapsedMs } from "@/utils/task-elapsed";
import type { ElapsedTranslate } from "@/utils/task-elapsed";

export const MEDIA_META: Record<
  CallType,
  { Icon: typeof Image; iconClass: string; swatchClass: string; color: string; labelKey: string }
> = {
  image: { Icon: Image, iconClass: "text-media-image", swatchClass: "bg-media-image", color: "var(--media-image)", labelKey: "usage_media_image" },
  video: { Icon: Video, iconClass: "text-media-video", swatchClass: "bg-media-video", color: "var(--media-video)", labelKey: "usage_media_video" },
  text: { Icon: FileText, iconClass: "text-media-text", swatchClass: "bg-media-text", color: "var(--media-text)", labelKey: "usage_media_text" },
  audio: { Icon: AudioLines, iconClass: "text-media-audio", swatchClass: "bg-media-audio", color: "var(--media-audio)", labelKey: "usage_media_audio" },
};

export const COMFYUI_EXPECTED_SUFFIXES: Readonly<Record<string, string>> = {
  image: ".jpeg / .jpg / .png / .webp",
  video: ".m4v / .mov / .mp4",
};

const DASH = "—";

export const STATUS_LABEL_KEYS: Record<UsageRecordStatus, string> = {
  pending: "usage_status_pending",
  success: "usage_status_success",
  failed: "usage_status_failed",
  cancelled: "usage_status_cancelled",
};

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

const FAILURE_PHRASE_KEYS: Record<string, string> = {
  rate_limited: "usage_error_rate_limited",
  content_policy: "usage_error_content_policy",
  timeout: "usage_error_timeout",
  text_wait_timeout: "usage_error_wait_timeout",
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

export interface TargetItem {
  kind: EpisodeItemKind;
  position: number | null;
  excerpt: string;
  thumbnail: string | null;
}

export interface TargetParts {
  item: TargetItem | null;
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
    const positioned = isPositionedItemId(segmentId);
    if (segmentRef) {
      const name =
        segmentRef.episode_title.trim() ||
        t("common:episode_position_name", { position: segmentRef.episode_position });
      if (!positioned) return { item: null, prefix: segmentRef.item_id, name };
      const item = {
        kind: episodeItemKind(segmentId),
        position: segmentRef.item_position ?? null,
        excerpt: segmentRef.item_excerpt ?? "",
        thumbnail: segmentRef.item_thumbnail ?? null,
      };
      return { item, prefix: null, name };
    }
    const itemId = itemIdWithinEpisode(segmentId);
    if (itemId === segmentId) return { item: null, prefix: segmentId, name: "" };
    const name = t("common:episode_unlisted_name");
    if (!positioned) return { item: null, prefix: itemId, name };
    return { item: { kind: episodeItemKind(segmentId), position: null, excerpt: "", thumbnail: null }, prefix: null, name };
  }
  const key = purposeKey(purpose);
  return { item: null, prefix: null, name: key ? t(`dashboard:${key}`) : DASH };
}

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

const UNPRICED_REASON_KEYS: Record<string, string> = {
  no_entry: "usage_unpriced_reason_no_entry",
  no_price_table: "usage_unpriced_reason_no_price_table",
  missing_rate: "usage_unpriced_reason_missing_rate",
  unrecognized_terms: "usage_unpriced_reason_unrecognized_terms",
  no_usage_estimate: "usage_unpriced_reason_no_usage_estimate",
};

export function unpricedReasonKey(reason: string | null): string {
  return (reason && UNPRICED_REASON_KEYS[reason]) || "usage_unpriced_reason_unknown";
}

export function projectTitleResolver(summary: UsageSummary | null): (name: string) => string | null {
  const titles = summary?.filter_options.project_titles ?? {};
  return (name) => titles[name] ?? null;
}

const INTL_LOCALES: Record<string, string> = {
  zh: "zh-CN",
  en: "en-US",
  vi: "vi-VN",
  ru: "ru-RU",
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

export function formatRatio(rate: number | null, language: string): string {
  return rate === null ? DASH : percentFormatter(language).format(rate);
}

const countFormatters = new Map<string, Intl.NumberFormat>();

export function formatCount(value: number, language: string): string {
  const locale = intlLocale(language);
  let formatter = countFormatters.get(locale);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale);
    countFormatters.set(locale, formatter);
  }
  return formatter.format(value);
}

const compactFormatters = new Map<string, Intl.NumberFormat>();

function trimmedFixed(value: number): string {
  return String(Number(value.toFixed(1)));
}

export function formatTokenAmount(value: number, language: string, t: TFunction): string {
  if (language.split("-")[0] === "zh") {
    if (value >= 100_000_000) return t("usage_tokens_amount_100m", { value: trimmedFixed(value / 100_000_000) });
    if (value >= 10_000) return t("usage_tokens_amount_10k", { value: trimmedFixed(value / 10_000) });
    return t("usage_tokens_amount", { value: formatCount(value, language) });
  }
  const locale = intlLocale(language);
  let formatter = compactFormatters.get(locale);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 });
    compactFormatters.set(locale, formatter);
  }
  return t("usage_tokens_amount", { value: formatter.format(value) });
}

const dayFormatters = new Map<string, Intl.DateTimeFormat>();

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

const DELETED_PROJECT = /^(.+)#deleted-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/;

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

export function formatDurationMs(
  durationMs: number | null,
  t: ElapsedTranslate,
): string {
  if (durationMs === null || durationMs < 0) return DASH;
  return formatElapsedMs(durationMs, t);
}

export function elapsedSince(startedAt: string, now: number, t: ElapsedTranslate): string {
  const start = parseIsoTimestamp(startedAt).getTime();
  if (Number.isNaN(start)) return DASH;
  return formatDurationMs(Math.max(0, now - start), t);
}
