import { AlertOctagon, Repeat2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { UsageAttention, UsageSummary } from "@/types";
import type { UsageRecordsFilters } from "@/stores/usage-records-store";
import { formatShortDateTime } from "@/utils/date-format";
import { episodeItemRefLabel } from "@/utils/episode-display";
import {
  MEDIA_META,
  formatRatio,
  projectTitleResolver,
  providerLabelResolver,
  usageProjectLabel,
} from "./usage-record-format";

interface UsageAttentionCardProps {
  summary: UsageSummary;
  onChange: (patch: Partial<UsageRecordsFilters>) => void;
}

/**
 * 需要关注只在真有异常时渲染，由调用方按 `summary.attention` 是否为空决定；
 * 空卡片比没有卡片更占地方也更没信息。
 */
export function UsageAttentionCard({ summary, onChange }: UsageAttentionCardProps) {
  const { t } = useTranslation("dashboard");
  const providerLabel = providerLabelResolver(summary);
  const titleOf = projectTitleResolver(summary);

  return (
    <section
      aria-label={t("usage_attention_title")}
      className="flex min-w-0 flex-col gap-3 rounded-xl border border-destructive/30 bg-card p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{t("usage_attention_title")}</h3>
        <span className="num text-xs text-muted-foreground">{summary.attention.length}</span>
      </div>
      <ul className="flex flex-col gap-2">
        {summary.attention.map((item) => (
          <li key={attentionKey(item)}>
            <AttentionItem item={item} providerLabel={providerLabel} titleOf={titleOf} onChange={onChange} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function attentionKey(item: UsageAttention): string {
  return item.type === "failure_rate"
    ? `rate:${item.provider}/${item.model ?? ""}`
    : `seq:${item.project_name}/${item.media_type}/${item.segment_id}`;
}

function AttentionItem({
  item,
  providerLabel,
  titleOf,
  onChange,
}: {
  item: UsageAttention;
  providerLabel: (provider: string | null) => string;
  titleOf: (name: string) => string | null;
  onChange: (patch: Partial<UsageRecordsFilters>) => void;
}) {
  const { t, i18n } = useTranslation("dashboard");
  const isRate = item.type === "failure_rate";
  const Icon = isRate ? AlertOctagon : Repeat2;

  const title = isRate
    ? t("usage_attention_failure_rate_title", {
        name: item.model
          ? `${providerLabel(item.provider)} · ${item.model}`
          : providerLabel(item.provider),
      })
    : t("usage_attention_consecutive_title", {
        project: usageProjectLabel(item.project_name, t, i18n.language, titleOf(item.project_name)),
        segment: episodeItemRefLabel(item.segment_id, item.segment_ref, t),
      });

  const detail = isRate
    ? t("usage_attention_failure_rate_detail", {
        failed: item.failed,
        total: item.success + item.failed,
        rate: formatRatio(item.failure_rate, i18n.language),
        overall: formatRatio(item.overall_failure_rate, i18n.language),
      })
    : t("usage_attention_consecutive_detail", {
        count: item.count,
        media: t(MEDIA_META[item.media_type].labelKey),
        time: formatShortDateTime(item.last_failed_at) ?? "—",
      });

  // 失败率偏高把状态一并切到失败，用户点进去看到的就是那些失败行；连续失败只写目标三维，
  // 该分镜的成功与失败要连起来看才知道问题是不是还在。
  const patch: Partial<UsageRecordsFilters> = isRate
    ? { provider: item.provider, model: item.model, status: "failed" }
    : {
        project: item.project_name,
        mediaType: item.media_type,
        segment: item.segment_id,
      };

  return (
    <button
      type="button"
      onClick={() => onChange(patch)}
      className="group flex w-full items-start gap-2.5 rounded-lg border border-border bg-background/40 px-3 py-2.5 text-left transition-colors outline-none hover:border-destructive/40 focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <Icon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-destructive" />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-sm text-foreground">{title}</span>
        <span className="text-xs text-muted-foreground">{detail}</span>
      </span>
      <span className="shrink-0 pt-0.5 text-xs text-muted-foreground transition-colors group-hover:text-primary">
        {t(isRate ? "usage_attention_failure_rate_action" : "usage_attention_consecutive_action")}
      </span>
    </button>
  );
}
