import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useNowTick } from "@/hooks/useNowTick";
import { formatCurrencyAmount } from "@/utils/cost-format";
import { formatShortDateTime } from "@/utils/date-format";
import {
  MEDIA_META,
  STATUS_COLORS,
  STATUS_LABEL_KEYS,
  elapsedSince,
  failurePhraseKey,
  formatDurationMs,
  purposeKey,
  truncateReason,
} from "./usage-record-format";
import type { UsageRecordView } from "./usage-record-view";

export interface RecordRowProps {
  record: UsageRecordView;
  /** `table` 是设置页记录表的一行；`compact` 供顶栏悬浮层复用，两行排布且不显示项目。 */
  layout: "table" | "compact";
  /** 供应商 id → 显示名，查不到回退 id。 */
  providerLabel: (provider: string | null) => string;
  /** 筛选已固定某个项目时隐藏项目列，避免整列重复同一个值。 */
  showProject?: boolean;
  onOpenDetail?: (recordId: number) => void;
  /** 行尾的额外动作（取消、重试下载），取代默认的「详情」。 */
  trailing?: ReactNode;
}

const CELL_CLS = "px-2 py-1.5 align-middle text-[11.5px] text-text-2";

/** 目标列：有分镜显示分镜，否则显示来源；两者都没有显示破折号。 */
function useTargetLabel(record: UsageRecordView): string {
  const { t } = useTranslation("dashboard");
  if (record.segmentId) return t("usage_target_segment", { id: record.segmentId });
  const key = purposeKey(record.purpose);
  if (key) return t(key);
  return "—";
}

function StatusCell({
  record,
  reason,
}: {
  record: UsageRecordView;
  reason: string | null;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden="true"
        className="h-[5px] w-[5px] shrink-0 rounded-full"
        style={{ background: STATUS_COLORS[record.status] }}
      />
      <span style={{ color: STATUS_COLORS[record.status] }}>
        {t(STATUS_LABEL_KEYS[record.status])}
      </span>
      {reason && <span className="text-text-3">{reason}</span>}
    </span>
  );
}

/** 进行中行订阅秒级时钟；已定格的耗时是静态文本，不为它每秒重渲染。 */
function ElapsedCell({ record }: { record: UsageRecordView }) {
  const now = useNowTick();
  return <>{elapsedSince(record.startedAt, now)}</>;
}

function CostCell({ record }: { record: UsageRecordView }) {
  if (record.status === "pending" || record.costAmount <= 0) return <>—</>;
  return (
    <>{formatCurrencyAmount(record.currency, record.costAmount, { maximumFractionDigits: 4 })}</>
  );
}

export function RecordRow({
  record,
  layout,
  providerLabel,
  showProject = true,
  onOpenDetail,
  trailing,
}: RecordRowProps) {
  const { t } = useTranslation("dashboard");
  const target = useTargetLabel(record);
  const media = MEDIA_META[record.mediaType];
  const MediaIcon = media.Icon;
  const model = record.model ?? t("usage_model_unresolved");
  const projectLabel = record.projectName || t("usage_project_untitled");
  const phraseKey = failurePhraseKey(record.errorCode);
  const failureReason =
    record.status === "failed"
      ? phraseKey
        ? t(phraseKey)
        : record.errorMessage
          ? truncateReason(record.errorMessage)
          : null
      : null;
  const detailButton =
    trailing ??
    (record.recordId !== null && onOpenDetail ? (
      <button
        type="button"
        onClick={() => onOpenDetail(record.recordId as number)}
        className="focus-ring rounded px-1 text-[11px] text-text-3 transition-colors hover:text-accent-2"
      >
        {t("usage_row_detail")}
      </button>
    ) : null);

  if (layout === "compact") {
    const body = (
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[11.5px] text-text-2">
          <span className="truncate">{target}</span>
          {!trailing && failureReason && (
            <span className="shrink-0 text-text-3">{failureReason}</span>
          )}
          <span className="num ml-auto shrink-0 text-[11px] text-text-3">
            {record.status === "pending" ? (
              <ElapsedCell record={record} />
            ) : (
              formatDurationMs(record.durationMs)
            )}
          </span>
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[10.5px] text-text-3">
          <span className="truncate">
            {providerLabel(record.provider)} · {model}
          </span>
          <span className="ml-auto shrink-0">
            <StatusCell record={record} reason={null} />
          </span>
        </div>
      </div>
    );
    const icon = (
      <MediaIcon
        aria-label={t(media.labelKey)}
        className="mt-[3px] h-3.5 w-3.5 shrink-0"
        style={{ color: media.color }}
      />
    );
    return (
      <div className="flex items-start gap-1">
        {record.recordId !== null && onOpenDetail ? (
          <button
            type="button"
            onClick={() => onOpenDetail(record.recordId as number)}
            className="focus-ring flex min-w-0 flex-1 items-start gap-2 rounded px-2 py-1.5 text-left transition-colors hover:bg-[oklch(1_0_0_/_0.03)]"
          >
            {icon}
            {body}
          </button>
        ) : (
          <div className="flex min-w-0 flex-1 items-start gap-2 px-2 py-1.5">
            {icon}
            {body}
          </div>
        )}
        {trailing && <div className="shrink-0 py-1.5 pr-2">{trailing}</div>}
      </div>
    );
  }

  return (
    <tr className="border-t border-hairline-soft transition-colors hover:bg-[oklch(1_0_0_/_0.02)]">
      <td className={CELL_CLS}>
        <MediaIcon
          aria-label={t(media.labelKey)}
          className="h-3.5 w-3.5"
          style={{ color: media.color }}
        />
      </td>
      {showProject && (
        <td className={`${CELL_CLS} max-w-[9rem] truncate`}>{projectLabel}</td>
      )}
      <td className={`${CELL_CLS} max-w-[10rem] truncate text-text`}>{target}</td>
      <td className={`${CELL_CLS} max-w-[8rem] truncate`}>
        {providerLabel(record.provider)}
      </td>
      <td className={`${CELL_CLS} max-w-[10rem] truncate font-mono text-[11px]`}>
        {model}
      </td>
      <td className={CELL_CLS}>
        <StatusCell record={record} reason={failureReason} />
      </td>
      <td className={`${CELL_CLS} num whitespace-nowrap text-text-3`}>
        {record.status === "pending" ? (
          <ElapsedCell record={record} />
        ) : (
          formatDurationMs(record.durationMs)
        )}
      </td>
      <td className={`${CELL_CLS} num whitespace-nowrap text-text-3`}>
        {formatShortDateTime(record.startedAt) ?? "—"}
      </td>
      <td className={`${CELL_CLS} num whitespace-nowrap`}>
        <CostCell record={record} />
      </td>
      <td className={`${CELL_CLS} whitespace-nowrap text-right`}>{detailButton}</td>
    </tr>
  );
}
