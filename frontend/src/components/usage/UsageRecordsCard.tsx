import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { UsageSummary } from "@/types";
import type {
  UsageRecordsFilters,
  UsageStatusFilter,
} from "@/stores/usage-records-store";
import { USAGE_PAGE_SIZE } from "@/stores/usage-records-store";
import { RecordRow } from "./RecordRow";
import { providerLabelResolver } from "./usage-record-format";
import type { UsageRecordView } from "./usage-record-view";

interface UsageRecordsCardProps {
  filters: UsageRecordsFilters;
  summary: UsageSummary | null;
  records: UsageRecordView[];
  inProgress: UsageRecordView[];
  loading: boolean;
  total: number;
  pageIndex: number;
  hasNext: boolean;
  onStatusChange: (status: UsageStatusFilter) => void;
  onPage: (direction: "prev" | "next") => void;
  onOpenDetail: (recordId: number) => void;
  onCancelAll?: () => void;
}

const STATUS_TABS: { value: UsageStatusFilter; labelKey: string }[] = [
  { value: "all", labelKey: "all" },
  { value: "pending", labelKey: "usage_status_pending" },
  { value: "success", labelKey: "usage_status_success" },
  { value: "failed", labelKey: "usage_status_failed" },
  { value: "cancelled", labelKey: "usage_status_cancelled" },
];

const HEAD_CLS =
  "px-2 py-1.5 text-left font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4";

export function UsageRecordsCard({
  filters,
  summary,
  records,
  inProgress,
  loading,
  total,
  pageIndex,
  hasNext,
  onStatusChange,
  onPage,
  onOpenDetail,
  onCancelAll,
}: UsageRecordsCardProps) {
  const { t } = useTranslation("dashboard");
  const providerLabel = providerLabelResolver(summary);
  // 筛选已固定某个项目时隐藏项目列，避免整列重复同一个值。
  const showProject = filters.project === null;
  const columns = showProject ? 10 : 9;
  const from = total === 0 ? 0 : pageIndex * USAGE_PAGE_SIZE + 1;
  const to = pageIndex * USAGE_PAGE_SIZE + records.length;
  const empty = records.length === 0 && inProgress.length === 0 && !loading;
  const showPagination = filters.status !== "pending" && !empty;

  return (
    <section className="rounded-[10px] border border-hairline" style={CARD_STYLE}>
      <header className="flex flex-wrap items-center gap-2 px-4 py-3">
        <h4 className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          {t("usage_records_list")}
        </h4>
        <div
          role="group"
          aria-label={t("usage_status_filter_label")}
          className="ml-auto flex items-center gap-1"
        >
          {STATUS_TABS.map((tab) => {
            const active = filters.status === tab.value;
            return (
              <button
                key={tab.value}
                type="button"
                aria-pressed={active}
                onClick={() => onStatusChange(tab.value)}
                className={
                  "focus-ring rounded-full px-2 py-0.5 text-[11px] transition-colors " +
                  (active
                    ? "bg-accent-dim text-accent-2"
                    : "text-text-3 hover:text-text")
                }
              >
                {t(tab.labelKey)}
              </button>
            );
          })}
        </div>
      </header>

      {empty ? (
        <p className="px-4 pb-5 text-[12px] leading-[1.6] text-text-3">
          {t("usage_records_empty")}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-t border-hairline-soft">
                <th scope="col" className={HEAD_CLS}>
                  {t("usage_col_media_type")}
                </th>
                {showProject && (
                  <th scope="col" className={HEAD_CLS}>
                    {t("usage_col_project")}
                  </th>
                )}
                <th scope="col" className={HEAD_CLS}>
                  {t("usage_col_target")}
                </th>
                <th scope="col" className={HEAD_CLS}>
                  {t("usage_col_provider")}
                </th>
                <th scope="col" className={HEAD_CLS}>
                  {t("usage_col_model")}
                </th>
                <th scope="col" className={HEAD_CLS}>
                  {t("usage_col_status")}
                </th>
                <th scope="col" className={HEAD_CLS}>
                  {t("usage_col_duration")}
                </th>
                <th scope="col" className={HEAD_CLS}>
                  {t("usage_col_time")}
                </th>
                <th scope="col" className={HEAD_CLS}>
                  {t("usage_col_cost")}
                </th>
                <th scope="col" className={`${HEAD_CLS} text-right`}>
                  <span className="sr-only">{t("usage_row_detail")}</span>
                </th>
              </tr>
            </thead>

            {inProgress.length > 0 && (
              <tbody>
                <tr className="border-t border-hairline-soft bg-[oklch(1_0_0_/_0.02)]">
                  <td colSpan={columns} className="px-2 py-1.5">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-accent-2">
                        {t("usage_status_pending")} · {inProgress.length}
                      </span>
                      {onCancelAll && (
                        <button
                          type="button"
                          onClick={onCancelAll}
                          className="focus-ring ml-auto rounded px-1 text-[11px] text-text-3 transition-colors hover:text-danger-2"
                        >
                          {t("usage_cancel_all")}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
                {inProgress.map((record) => (
                  <RecordRow
                    key={record.key}
                    record={record}
                    layout="table"
                    showProject={showProject}
                    providerLabel={providerLabel}
                    onOpenDetail={onOpenDetail}
                  />
                ))}
              </tbody>
            )}

            <tbody>
              {records.map((record) => (
                <RecordRow
                  key={record.key}
                  record={record}
                  layout="table"
                  showProject={showProject}
                  providerLabel={providerLabel}
                  onOpenDetail={onOpenDetail}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showPagination && (
        <footer className="flex items-center justify-between border-t border-hairline-soft px-4 py-2">
          <span className="num text-[10.5px] text-text-4">
            {t("usage_page_position", { from, to, total })}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              aria-label={t("usage_prev_page")}
              disabled={pageIndex === 0}
              onClick={() => onPage("prev")}
              className="focus-ring grid h-6 w-6 place-items-center rounded-md text-text-3 transition-colors hover:text-text disabled:cursor-not-allowed disabled:opacity-30"
            >
              <ChevronLeft aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              aria-label={t("usage_next_page")}
              disabled={!hasNext}
              onClick={() => onPage("next")}
              className="focus-ring grid h-6 w-6 place-items-center rounded-md text-text-3 transition-colors hover:text-text disabled:cursor-not-allowed disabled:opacity-30"
            >
              <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
          </div>
        </footer>
      )}
    </section>
  );
}
