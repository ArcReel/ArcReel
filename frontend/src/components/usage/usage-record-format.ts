import { AudioLines, FileText, Image, Video } from "lucide-react";

import type { TFunction } from "i18next";

import type { CallType, EpisodeItemRef, UsageRecordStatus, UsageSummary } from "@/types";
import { parseIsoTimestamp } from "@/utils/date-format";
import { itemIdWithinEpisode } from "@/utils/episode-display";
import { episodeItemKind, isPositionedItemId, type EpisodeItemKind } from "@/utils/episode-item";
import { formatElapsedMs } from "@/utils/task-elapsed";
import type { ElapsedTranslate } from "@/utils/task-elapsed";

/** Media glyphs/tones: `iconClass` colors icons, `swatchClass` swatches, `color` chart fills; all share `--media-*` tokens. */
export const MEDIA_META: Record<
  CallType,
  { Icon: typeof Image; iconClass: string; swatchClass: string; color: string; labelKey: string }
> = {
  image: { Icon: Image, iconClass: "text-media-image", swatchClass: "bg-media-image", color: "var(--media-image)", labelKey: "usage_media_image" },
  video: { Icon: Video, iconClass: "text-media-video", swatchClass: "bg-media-video", color: "var(--media-video)", labelKey: "usage_media_video" },
  text: { Icon: FileText, iconClass: "text-media-text", swatchClass: "bg-media-text", color: "var(--media-text)", labelKey: "usage_media_text" },
  audio: { Icon: AudioLines, iconClass: "text-media-audio", swatchClass: "bg-media-audio", color: "var(--media-audio)", labelKey: "usage_media_audio" },
};

/** Missing-value placeholders. */
const DASH = "—";

export const STATUS_LABEL_KEYS: Record<UsageRecordStatus, string> = {
  pending: "usage_status_pending",
  success: "usage_status_success",
  failed: "usage_status_failed",
  cancelled: "usage_status_cancelled",
};

/** Status wording/status-dot colors. */
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

/** Translated failure phrases; lists show phrases only and details raw errors. */
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
 * Two-part targets: storyboard numbers (episode-local IDs) as untruncated prefixes, then episode titles truncated if necessary.
 * Calls without storyboards show uses with empty prefixes.
 */
export interface TargetItem {
  kind: EpisodeItemKind;
  /** Current episode positions; null when no longer scripted. */
  position: number | null;
  excerpt: string;
  thumbnail: string | null;
}

export interface TargetParts {
  /** Storyboards/video units identified by positions, thumbnails and body excerpts. */
  item: TargetItem | null;
  /** Episode-local IDs of non-positional entries (grids). */
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
    // Episode prefixes without references mean episodes left ledgers; show Untitled episode and Deleted because entries left with episodes.
    const name = t("common:episode_unlisted_name");
    if (!positioned) return { item: null, prefix: itemId, name };
    return { item: { kind: episodeItemKind(segmentId), position: null, excerpt: "", thumbnail: null }, prefix: null, name };
  }
  const key = purposeKey(purpose);
  return { item: null, prefix: null, name: key ? t(`dashboard:${key}`) : DASH };
}

/** Prefer provider filter-candidate labels; unresolved providers fall back to IDs. */
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

/** Unpriced reason codes → copy keys; backend `UnpricedReason` codes unknown here use generic explanations. */
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

/** Summaries contain project names only; look up titles in `filter_options.project_titles`. */
export function projectTitleResolver(summary: UsageSummary | null): (name: string) => string | null {
  const titles = summary?.filter_options.project_titles ?? {};
  return (name) => titles[name] ?? null;
}

/** i18n language codes → Intl locales; names differ, requiring explicit maps; unknown languages fall back to English. */
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

/**
 * Shared success/failure percentage formatting across KPI bars/breakdowns/trend tooltips/popovers:
 * repeated same-page values must format identically. Zero denominators yield backend null, displayed as dashes.
 */
export function formatRatio(rate: number | null, language: string): string {
  return rate === null ? DASH : percentFormatter(language).format(rate);
}

const countFormatters = new Map<string, Intl.NumberFormat>();

/**
 * Count grouping follows UI languages. Settings/popover KPIs share `intlLocale` with `formatRatio`:
 * call counts/success rates within rows must use consistent separators, while `toLocaleString()` follows
 * browser languages.
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

const compactFormatters = new Map<string, Intl.NumberFormat>();

/** Fixed decimals trimmed of trailing zeros: 12.0 → 12, 12.34 → 12.3. */
function trimmedFixed(value: number): string {
  return String(Number(value.toFixed(1)));
}

/**
 * Compact token counts include units in values (e.g. 12.3 万 token). Chinese increments by 万/亿,
 * other languages use Intl compact notation (12.3K tokens); below ten thousand/thousand, use plain grouped counts.
 */
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

/**
 * Render calendar `YYYY-MM-DD` dates in UI languages. These are backend-computed local days, not instants, so construct
 * local-timezone Dates; `new Date(string)` would interpret UTC midnight, shifting days across hemispheres.
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
 * Deleted-project records reattach to tombstone names `<项目名>#deleted-<UTC 时刻>` (see `lib/db/repositories/project_records.py`),
 * separate from new same-named projects. UI shows original names/deletion dates; filters retain raw tombstone names.
 */
const DELETED_PROJECT = /^(.+)#deleted-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/;

/**
 * Usage-page project names: show titles when available, otherwise names (deleted/unreadable/untitled projects);
 * deleted projects show original names/deletion dates; projectless endpoint trials show Untitled.
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

/** Duration columns show dashes without available durations; copy shares task-readout `formatElapsedMs`. */
export function formatDurationMs(
  durationMs: number | null,
  t: ElapsedTranslate,
): string {
  if (durationMs === null || durationMs < 0) return DASH;
  return formatElapsedMs(durationMs, t);
}

/** Live running-row durations, starting at ISO instants. */
export function elapsedSince(startedAt: string, now: number, t: ElapsedTranslate): string {
  const start = parseIsoTimestamp(startedAt).getTime();
  if (Number.isNaN(start)) return DASH;
  return formatDurationMs(Math.max(0, now - start), t);
}
