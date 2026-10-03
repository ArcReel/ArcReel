import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { useNowTick } from "@/hooks/useNowTick";
import { formatCurrencyAmount } from "@/utils/cost-format";
import { formatShortDateTime } from "@/utils/date-format";
import {
  MEDIA_META,
  STATUS_DOT_CLASSES,
  STATUS_LABEL_KEYS,
  STATUS_TEXT_CLASSES,
  elapsedSince,
  failurePhraseKey,
  formatDurationMs,
  targetParts,
} from "./usage-record-format";
import type { UsageRecordView } from "./usage-record-view";

export interface RecordRowProps {
  record: UsageRecordView;
  /** `table` 是设置页记录表的一行；`compact` 供顶栏悬浮层复用，两行排布且不显示项目。 */
  layout: "table" | "compact";
  /** 供应商 id → 显示名，查不到回退 id。 */
  providerLabel: (provider: string | null) => string;
  /** 记录的项目显示名；只有表格布局在显示项目列时用到。 */
  projectLabel?: (record: UsageRecordView) => string;
  /** 筛选已固定某个项目时隐藏项目列，避免整列重复同一个值。 */
  showProject?: boolean;
  onOpenDetail?: (recordId: number) => void;
  /** 行尾的额外动作（取消、重试下载），取代默认的「详情」。 */
  trailing?: ReactNode;
}

/** 目标：分镜号是不截断的前缀，只有后面的集名或用途在放不下时截断。 */
export function TargetLabel({ record, focusable = true }: { record: UsageRecordView; focusable?: boolean }) {
  const { t } = useTranslation("dashboard");
  const { prefix, name } = targetParts(record.segmentId, record.segmentRef, record.purpose, t);
  return (
    <span className="flex min-w-0 items-baseline gap-1.5">
      {prefix && <span className="num shrink-0 text-foreground">{prefix}</span>}
      {name && (
        <TruncatedText
          text={name}
          focusable={focusable}
          className={prefix ? "text-subtle-foreground" : "text-foreground"}
        />
      )}
    </span>
  );
}

/** 状态只放状态与失败短语；没有短语的失败只写「失败」，原始报错在详情里看。 */
function StatusLabel({
  record,
  showPhrase,
  focusable = true,
}: {
  record: UsageRecordView;
  showPhrase: boolean;
  focusable?: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const phraseKey = record.status === "failed" && showPhrase ? failurePhraseKey(record.errorCode) : null;
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <span aria-hidden="true" className={cn("size-1.5 shrink-0 rounded-full", STATUS_DOT_CLASSES[record.status])} />
      <span className={cn("shrink-0 whitespace-nowrap", STATUS_TEXT_CLASSES[record.status])}>
        {t(STATUS_LABEL_KEYS[record.status])}
      </span>
      {phraseKey && <TruncatedText text={t(phraseKey)} focusable={focusable} className="text-muted-foreground" />}
    </span>
  );
}

/** 进行中行订阅秒级时钟；已定格的耗时是静态文本，不为它每秒重渲染。 */
function ElapsedCell({ record }: { record: UsageRecordView }) {
  const { t } = useTranslation("dashboard");
  const now = useNowTick();
  return <>{elapsedSince(record.startedAt, now, t)}</>;
}

function DurationLabel({ record }: { record: UsageRecordView }) {
  const { t } = useTranslation("dashboard");
  return record.status === "pending" ? <ElapsedCell record={record} /> : <>{formatDurationMs(record.durationMs, t)}</>;
}

function costText(record: UsageRecordView): string {
  if (record.status === "pending" || record.costAmount <= 0) return "—";
  return formatCurrencyAmount(record.currency, record.costAmount, { maximumFractionDigits: 4 });
}

function MediaIcon({ record, className }: { record: UsageRecordView; className?: string }) {
  const { t } = useTranslation("dashboard");
  const media = MEDIA_META[record.mediaType];
  return (
    <media.Icon role="img" aria-label={t(media.labelKey)} className={cn("size-3.5 shrink-0", media.iconClass, className)} />
  );
}

export function RecordRow({
  record,
  layout,
  providerLabel,
  projectLabel,
  showProject = true,
  onOpenDetail,
  trailing,
}: RecordRowProps) {
  const { t } = useTranslation("dashboard");
  const model = record.model ?? t("usage_model_unresolved");
  const recordId = record.recordId;
  const canOpen = recordId !== null && onOpenDetail !== undefined;

  if (layout === "compact") {
    // 可打开详情时整行是按钮，行内截断的文字不再单独进入 Tab 顺序。
    const focusable = !canOpen;
    const body = (
      <>
        <MediaIcon record={record} className="mt-0.5" />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex min-w-0 items-center gap-2 text-sm">
            <TargetLabel record={record} focusable={focusable} />
            <span className="num ml-auto shrink-0 text-xs text-muted-foreground">
              <DurationLabel record={record} />
            </span>
          </span>
          <span className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
            <TruncatedText text={`${providerLabel(record.provider)} · ${model}`} focusable={focusable} />
            <span className="ml-auto shrink-0">
              <StatusLabel record={record} showPhrase={!trailing} focusable={focusable} />
            </span>
          </span>
        </span>
      </>
    );
    return (
      <div className="flex items-start gap-1">
        {canOpen ? (
          <button
            type="button"
            onClick={() => onOpenDetail(recordId)}
            className="flex min-w-0 flex-1 items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
          >
            {body}
          </button>
        ) : (
          <div className="flex min-w-0 flex-1 items-start gap-2 px-2 py-1.5">{body}</div>
        )}
        {trailing && <div className="shrink-0 py-1 pr-1">{trailing}</div>}
      </div>
    );
  }

  return (
    <tr className="border-t border-border transition-colors hover:bg-muted/30">
      <td className="py-2 pl-3">
        <MediaIcon record={record} />
      </td>
      {showProject && (
        <td className="px-2 py-2">
          <TruncatedText text={projectLabel?.(record) ?? record.projectName} />
        </td>
      )}
      <td className="px-2 py-2">
        <TargetLabel record={record} />
      </td>
      <td className="px-2 py-2">
        <TruncatedText text={model} className="num text-xs text-subtle-foreground" />
        <TruncatedText text={providerLabel(record.provider)} className="text-xs text-muted-foreground" />
      </td>
      <td className="px-2 py-2">
        <StatusLabel record={record} showPhrase />
      </td>
      <td className="num px-2 py-2 text-right text-xs whitespace-nowrap text-muted-foreground">
        <DurationLabel record={record} />
      </td>
      <td className="num px-2 py-2 text-xs whitespace-nowrap text-muted-foreground">
        {formatShortDateTime(record.startedAt) ?? "—"}
      </td>
      <td className="num px-2 py-2 text-right text-xs whitespace-nowrap">{costText(record)}</td>
      <td className="py-2 pr-3 text-right">
        {trailing ??
          (canOpen && (
            <Button variant="ghost" size="xs" onClick={() => onOpenDetail(recordId)}>
              {t("usage_row_detail")}
            </Button>
          ))}
      </td>
    </tr>
  );
}
