import { AlertOctagon, Repeat2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { UsageAttention, UsageSummary } from "@/types";
import type { UsageRecordsFilters } from "@/stores/usage-records-store";
import { formatShortDateTime } from "@/utils/date-format";
import {
  MEDIA_META,
  formatRatio,
  providerLabelResolver,
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

  return (
    <section
      className="col-span-12 rounded-[10px] border border-danger/20 p-4 lg:col-span-5"
      style={CARD_STYLE}
    >
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <h4 className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          {t("usage_attention_title")}
        </h4>
        <span className="num text-[11px] text-text-4">{summary.attention.length}</span>
      </div>
      <ul className="flex flex-col gap-2">
        {summary.attention.map((item) => (
          <li key={attentionKey(item)}>
            <AttentionItem item={item} providerLabel={providerLabel} onChange={onChange} />
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
  onChange,
}: {
  item: UsageAttention;
  providerLabel: (provider: string | null) => string;
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
        project: item.project_name || t("usage_project_untitled"),
        segment: item.segment_id,
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
      className="focus-ring group flex w-full items-start gap-2.5 rounded-[8px] border border-hairline bg-bg-grad-b/40 px-3 py-2.5 text-left transition-colors hover:border-danger/40"
    >
      <Icon aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
      <span className="min-w-0 flex-1">
        <span className="block text-[12.5px] text-text">{title}</span>
        <span className="mt-0.5 block text-[11.5px] text-text-3">{detail}</span>
      </span>
      <span className="shrink-0 pt-0.5 text-[11px] text-text-4 transition-colors group-hover:text-accent-2">
        {t(isRate ? "usage_attention_failure_rate_action" : "usage_attention_consecutive_action")}
      </span>
    </button>
  );
}
