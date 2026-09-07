import { AudioLines, FileText, Image, Video } from "lucide-react";

import type { CallType, UsageRecordStatus, UsageSummary } from "@/types";
import { parseIsoTimestamp } from "@/utils/date-format";

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
  return (provider) => (provider ? (labels.get(provider) ?? provider) : "—");
}

/** 耗时列：秒以内取整秒，超过一分钟拆成 `Xm YYs`。 */
export function formatDurationMs(durationMs: number | null): string {
  if (durationMs === null || durationMs < 0) return "—";
  const totalSeconds = Math.round(durationMs / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

/** 成功率与失败率共用的百分比渲染；分母为 0 时后端给 null，显示破折号。 */
export function formatRatio(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 1000) / 10}%`;
}

/** 进行中行的实时耗时，起点为 ISO 时刻。 */
export function elapsedSince(startedAt: string, now: number): string {
  const start = parseIsoTimestamp(startedAt).getTime();
  if (Number.isNaN(start)) return "—";
  return formatDurationMs(Math.max(0, now - start));
}
